import os
import re
from contextlib import asynccontextmanager
from typing import Any, List, Optional, Set, Tuple

import httpx
import spacy
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

import evolution_client
import session_store
from media_extractor import extract_text_from_media_async

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "test-user")
EVOLUTION_ALERT_NUMBER = os.getenv("EVOLUTION_ALERT_NUMBER", "")
EVOLUTION_ALERTS_ENABLED = os.getenv("EVOLUTION_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes")
RISK_ALERT_THRESHOLD = int(os.getenv("RISK_ALERT_THRESHOLD", "8"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await session_store.connect()
    yield
    await session_store.disconnect()


# Initialize FastAPI app
app = FastAPI(
    title="Project Aegis: Social Engineering Inference Engine",
    description="Enterprise-grade NLP engine for real-time social engineering and scam detection.",
    version="1.0.0",
    lifespan=lifespan,
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


def extract_whatsapp_text(message: dict) -> Optional[str]:
    """Pull plain text from Evolution / Baileys message shapes."""
    if not message:
        return None
    if message.get("conversation"):
        return message["conversation"]
    extended = message.get("extendedTextMessage")
    if extended and extended.get("text"):
        return extended["text"]
    for doc_key in ("documentMessage", "documentWithCaptionMessage"):
        doc = message.get(doc_key)
        if isinstance(doc, dict):
            if doc.get("caption"):
                return doc["caption"]
            nested = doc.get("documentMessage")
            if isinstance(nested, dict) and nested.get("caption"):
                return nested["caption"]
    image = message.get("imageMessage")
    if image and image.get("caption"):
        return image["caption"]
    video = message.get("videoMessage")
    if video and video.get("caption"):
        return video["caption"]
    buttons = message.get("buttonsResponseMessage")
    if buttons and buttons.get("selectedDisplayText"):
        return buttons["selectedDisplayText"]
    list_resp = message.get("listResponseMessage")
    if list_resp and list_resp.get("title"):
        return list_resp["title"]
    return None


def message_has_media(message: dict) -> bool:
    return any(
        key in message
        for key in ("imageMessage", "documentMessage", "documentWithCaptionMessage")
    )


def _media_filename_from_message(message: dict) -> str:
    for key in ("documentMessage", "documentWithCaptionMessage", "imageMessage"):
        blob = message.get(key)
        if not isinstance(blob, dict):
            continue
        for field in ("fileName", "filename", "title"):
            if blob.get(field):
                return str(blob[field])
        nested = blob.get("documentMessage")
        if isinstance(nested, dict):
            for field in ("fileName", "filename", "title"):
                if nested.get(field):
                    return str(nested[field])
    return "attachment"


def _media_mimetype_from_message(message: dict) -> str:
    for key in ("documentMessage", "documentWithCaptionMessage", "imageMessage"):
        blob = message.get(key)
        if not isinstance(blob, dict):
            continue
        if blob.get("mimetype"):
            return str(blob["mimetype"])
        nested = blob.get("documentMessage")
        if isinstance(nested, dict) and nested.get("mimetype"):
            return str(nested["mimetype"])
    return ""


def normalize_combined_text(*parts: Optional[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts if p and p.strip())).strip()


async def resolve_inbound_content(record: dict, instance: str) -> dict[str, Any]:
    """Merge caption/text with extracted PDF or image content."""
    message = record.get("message") or {}
    direct_text = extract_whatsapp_text(message) or ""
    media_info: dict[str, Any] = {"has_media": message_has_media(message)}

    if media_info["has_media"]:
        media_info["filename"] = _media_filename_from_message(message)
        media_info["mimetype"] = _media_mimetype_from_message(message)
        fetch = await evolution_client.fetch_message_media(instance, record)
        media_info["fetch"] = {k: v for k, v in fetch.items() if k != "bytes"}
        if fetch.get("ok"):
            extraction = await extract_text_from_media_async(
                fetch["bytes"],
                fetch.get("mimetype") or media_info["mimetype"],
                fetch.get("fileName") or media_info["filename"],
            )
            media_info["extraction"] = extraction
        else:
            media_info["extraction"] = {"ok": False, "error": fetch.get("error", "fetch_failed")}

    extracted_text = ""
    extraction = media_info.get("extraction") or {}
    if extraction.get("ok"):
        extracted_text = extraction.get("text", "")

    combined_text = normalize_combined_text(direct_text, extracted_text)
    return {
        "combined_text": combined_text,
        "direct_text": direct_text,
        "extracted_text": extracted_text,
        "media_info": media_info,
    }


def evolution_message_records(data: Any) -> List[dict]:
    """Evolution v2 may send one message object or a list."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def score_message_delta(
    analysis: dict,
    document_delta: int = 0,
    document_flags: Optional[List[str]] = None,
) -> Tuple[int, Set[str]]:
    """Points and flags contributed by a single message."""
    delta = 0
    flags: Set[str] = set()

    if analysis["urgency_score"] > 0:
        delta += analysis["urgency_score"] * 2
        flags.add("URGENCY_DETECTED")
    if analysis["authority_markers"]:
        delta += 3
        flags.add("AUTHORITY_CLAIMED")
    if analysis["action_requests"]:
        delta += 5
        flags.add("FINANCIAL_PIVOT")

    if document_delta > 0:
        delta += document_delta
        flags.update(document_flags or [])

    return delta, flags


async def process_inbound_whatsapp_message(record: dict, instance: str) -> dict:
    key = record.get("key") or {}
    if key.get("fromMe"):
        return {"status": "ignored", "reason": "outbound_message"}

    message_id = key.get("id", "")
    if not await session_store.mark_message_if_new(message_id):
        return {"status": "ignored", "reason": "duplicate_message", "message_id": message_id}

    sender_id = key.get("remoteJid", "unknown")
    content = await resolve_inbound_content(record, instance)
    incoming_text = content["combined_text"]

    if not incoming_text:
        if content["media_info"].get("has_media"):
            return {
                "status": "ignored",
                "reason": "media_extract_failed",
                "media_info": content["media_info"],
            }
        return {"status": "ignored", "reason": "non_text_message"}

    extraction = content["media_info"].get("extraction") or {}
    source_kind = extraction.get("kind") or ("text" if not content["media_info"].get("has_media") else "media")
    preview_prefix = "[PDF] " if source_kind == "pdf" else "[IMAGE] " if source_kind == "image" else ""
    message_preview = f"{preview_prefix}{incoming_text[:160]}"

    print(f"📥 [LIVE DATA] ({source_kind}) from {sender_id}: '{message_preview}'")

    analysis = analyze_social_engineering(incoming_text)
    document_delta = int(extraction.get("document_delta") or 0)
    document_flags = list(extraction.get("document_flags") or [])
    delta, msg_flags = score_message_delta(analysis, document_delta, document_flags)

    session = await session_store.get_session(sender_id)
    previous_score = session.risk_score
    cumulative_score = previous_score + delta
    active_flags = set(session.flags) | msg_flags

    session.risk_score = cumulative_score
    session.flags = sorted(active_flags)
    session.message_count += 1
    session.last_message_preview = message_preview
    await session_store.save_session(session)

    alert_triggered = cumulative_score >= RISK_ALERT_THRESHOLD
    if alert_triggered:
        print(
            f"🚨 [SCAM THREAT TRIGGERED] User: {sender_id} | "
            f"Total Risk: {cumulative_score} (+{delta} this msg) | "
            f"Active Indicators: {session.flags}"
        )

    return {
        "status": "processed",
        "sender": sender_id,
        "message_id": message_id,
        "source_kind": source_kind,
        "message_delta": delta,
        "previous_risk": previous_score,
        "cumulative_risk": cumulative_score,
        "message_count": session.message_count,
        "alert": alert_triggered,
        "active_flags": session.flags,
        "extracted_indicators": analysis,
        "document_signals": {
            "triggers": extraction.get("document_triggers", []),
            "reasons": extraction.get("document_reasons", []),
            "used_ocr": extraction.get("used_ocr", False),
        },
        "media_info": content["media_info"],
        "message_preview": message_preview,
    }


async def send_scam_alert_whatsapp(
    instance: str,
    sender_id: str,
    risk_score: int,
    active_flags: List[str],
    message_preview: str,
) -> dict:
    """Send a WhatsApp alert to the configured owner number via Evolution API."""
    if not EVOLUTION_ALERTS_ENABLED:
        return {"alert_sent": False, "reason": "alerts_disabled"}
    if not EVOLUTION_API_KEY or not EVOLUTION_ALERT_NUMBER:
        print("[ALERT] Skipped: set EVOLUTION_API_KEY and EVOLUTION_ALERT_NUMBER")
        return {"alert_sent": False, "reason": "missing_config"}

    instance_name = instance or EVOLUTION_INSTANCE
    flags_text = ", ".join(active_flags) if active_flags else "none"
    preview = message_preview if len(message_preview) <= 120 else f"{message_preview[:117]}..."
    alert_text = (
        "⚠️ Project Aegis — scam risk detected\n"
        f"Risk score: {risk_score}\n"
        f"From: {sender_id}\n"
        f"Flags: {flags_text}\n"
        f"Message: {preview}"
    )

    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}"
    payload = {"number": EVOLUTION_ALERT_NUMBER, "text": alert_text}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            print(f"[ALERT] Evolution sendText failed ({response.status_code}): {response.text}")
            return {"alert_sent": False, "reason": "evolution_error", "status_code": response.status_code}

        print(f"[ALERT] WhatsApp notification sent to {EVOLUTION_ALERT_NUMBER}")
        return {"alert_sent": True, "recipient": EVOLUTION_ALERT_NUMBER}
    except httpx.HTTPError as exc:
        print(f"[ALERT] Evolution sendText request failed: {exc}")
        return {"alert_sent": False, "reason": "request_failed", "error": str(exc)}


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

        analysis = analyze_social_engineering(payload.current_message.text)
        delta, msg_flags = score_message_delta(analysis)
        current_score += delta
        active_flags |= msg_flags

        alert_triggered = current_score >= RISK_ALERT_THRESHOLD

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
@app.post("/v1/webhook/whatsapp/{event_suffix:path}", status_code=status.HTTP_200_OK)
async def handle_whatsapp_webhook(request: Request, event_suffix: str = ""):
    try:
        body = await request.json()
        event = body.get("event", event_suffix.replace("/", ".") or "unknown")

        if event != "messages.upsert":
            return {"status": "ignored", "reason": "non_message_event", "event": event}

        instance = body.get("instance", EVOLUTION_INSTANCE)
        results = []
        for record in evolution_message_records(body.get("data")):
            result = await process_inbound_whatsapp_message(record, instance)
            if result.get("status") == "processed" and result.get("alert"):
                if await session_store.try_acquire_alert_cooldown(result["sender"]):
                    alert_meta = await send_scam_alert_whatsapp(
                        instance=instance,
                        sender_id=result["sender"],
                        risk_score=result["cumulative_risk"],
                        active_flags=result.get("active_flags", []),
                        message_preview=result.get("message_preview", ""),
                    )
                else:
                    alert_meta = {
                        "alert_sent": False,
                        "reason": "cooldown",
                        "cooldown_seconds": session_store.ALERT_COOLDOWN_SECONDS,
                    }
                result["alert_delivery"] = alert_meta
            results.append(result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook Ingestion Pipeline Failure: {str(e)}",
        )

    if not results:
        return {"status": "ignored", "reason": "empty_payload"}

    for result in reversed(results):
        if result.get("status") == "processed":
            return result
    return results[-1]


# --- Endpoint 3: Local PDF/image scan (dev test without WhatsApp) ---
@app.post("/v1/scan-document", status_code=status.HTTP_200_OK)
async def scan_document_upload(file: UploadFile = File(...)):
    try:
        data = await file.read()
        extraction = await extract_text_from_media_async(
            data,
            file.content_type or "",
            file.filename or "upload",
        )
        if not extraction.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=extraction.get("error", "unsupported_media_type"),
            )

        analysis = analyze_social_engineering(extraction.get("text", ""))
        delta, flags = score_message_delta(
            analysis,
            int(extraction.get("document_delta") or 0),
            list(extraction.get("document_flags") or []),
        )
        return {
            "filename": file.filename,
            "kind": extraction.get("kind"),
            "text_preview": extraction.get("text", "")[:500],
            "used_ocr": extraction.get("used_ocr", False),
            "document_signals": {
                "triggers": extraction.get("document_triggers", []),
                "reasons": extraction.get("document_reasons", []),
            },
            "nlp_indicators": analysis,
            "message_delta": delta,
            "active_flags": sorted(flags),
            "alert_would_trigger": delta >= RISK_ALERT_THRESHOLD,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document scan failed: {str(e)}",
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
        "redis_connected": await session_store.ping(),
        "engine": "Project Aegis Core Inference v1.0",
    }
    