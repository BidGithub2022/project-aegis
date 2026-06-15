"""
Gray-zone agent worker — Cursor SDK + project-aegis MCP tools.

Runs on your Mac (not inside egis-app Docker). Pulls jobs from Redis,
reviews cases with Cursor, posts verdict back to egis-app.

  export CURSOR_API_KEY="cursor_..."
  export REDIS_HOST=localhost
  export EGIS_API_URL=http://localhost:8000
  python agent_worker.py
"""

import json
import logging
import os
import re
import sys

import httpx
import redis
from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
from cursor_sdk.errors import AuthenticationError
from dotenv import load_dotenv

import agent_config

load_dotenv(agent_config.PROJECT_ROOT / ".env")

VERDICT_RE = re.compile(
    r"VERDICT:\s*(escalate|benign|monitor)\b",
    re.IGNORECASE,
)
REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)

SYSTEM_INSTRUCTIONS = """You are Project Aegis fraud analyst reviewing a GRAY-ZONE WhatsApp message.
The rule engine scored this between auto-ignore and auto-alert thresholds.

Use the project-aegis MCP tools:
- aegis_get_session — Redis session and message history for this sender
- aegis_analyze_text — re-score suspicious phrases if needed

Then decide:
- escalate — likely scam; owner should be alerted
- benign — normal conversation; suppress further gray-zone alerts
- monitor — suspicious but inconclusive; log only

Your FINAL message must include exactly these lines:
VERDICT: escalate|benign|monitor
REASON: one short sentence explaining why
"""


def parse_verdict(text: str) -> tuple[str, str]:
    verdict_match = VERDICT_RE.search(text)
    reason_match = REASON_RE.search(text)
    verdict = verdict_match.group(1).lower() if verdict_match else "monitor"
    if reason_match:
        reason = reason_match.group(1).strip().split("\n")[0][:500]
    else:
        reason = text.strip()[:200]
    return verdict, reason


def submit_verdict(job: dict, verdict: str, reason: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if agent_config.AGENT_INTERNAL_TOKEN:
        headers["X-Agent-Token"] = agent_config.AGENT_INTERNAL_TOKEN

    payload = {
        "job_id": job["job_id"],
        "conversation_id": job["conversation_id"],
        "instance": job.get("instance", "test-user"),
        "verdict": verdict,
        "reason": reason,
        "risk_score": job.get("cumulative_risk", 0),
        "message_preview": job.get("message_preview", ""),
        "active_flags": job.get("active_flags", []),
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{agent_config.EGIS_API_URL}/v1/agent/verdict",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def build_prompt(job: dict) -> str:
    return (
        "Review this gray-zone case:\n\n"
        f"{json.dumps(job, indent=2)}\n\n"
        f"Conversation ID for tools: {job['conversation_id']}"
    )


def run_agent_on_job(job: dict, api_key: str) -> tuple[str, str, str]:
    options = AgentOptions(
        model=agent_config.CURSOR_MODEL,
        api_key=api_key,
        local=LocalAgentOptions(cwd=str(agent_config.PROJECT_ROOT)),
        mcp_servers=agent_config.mcp_server_config(),
    )
    with Agent.create(options) as agent:
        run = agent.send(SYSTEM_INSTRUCTIONS + "\n\n" + build_prompt(job))
        text = run.text()
        verdict, reason = parse_verdict(text or "")
        return verdict, reason, text or ""


def requeue_job(client: redis.Redis, job: dict) -> None:
    """Put a failed job back on the queue (BRPOP removes it before processing)."""
    client.lpush(agent_config.AGENT_QUEUE_KEY, json.dumps(job))
    logging.warning("Re-queued job %s", job.get("job_id", "unknown"))


def process_job(job: dict, api_key: str) -> None:
    job_id = job.get("job_id", "unknown")
    conversation_id = job.get("conversation_id", "unknown")
    logging.info("Processing job %s for %s", job_id, conversation_id)
    verdict, reason, raw = run_agent_on_job(job, api_key)
    logging.info("Agent output: %s", raw[:300])
    result = submit_verdict(job, verdict, reason)
    logging.info(
        "Verdict %s — alert_delivery=%s",
        verdict,
        result.get("alert_delivery"),
    )


def make_redis_client() -> redis.Redis:
    return redis.Redis(
        host=agent_config.REDIS_HOST,
        port=agent_config.REDIS_PORT,
        db=agent_config.REDIS_DB,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
        health_check_interval=30,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    if not api_key:
        logging.error("Set CURSOR_API_KEY (Cursor Dashboard → Integrations / API Keys)")
        sys.exit(1)
    if api_key.startswith("cursor_your_key") or api_key == "cursor_your_key_here":
        logging.error("Replace the placeholder CURSOR_API_KEY in .env with a real key")
        sys.exit(1)

    if not agent_config.MCP_PYTHON.is_file():
        logging.error("MCP Python not found at %s", agent_config.MCP_PYTHON)
        sys.exit(1)

    client = make_redis_client()
    logging.info(
        "Agent worker listening on %s (Redis List %s:%s, API %s)",
        agent_config.AGENT_QUEUE_KEY,
        agent_config.REDIS_HOST,
        agent_config.REDIS_PORT,
        agent_config.EGIS_API_URL,
    )

    while True:
        try:
            item = client.brpop(agent_config.AGENT_QUEUE_KEY, timeout=5)
        except (redis.TimeoutError, redis.ConnectionError) as exc:
            logging.warning("Redis connection issue (%s), reconnecting", exc)
            client = make_redis_client()
            continue

        if not item:
            continue

        job = json.loads(item[1])
        try:
            process_job(job, api_key)
        except AuthenticationError:
            logging.error(
                "Invalid CURSOR_API_KEY — fix .env and restart. "
                "Job %s re-queued.",
                job.get("job_id"),
            )
            requeue_job(client, job)
            sys.exit(1)
        except Exception:
            logging.exception("Job %s failed, re-queuing", job.get("job_id"))
            requeue_job(client, job)


if __name__ == "__main__":
    main()
