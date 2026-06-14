import json
import os
from typing import Optional

import redis.asyncio as redis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
SESSION_TTL_SECONDS = int(os.getenv("REDIS_SESSION_TTL_SECONDS", "604800"))  # 7 days
MESSAGE_DEDUP_TTL_SECONDS = int(os.getenv("REDIS_MESSAGE_DEDUP_TTL_SECONDS", "86400"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))

SESSION_KEY_PREFIX = "aegis:session:"
MESSAGE_KEY_PREFIX = "aegis:msg:"
ALERT_COOLDOWN_PREFIX = "aegis:alert_cooldown:"

_client: Optional[redis.Redis] = None


class ConversationSession:
    def __init__(
        self,
        conversation_id: str,
        risk_score: int = 0,
        flags: list[str] | None = None,
        message_count: int = 0,
        last_message_preview: str = "",
    ):
        self.conversation_id = conversation_id
        self.risk_score = risk_score
        self.flags = list(flags or [])
        self.message_count = message_count
        self.last_message_preview = last_message_preview

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "risk_score": self.risk_score,
            "flags": self.flags,
            "message_count": self.message_count,
            "last_message_preview": self.last_message_preview,
        }

    @classmethod
    def from_dict(cls, data: dict, conversation_id: str) -> "ConversationSession":
        return cls(
            conversation_id=conversation_id,
            risk_score=int(data.get("risk_score", 0)),
            flags=list(data.get("flags") or []),
            message_count=int(data.get("message_count", 0)),
            last_message_preview=str(data.get("last_message_preview", "")),
        )


async def connect() -> None:
    global _client
    _client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
    )
    await _client.ping()


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping() -> bool:
    if _client is None:
        return False
    try:
        await _client.ping()
        return True
    except redis.RedisError:
        return False


def _require_client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis client is not connected")
    return _client


async def get_session(conversation_id: str) -> ConversationSession:
    client = _require_client()
    raw = await client.get(f"{SESSION_KEY_PREFIX}{conversation_id}")
    if not raw:
        return ConversationSession(conversation_id=conversation_id)
    return ConversationSession.from_dict(json.loads(raw), conversation_id)


async def save_session(session: ConversationSession) -> None:
    client = _require_client()
    key = f"{SESSION_KEY_PREFIX}{session.conversation_id}"
    await client.set(key, json.dumps(session.to_dict()), ex=SESSION_TTL_SECONDS)


async def mark_message_if_new(message_id: str) -> bool:
    """Return True if this message_id was not seen before (process it)."""
    if not message_id:
        return True
    client = _require_client()
    key = f"{MESSAGE_KEY_PREFIX}{message_id}"
    return bool(
        await client.set(key, "1", nx=True, ex=MESSAGE_DEDUP_TTL_SECONDS)
    )


async def try_acquire_alert_cooldown(conversation_id: str) -> bool:
    """Return True if an alert may be sent (cooldown not active)."""
    client = _require_client()
    key = f"{ALERT_COOLDOWN_PREFIX}{conversation_id}"
    return bool(
        await client.set(key, "1", nx=True, ex=ALERT_COOLDOWN_SECONDS)
    )
