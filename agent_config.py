"""Shared agent / gray-zone settings (egis-app + agent_worker)."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

GRAY_ZONE_MIN = int(os.getenv("GRAY_ZONE_MIN", "4"))
GRAY_ZONE_MAX = int(os.getenv("GRAY_ZONE_MAX", "7"))
AGENT_ENABLED = os.getenv("AGENT_ENABLED", "true").lower() in ("1", "true", "yes")

EGIS_API_URL = os.getenv("EGIS_API_URL", "http://localhost:8000").rstrip("/")
AGENT_INTERNAL_TOKEN = os.getenv("AGENT_INTERNAL_TOKEN", "")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

MCP_PYTHON = Path(
    os.getenv("MCP_PYTHON", str(PROJECT_ROOT / ".venv-mcp" / "bin" / "python"))
)
MCP_SERVER = Path(os.getenv("MCP_SERVER", str(PROJECT_ROOT / "mcp_server.py")))
CURSOR_MODEL = os.getenv("CURSOR_AGENT_MODEL", "composer-2.5")

# Simple Redis List queue (LPUSH / BRPOP) — same egis-redis instance, not Streams/RabbitMQ.
AGENT_QUEUE_KEY = "aegis:agent:queue"
AGENT_PENDING_PREFIX = "aegis:agent:pending:"
AGENT_CASE_PREFIX = "aegis:agent:case:"
AGENT_PENDING_TTL_SECONDS = int(os.getenv("AGENT_PENDING_TTL_SECONDS", "120"))
AGENT_CASE_TTL_SECONDS = int(os.getenv("AGENT_CASE_TTL_SECONDS", "604800"))
MESSAGE_HISTORY_MAX = int(os.getenv("MESSAGE_HISTORY_MAX", "20"))


def is_gray_zone(score: int) -> bool:
    return GRAY_ZONE_MIN <= score <= GRAY_ZONE_MAX


def mcp_server_config() -> dict:
    return {
        "project-aegis": {
            "command": str(MCP_PYTHON),
            "args": [str(MCP_SERVER)],
            "env": {"EGIS_API_URL": EGIS_API_URL},
        }
    }
