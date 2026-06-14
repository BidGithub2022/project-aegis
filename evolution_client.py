import base64
import os
from typing import Any, Optional

import httpx

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
MEDIA_FETCH_TIMEOUT = float(os.getenv("MEDIA_FETCH_TIMEOUT_SECONDS", "30"))


def _decode_base64_payload(data: Any) -> Optional[bytes]:
    if data is None:
        return None
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=False)
        except Exception:
            return None
    if isinstance(data, dict):
        if data.get("type") == "Buffer" and isinstance(data.get("data"), list):
            return bytes(data["data"])
        for key in ("base64", "data", "file"):
            if key in data:
                return _decode_base64_payload(data[key])
    return None


async def fetch_message_media(
    instance: str,
    record: dict,
    convert_to_mp4: bool = False,
) -> dict[str, Any]:
    """Download media bytes for a WhatsApp message via Evolution API."""
    if not EVOLUTION_API_KEY:
        return {"ok": False, "error": "missing_evolution_api_key"}

    url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{instance}"
    payload = {"message": record, "convertToMp4": convert_to_mp4}

    try:
        async with httpx.AsyncClient(timeout=MEDIA_FETCH_TIMEOUT) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": "evolution_http_error",
                "status_code": response.status_code,
                "detail": response.text[:500],
            }

        body = response.json()
        raw_b64 = body.get("base64") or body.get("data") or body
        media_bytes = _decode_base64_payload(raw_b64)
        if not media_bytes:
            media_bytes = _decode_base64_payload(body.get("buffer"))

        if not media_bytes:
            return {"ok": False, "error": "empty_media_payload", "raw_keys": list(body.keys())}

        return {
            "ok": True,
            "bytes": media_bytes,
            "mimetype": body.get("mimetype") or body.get("mimeType") or "",
            "fileName": body.get("fileName") or body.get("filename") or "",
            "mediaType": body.get("mediaType") or "",
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": "request_failed", "detail": str(exc)}
