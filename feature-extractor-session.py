import spacy

nlp = spacy.load("en_core_web_sm")

class ScamDetectorSession:
    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self.risk_score = 0
        self.flags = set()
        self.history = []

    def process_new_message(self, text):
        # Use your existing logic here
        analysis = self._analyze_text(text)
        
        # Update the session-wide state
        self.history.append(text)
        self.risk_score += (analysis['urgency_score'] * 2)
        
        if analysis['authority_markers']:
            self.flags.add("AUTHORITY_CLAIMED")
            self.risk_score += 3
            
        if analysis['action_requests']:
            self.flags.add("FINANCIAL_PIVOT")
            self.risk_score += 5

        return {
            "conversation_id": self.conversation_id,
            "cumulative_risk": self.risk_score,
            "active_flags": list(self.flags)
        }

    def _analyze_text(self, text):
        doc = nlp(text)
        results = {"urgency_score": 0, "authority_markers": [], "action_requests": []}

        for ent in doc.ents:
            if ent.label_ in ("ORG", "PERSON", "GPE"):
                results["authority_markers"].append(ent.text)

        urgency_keywords = {"now", "immediately", "urgent", "minutes", "seconds", "last chance"}
        for token in doc:
            if token.text.lower() in urgency_keywords:
                results["urgency_score"] += 1

        money_triggers = ["transfer", "pay", "send", "wire", "buy", "gift card", "crypto"]
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

# --- Simulation ---
user_session = ScamDetectorSession(conversation_id="user_1234")

# Message 1
print(user_session.process_new_message("Hello, this is support from the bank."))
# Message 2 (Later)
print(user_session.process_new_message("We need you to wire the funds immediately."))