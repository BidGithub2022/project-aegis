# MCP — Solution 2 (optional, isolated)

Solution 1 (unchanged): WhatsApp → Evolution → `egis-app` webhook → Redis → alerts.

Solution 2: **MCP tools** for debugging and manual analysis. MCP does **not** replace or modify the webhook.

## Design

| Property | Detail |
|----------|--------|
| Process | Separate `mcp_server.py` (not in Docker `egis-app` image) |
| Communication | HTTP only to `egis-app` (`EGIS_API_URL`) |
| Imports | No `app.py`, no spaCy in MCP process |
| Default `docker compose up` | Unchanged — MCP is opt-in |

## Setup

1. Start the normal stack:

   ```bash
   cd docker && docker compose up -d
   ```

2. Install MCP dependencies (local Python **3.10+**, not required in Docker `egis-app`):

   ```bash
   pip install -r requirements-mcp.txt
   ```

   The MCP server is **not** bundled in the inference Docker image, so the live webhook container is unchanged.

3. Register in Cursor — copy `mcp.json.example` into your Cursor MCP config and fix the path to `mcp_server.py`.

4. Run manually (optional):

   ```bash
   EGIS_API_URL=http://localhost:8000 python mcp_server.py
   ```

## Tools

| Tool | Affects live flow? |
|------|-------------------|
| `aegis_health` | No — read-only |
| `aegis_analyze_text` | No — `/v1/analyze` only |
| `aegis_scan_file` | No — local file upload to `/v1/scan-document` |
| `aegis_get_session` | No — read-only Redis via API |
| `aegis_list_sessions` | No — read-only Redis via API |
| `aegis_simulate_webhook` | **Yes** — runs full webhook pipeline (use for testing only) |

## New HTTP endpoints (additive)

These support MCP read tools and do not change webhook logic:

- `GET /v1/session/{conversation_id}`
- `GET /v1/sessions?min_risk=0&limit=50`

## When to use Solution 2

- Inspect Redis session for a sender
- Scan a PDF/image from disk without WhatsApp
- Test scoring on arbitrary text
- Replay webhook JSON in dev (`aegis_simulate_webhook`)

For automatic protection, rely on Solution 1 only.
