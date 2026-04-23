# ============================================================
# FabricObo - Single Container Build
#
# Stage 1: Build React SPA (client-app) with Node.js
# Stage 2: Python FastAPI with the built SPA embedded
#
# Usage (from repo root):
#   docker build -t fabricobo:latest .
#   docker run -p 8000:8000 --env-file pythonapi/.env fabricobo:latest
# ============================================================

# ── Stage 1: Build React frontend ──────────────────────────
FROM node:20-slim AS frontend

WORKDIR /build

# Copy source and install dependencies
COPY client-app/ .
RUN npm install && npm run build

# ── Stage 2: Python API ─────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY pythonapi/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python API source
COPY pythonapi/ .

# Embed the compiled React SPA so FastAPI can serve it
COPY --from=frontend /build/dist ./static

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
