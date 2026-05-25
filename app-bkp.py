from typing import List, Optional

import spacy
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI(
    title="Project Aegis: Social Engineering Inference Engine",
    version="1.0.0",
)

nlp = spacy.load("en_core_web_sm")


class MessagePayload(BaseModel):
    text: str


class SessionState(BaseModel):
    conversation_id: str
    risk_score: int
    flags: List[str]


class InferenceRequest(BaseModel):
    current_message: MessagePayload
    session_state: Optional[SessionState] = None


class InferenceResponse(BaseModel):
    conversation_id: str
    updated_risk_score: int
    active_flags: List[str]
    alert_triggered: bool


def analyze_social_engineering(text: str):
    doc = nlp(text)
    results = {
        "urgency_score": 0,
        "authority_markers": [],
        "action_requests": [],
    }

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


@app.post("/v1/analyze", response_model=InferenceResponse, status_code=status.HTTP_200_OK)
async def evaluate_message_stream(payload: InferenceRequest):
    try:
        conv_id = (
            payload.session_state.conversation_id
            if payload.session_state
            else "anonymous_session"
        )
        current_score = payload.session_state.risk_score if payload.session_state else 0
        active_flags = set(payload.session_state.flags) if payload.session_state else set()

        analysis = analyze_social_engineering(payload.current_message.text)

        if analysis["urgency_score"] > 0:
            current_score += analysis["urgency_score"] * 2
            active_flags.add("URGENCY_DETECTED")

        if analysis["authority_markers"]:
            current_score += 3
            active_flags.add("AUTHORITY_CLAIMED")

        if analysis["action_requests"]:
            current_score += 5
            active_flags.add("FINANCIAL_PIVOT")

        alert_triggered = current_score >= 8

        return InferenceResponse(
            conversation_id=conv_id,
            updated_risk_score=current_score,
            active_flags=list(active_flags),
            alert_triggered=alert_triggered,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference Engine Crash: {str(e)}",
        )


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "healthy", "model_loaded": True}
