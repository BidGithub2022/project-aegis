import json
import sys
import spacy

nlp = spacy.load("en_core_web_sm")


def analyze_social_engineering(text):
    doc = nlp(text)
    results = {"urgency_score": 0, "authority_markers": [], "action_requests": []}

    for ent in doc.ents:
        if ent.label_ in ("ORG", "PERSON", "GPE"):
            results["authority_markers"].append(ent.text)

    urgency_keywords = {"now", "immediately", "urgent", "minutes", "seconds", "last chance"}
    for token in doc:
        if token.text.lower() in urgency_keywords:
            results["urgency_score"] += 1

    money_triggers = ["transfer", "pay", "send", "wire", "buy", "gift card", "crypto", "zelle"]
    money_verb_lemmas = {"transfer", "pay", "send", "wire", "buy"}
    seen = set()

    for chunk in doc.noun_chunks:
        lower = chunk.text.lower()
        if any(t in lower for t in money_triggers) and lower not in seen:
            seen.add(lower)
            results["action_requests"].append(chunk.text)

    for token in doc:
        if token.pos_ == "VERB" and token.lemma_.lower() in money_verb_lemmas:
            key = token.lemma_.lower()
            if key not in seen:
                seen.add(key)
                results["action_requests"].append(token.text)

    if "gift card" in doc.text.lower() and "gift card" not in seen:
        seen.add("gift card")
        results["action_requests"].append("gift card")

    return results


SCAM_THRESHOLD = 5


def run_evaluation(data_path):
    with open(data_path, "r") as f:
        dataset = json.load(f)

    total_scams = sum(1 for d in dataset if d["label"] == "scam")
    total_safe = sum(1 for d in dataset if d["label"] == "safe")
    caught_scams = 0
    false_positives = 0

    print(f"{'ID':<4} | {'ACTUAL':<8} | {'PRED':<8} | {'RISK':<5} | DETAILS")
    print("-" * 90)

    for item in dataset:
        analysis = analyze_social_engineering(item["text"])

        risk_score = (
            analysis["urgency_score"] * 2
            + len(analysis["authority_markers"])
            + len(analysis["action_requests"]) * 3
        )
        is_scam_predicted = risk_score >= SCAM_THRESHOLD

        if is_scam_predicted and item["label"] == "scam":
            caught_scams += 1
        elif is_scam_predicted and item["label"] == "safe":
            false_positives += 1

        pred_label = "SCAM" if is_scam_predicted else "SAFE"
        details = (
            f"urg={analysis['urgency_score']} "
            f"auth={analysis['authority_markers']} "
            f"act={analysis['action_requests']}"
        )
        print(f"{item['id']:<4} | {item['label'].upper():<8} | {pred_label:<8} | {risk_score:<5} | {details}")

    print("-" * 90)
    print(f"Threshold: risk >= {SCAM_THRESHOLD}")
    print(f"Recall  (scams caught): {caught_scams}/{total_scams}")
    print(f"False alarms (safe → SCAM): {false_positives}/{total_safe}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "dataset.json"
    run_evaluation(path)
