"""
Project Aegis MCP server (Solution 2) — isolated from the live webhook path.

Calls the running egis-app HTTP API only. Does not import app.py or load spaCy.
Start separately while docker compose is running:

  pip install -r requirements-mcp.txt
  EGIS_API_URL=http://localhost:8000 python mcp_server.py

Configure in Cursor via mcp.json.example.
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

EGIS_API_URL = os.getenv("EGIS_API_URL", "http://localhost:8000").rstrip("/")
HTTP_TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT_SECONDS", "60"))

mcp = FastMCP(
    "project-aegis",
    instructions=(
        "Tools for Project Aegis fraud detection (Solution 2). "
        "The live WhatsApp webhook (Solution 1) runs automatically in egis-app. "
        "Use these tools for inspection, testing, and analysis without changing that flow."
    ),
)


async def _api_get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(f"{EGIS_API_URL}{path}")
        response.raise_for_status()
        return response.json()


async def _api_post_json(path: str, payload: dict) -> Any:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(f"{EGIS_API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def aegis_health() -> dict:
    """Check egis-app health (model loaded, Redis connected). Read-only."""
    return await _api_get("/healthz")


@mcp.tool()
async def aegis_analyze_text(
    text: str,
    conversation_id: str = "mcp-session",
    risk_score: int = 0,
    flags: list[str] | None = None,
) -> dict:
    """
    Analyze text via /v1/analyze. Does not read or write Redis session state.
    Pass optional risk_score and flags to simulate cumulative scoring manually.
    """
    payload: dict[str, Any] = {"current_message": {"text": text}}
    if risk_score > 0 or flags:
        payload["session_state"] = {
            "conversation_id": conversation_id,
            "risk_score": risk_score,
            "flags": flags or [],
        }
    return await _api_post_json("/v1/analyze", payload)


@mcp.tool()
async def aegis_scan_file(file_path: str) -> dict:
    """
    Scan a local PDF or image via /v1/scan-document. Does not touch WhatsApp or Redis sessions.
    file_path must be readable on the machine running this MCP server.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return {"error": "file_not_found", "path": str(path)}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        with path.open("rb") as handle:
            response = await client.post(
                f"{EGIS_API_URL}/v1/scan-document",
                files={"file": (path.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def aegis_get_session(conversation_id: str) -> dict:
    """Read Redis session for a WhatsApp remoteJid. Read-only; does not change webhook flow."""
    safe_id = conversation_id.strip()
    return await _api_get(f"/v1/session/{safe_id}")


@mcp.tool()
async def aegis_list_sessions(min_risk: int = 0, limit: int = 20) -> dict:
    """List conversation sessions from Redis (read-only)."""
    limit = max(1, min(limit, 200))
    min_risk = max(0, min_risk)
    return await _api_get(f"/v1/sessions?min_risk={min_risk}&limit={limit}")


@mcp.tool()
async def aegis_simulate_webhook(webhook_json: str) -> dict:
    """
    POST a messages.upsert payload to the live webhook. WARNING: runs the full Solution 1 pipeline
    (Redis update, scoring, possible WhatsApp alert). Use only for testing with fake message ids.
    """
    try:
        payload = json.loads(webhook_json)
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "detail": str(exc)}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{EGIS_API_URL}/v1/webhook/whatsapp",
            json=payload,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return {"status_code": response.status_code, "body": body}


if __name__ == "__main__":
    mcp.run()
