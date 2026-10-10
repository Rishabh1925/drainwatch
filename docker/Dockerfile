# syntax=docker/dockerfile:1
#
# Single container: API + ML model + React dashboard.
# Deployed to AWS ECS Fargate (ap-southeast-2).

# ---------- stage 1: build the React dashboard ----------
FROM node:20-slim AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./
RUN npm install
COPY frontend/ ./
RUN npm run build


# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    PORT=8080

WORKDIR /app

# libgomp1 is required by torch; the slim base does not carry it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# CPU-only torch first, in its own layer, so it is cached across rebuilds.
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.6.0

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir \
      "transformers>=4.44,<6" \
      "safetensors>=0.4" \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.30" \
      "python-multipart>=0.0.9" \
      "boto3>=1.34" \
      "numpy>=1.26" \
      "pillow>=10.2" \
      "supabase>=2.0" \
      "python-dotenv>=1.0" \
 && pip install --no-cache-dir --no-deps -e .

# Trained weights baked into the image — no S3 pull at boot.
COPY checkpoints/ ./checkpoints/

# Built React app: main.py prefers frontend/dist over web/dist.
COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health').read()" || exit 1

CMD ["sh", "-c", "uvicorn drainwatch.api.main:app --host 0.0.0.0 --port ${PORT}"]
