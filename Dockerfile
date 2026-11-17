# Stage 1: Build React 19 Enterprise Console
FROM node:22-alpine AS web-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN mkdir -p /app/tenantrag/enterprise/static && npm run build

# Stage 2: Production Python Enterprise Runtime
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    TENANT_RAG_DATA_DIR=/data/tenantrag

COPY pyproject.toml MANIFEST.in LICENSE README.md README.zh-CN.md ./
COPY tenantrag/ ./tenantrag/
COPY admin/ ./admin/
COPY eval/golden.jsonl ./eval/golden.jsonl
COPY --from=web-builder /app/tenantrag/enterprise/static/ ./tenantrag/enterprise/static/

RUN pip install --no-cache-dir ".[enterprise]"

VOLUME ["/data/tenantrag"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/ready', timeout=3)"

CMD ["tenant-rag-enterprise"]
