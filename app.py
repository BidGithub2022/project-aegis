import json
from typing import List, Optional
from pydantic import BaseModel
import spacy
from fastapi import FastAPI, HTTPException, status

# Initialize FastAPI app
app = FastAPI(
    title="Project Aegis: Social Engineering Inference Engine",
    description="Enterprise-grade NLP engine for real-time social engineering and scam detection.",
    version="1.0.0"
)

# Load the industrial-grade NLP model into memory on startup
nlp = spacy.load("en_core_web_sm")


# ==========================================
# 1. CORE DATA SCHEMAS (PYDANTIC)
# ==========================================

# --- Internal Architecture Schemas ---
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


# --- Inbound Evolution API (WhatsApp) Webhook Schemas ---
class EvolutionMessageContent(BaseModel):
    conversation: Optional[str] = None

class EvolutionMessageKey(BaseModel):
    remoteJid: str
    fromMe: bool
    id: str

class EvolutionMessageData(BaseModel):
    key: EvolutionMessageKey
    message: EvolutionMessageContent

class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: EvolutionMessageData


# ==========================================
# 2. CORE NLP INFERENCE LOGIC (spaCy)
# ==========================================

def analyze_social_engineering(text: str):
    doc = nlp(text)
    results = {
        "urgency_score": 0,
        "authority_markers": [],
        "action_requests": []
    }
    
    # 1. Authority Detection (Named Entity Recognition)
    for ent in doc.ents:
        if ent.label_ in ["ORG", "PERSON", "GPE"]:
            results["authority_markers"].append(ent.text)
            
    # 2. Urgency Detection (Keyword matching on tokens)
    urgency_keywords = ["now", "immediately", "urgent", "minutes", "seconds", "last chance"]
    for token in doc:
        if token.text.lower() in urgency_keywords:
            results["urgency_score"] += 1
            
    # 3. Action Extraction (Lemmatized Verbs & Noun Chunks)
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

    return results


# ==========================================
# 3. INTERACTIVE ENDPOINTS (API ZONE)
# ==========================================

# --- Endpoint 1: Internal Pipeline Inference ---
@app.post("/v1/analyze", response_model=InferenceResponse, status_code=status.HTTP_200_OK)
async def evaluate_message_stream(payload: InferenceRequest):
    try:
        conv_id = payload.session_state.conversation_id if payload.session_state else "anonymous_session"
        current_score = payload.session_state.risk_score if payload.session_state else 0
        active_flags = set(payload.session_state.flags) if payload.session_state else set()
        
        # Run linguistic processing
        analysis = analyze_social_engineering(payload.current_message.text)
        
        # State Modification Logic
        if analysis['urgency_score'] > 0:
            current_score += (analysis['urgency_score'] * 2)
            active_flags.add("URGENCY_DETECTED")
            
        if analysis['authority_markers']:
            current_score += 3
            active_flags.add("AUTHORITY_CLAIMED")
            
        if analysis['action_requests']:
            current_score += 5
            active_flags.add("FINANCIAL_PIVOT")

        alert_triggered = True if current_score >= 8 else False

        return InferenceResponse(
            conversation_id=conv_id,
            updated_risk_score=current_score,
            active_flags=list(active_flags),
            alert_triggered=alert_triggered
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference Engine Crash: {str(e)}"
        )


# --- Endpoint 2: Live Evolution API Webhook Ingestor ---
@app.post("/v1/webhook/whatsapp", status_code=status.HTTP_200_OK)
async def handle_whatsapp_webhook(payload: EvolutionWebhookPayload):
    try:
        # 1. Ignore outbound messages sent by you to prevent feedback loops
        if payload.data.key.fromMe:
            return {"status": "ignored", "reason": "outbound_message"}
            
        incoming_text = payload.data.message.conversation
        sender_id = payload.data.key.remoteJid
        
        # Guard clause against empty media messages, stickers, etc.
        if not incoming_text:
            return {"status": "ignored", "reason": "non_text_message"}
            
        print(f"📥 [LIVE DATA] Message from {sender_id}: '{incoming_text}'")
        
        # 2. Run NLP parsing on live data stream
        analysis = analyze_social_engineering(incoming_text)
        
        # 3. State calculation (Simulated baseline state for isolation)
        current_score = 0
        active_flags = set()
        
        if analysis['urgency_score'] > 0:
            current_score += (analysis['urgency_score'] * 2)
            active_flags.add("URGENCY_DETECTED")
        if analysis['authority_markers']:
            current_score += 3
            active_flags.add("AUTHORITY_CLAIMED")
        if analysis['action_requests']:
            current_score += 5
            active_flags.add("FINANCIAL_PIVOT")
            
        alert_triggered = True if current_score >= 8 else False
        
        # 4. Trigger localized alert visibility
        if alert_triggered:
            print(f"🚨 [SCAM THREAT TRIGGERED] User: {sender_id} | Total Risk: {current_score} | Active Indicators: {list(active_flags)}")
            
        return {
            "status": "processed",
            "sender": sender_id,
            "cumulative_risk": current_score,
            "alert": alert_triggered,
            "extracted_indicators": analysis
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook Ingestion Pipeline Failure: {str(e)}"
        )


# ==========================================
# 4. CLUSTER OPERATIONS MANAGEMENT (HEALTH)
# ==========================================

@app.get("/healthz", status_code=status.HTTP_200_OK)
async def health_check():
    """
    Kubernetes / OpenShift Liveness and Readiness Probe Target.
    Guarantees that the container is up and the spaCy NLP model is fully loaded in memory.
    """
    return {
        "status": "healthy",
        "model_loaded": True if nlp else False,
        "engine": "Project Aegis Core Inference v1.0"
    }