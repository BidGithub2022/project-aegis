import spacy

# Load a pre-trained NLP model
# In a real job, you'd use a large transformer model (like 'en_core_web_trf')
nlp = spacy.load("en_core_web_sm")

def analyze_social_engineering(text):
    doc = nlp(text)
    results = {
        "urgency_score": 0,
        "authority_markers": [],
        "action_requests": []
    }
    
    # 1. Authority Detection (Looking for entities like Organizations or Titles)
    # This addresses the "Identity Claim" phase
    for ent in doc.ents:
        if ent.label_ in ["ORG", "PERSON", "GPE"]:
            results["authority_markers"].append(ent.text)
            
    # 2. Urgency Detection (Looking for 'Time' related patterns)
    urgency_keywords = ["now", "immediately", "urgent", "minutes", "seconds", "last chance"]
    for token in doc:
        if token.text.lower() in urgency_keywords:
            results["urgency_score"] += 1
            
    # 3. Action extraction — noun chunks (e.g. "a wire transfer") plus payment verbs
    # Verbs like "wire" in "You must wire $5,000" are not noun chunks; scan VERB lemmas too.
    money_triggers = ["transfer", "pay", "send", "wire", "buy", "gift card", "crypto"]
    money_verb_lemmas = {"transfer", "pay", "send", "wire", "buy", "crypto"}
    seen = set()

    for chunk in doc.noun_chunks:
        lower = chunk.text.lower()
        if any(t in lower for t in money_triggers):
            if lower not in seen:
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

# --- Test it with a real-world scam scenario ---
scam_message = "This is Agent Miller from the Federal Treasury. Your account has been compromised. You must wire $5,000 via Bitcoin immediately to secure your assets."

analysis = analyze_social_engineering(scam_message)
print(f"Risk Report: {analysis}")