# Agent — Gray-zone triage (Solution 3)

Automatic **rule-based** alerts (score ≥ 8) stay on the webhook path (Solution 1).
**Gray-zone** messages (score 4–7) are queued for a **Cursor agent** that uses MCP tools (Solution 2) to investigate and decide.

## Architecture

```text
WhatsApp → webhook → spaCy + heuristics → Redis session
                    │
                    ├─ score ≥ 8  → auto WhatsApp alert (unchanged)
                    ├─ score 4–7  → Redis queue → agent_worker (Cursor SDK)
                    └─ score 0–3  → ignore
```

| Component | Where it runs |
|-----------|----------------|
| `egis-app` | Docker — enqueues gray-zone jobs |
| `egis-agent` | Docker — `agent_worker.py` (Cursor SDK + MCP) |
| `mcp_server.py` | Spawned inside `egis-agent` container for tools |

## Setup (Docker — recommended)

### 1. Configure `.env` (project root)

```bash
cp .env.example .env
# Set CURSOR_API_KEY from Cursor Dashboard → Integrations
```

### 2. Start stack including agent worker

```bash
cd docker
docker compose up -d --build
```

This starts `egis-agent` automatically (`restart: unless-stopped`). No manual `python agent_worker.py` needed.

### 3. Check agent is running

```bash
docker compose logs -f egis-agent
curl http://localhost:8000/v1/agent/status
```

## Setup (manual on Mac — optional)

Use this for local debugging without rebuilding the agent image.

```bash
cd /Users/bidyut/project-aegis
.venv-mcp/bin/pip install -r requirements-agent.txt
set -a && source .env && set +a
.venv-mcp/bin/python agent_worker.py
```

**Stop the Docker agent first** if you run manually (both compete on the same Redis queue):

```bash
docker compose stop egis-agent
```

### Where is the queue?

No RabbitMQ or Redis Streams — jobs live in the **existing `egis-redis` container** as a Redis **List**:

| Redis key | Type | Written by | Read by |
|-----------|------|------------|---------|
| `aegis:agent:queue` | List | `egis-app` (`LPUSH`) | `agent_worker.py` (`BRPOP`) |
| `aegis:agent:pending:{jid}` | String (TTL 120s) | `egis-app` | dedupe guard |
| `aegis:agent:case:{job_id}` | String (TTL 7d) | `egis-app` after verdict | ops / debugging |

Inspect manually:

```bash
docker exec egis-state-cache redis-cli LLEN aegis:agent:queue
docker exec egis-state-cache redis-cli LRANGE aegis:agent:queue 0 -1
```

`BRPOP` **removes** the job when the worker picks it up. If processing fails, the worker re-queues it (`LPUSH`).

## Test gray-zone flow

Send a webhook that lands in gray zone (not instant alert):

```bash
curl -X POST http://localhost:8000/v1/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "event": "messages.upsert",
    "instance": "test-user",
    "data": {
      "key": { "remoteJid": "919777666555@s.whatsapp.net", "fromMe": false, "id": "gray1" },
      "message": { "conversation": "Please send payment soon it is urgent" }
    }
  }'
```

Check response for `"agent_queued": true` and queue length:

```bash
curl http://localhost:8000/v1/agent/status
```

Watch `agent_worker.py` logs for verdict. Then:

```bash
curl http://localhost:8000/v1/agent/case/gray1
curl http://localhost:8000/v1/session/919777666555@s.whatsapp.net
```

## API endpoints (additive)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/agent/status` | Queue depth, gray-zone config |
| `POST` | `/v1/agent/verdict` | Worker applies verdict (optional `X-Agent-Token`) |
| `GET` | `/v1/agent/case/{job_id}` | Read stored case outcome |

## Verdicts

| Verdict | Effect |
|---------|--------|
| `escalate` | WhatsApp alert to owner (if cooldown allows) |
| `benign` | Lower risk score; no alert |
| `monitor` | Log only; session keeps current score |

## Environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `AGENT_ENABLED` | `true` | `egis-app` |
| `GRAY_ZONE_MIN` | `4` | `egis-app` |
| `GRAY_ZONE_MAX` | `7` | `egis-app` |
| `AGENT_INTERNAL_TOKEN` | empty | Worker + `/v1/agent/verdict` |
| `CURSOR_API_KEY` | — | `agent_worker.py` |
| `CURSOR_AGENT_MODEL` | `composer-2.5` | Worker |
| `REDIS_HOST` | `egis-redis` (docker) / `localhost` (manual Mac) | Both |
| `EGIS_API_URL` | `http://egis-app:8000` (docker) / `http://localhost:8000` (manual) | Worker + MCP |

## Security

Set `AGENT_INTERNAL_TOKEN` in `docker-compose.yml` and `.env` so only your worker can call `/v1/agent/verdict`.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `agent_queued: false` | Score outside 4–7, or duplicate pending review (120s cooldown per sender) |
| Worker idle | `docker compose logs egis-agent`; ensure `CURSOR_API_KEY` in `.env` |
| MCP errors in agent | In container `EGIS_API_URL=http://egis-app:8000` (set in compose) |
| 401 on verdict | Match `AGENT_INTERNAL_TOKEN` in compose and `.env` |
