import json
import os
from typing import Optional

import redis.asyncio as redis

import agent_config

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
        message_history: list[dict] | None = None,
        agent_verdict: str = "",
        agent_reason: str = "",
    ):
        self.conversation_id = conversation_id
        self.risk_score = risk_score
        self.flags = list(flags or [])
        self.message_count = message_count
        self.last_message_preview = last_message_preview
        self.message_history = list(message_history or [])
        self.agent_verdict = agent_verdict
        self.agent_reason = agent_reason

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "risk_score": self.risk_score,
            "flags": self.flags,
            "message_count": self.message_count,
            "last_message_preview": self.last_message_preview,
            "message_history": self.message_history,
            "agent_verdict": self.agent_verdict,
            "agent_reason": self.agent_reason,
        }

    @classmethod
    def from_dict(cls, data: dict, conversation_id: str) -> "ConversationSession":
        return cls(
            conversation_id=conversation_id,
            risk_score=int(data.get("risk_score", 0)),
            flags=list(data.get("flags") or []),
            message_count=int(data.get("message_count", 0)),
            last_message_preview=str(data.get("last_message_preview", "")),
            message_history=list(data.get("message_history") or []),
            agent_verdict=str(data.get("agent_verdict", "")),
            agent_reason=str(data.get("agent_reason", "")),
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


async def list_sessions(min_risk: int = 0, limit: int = 50) -> list[dict]:
    """Read-only listing for ops / MCP (does not affect webhook flow)."""
    client = _require_client()
    results: list[dict] = []
    async for key in client.scan_iter(match=f"{SESSION_KEY_PREFIX}*", count=100):
        raw = await client.get(key)
        if not raw:
            continue
        conversation_id = key.removeprefix(SESSION_KEY_PREFIX)
        data = json.loads(raw)
        score = int(data.get("risk_score", 0))
        if score < min_risk:
            continue
        results.append(
            {
                "conversation_id": conversation_id,
                "risk_score": score,
                "flags": list(data.get("flags") or []),
                "message_count": int(data.get("message_count", 0)),
                "last_message_preview": str(data.get("last_message_preview", "")),
                "agent_verdict": str(data.get("agent_verdict", "")),
            }
        )
        if len(results) >= limit:
            break
    results.sort(key=lambda item: item["risk_score"], reverse=True)
    return results


def append_message_history(
    session: ConversationSession,
    preview: str,
    delta: int,
    flags: list[str],
) -> None:
    session.message_history.append(
        {
            "preview": preview,
            "delta": delta,
            "flags": list(flags),
        }
    )
    max_len = agent_config.MESSAGE_HISTORY_MAX
    if len(session.message_history) > max_len:
        session.message_history = session.message_history[-max_len:]


async def enqueue_agent_job(job: dict) -> bool:
    """Push a gray-zone job. Returns False if a review is already pending for this sender."""
    client = _require_client()
    conversation_id = str(job.get("conversation_id", ""))
    if not conversation_id:
        return False
    pending_key = f"{agent_config.AGENT_PENDING_PREFIX}{conversation_id}"
    if not await client.set(
        pending_key,
        "1",
        nx=True,
        ex=agent_config.AGENT_PENDING_TTL_SECONDS,
    ):
        return False
    await client.lpush(agent_config.AGENT_QUEUE_KEY, json.dumps(job))
    return True


async def clear_agent_pending(conversation_id: str) -> None:
    client = _require_client()
    await client.delete(f"{agent_config.AGENT_PENDING_PREFIX}{conversation_id}")


async def save_agent_case(job_id: str, case: dict) -> None:
    client = _require_client()
    key = f"{agent_config.AGENT_CASE_PREFIX}{job_id}"
    await client.set(key, json.dumps(case), ex=agent_config.AGENT_CASE_TTL_SECONDS)


async def get_agent_case(job_id: str) -> Optional[dict]:
    client = _require_client()
    raw = await client.get(f"{agent_config.AGENT_CASE_PREFIX}{job_id}")
    if not raw:
        return None
    return json.loads(raw)


async def agent_queue_length() -> int:
    client = _require_client()
    return int(await client.llen(agent_config.AGENT_QUEUE_KEY))
