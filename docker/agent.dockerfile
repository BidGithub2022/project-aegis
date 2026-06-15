FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_PYTHON=/usr/local/bin/python \
    MCP_SERVER=/app/mcp_server.py \
    REDIS_HOST=egis-redis \
    EGIS_API_URL=http://egis-app:8000

WORKDIR /app

COPY requirements-mcp.txt requirements-agent.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-agent.txt

COPY agent_worker.py agent_config.py mcp_server.py ./

CMD ["python", "agent_worker.py"]
