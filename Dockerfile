# syntax=docker/dockerfile:1

# ── Stage 1: build the React frontend ────────────────────────────────────────
FROM node:24-slim AS frontend
WORKDIR /app
# Install deps first (cache-friendly): copy manifests, then npm ci.
COPY package.json package-lock.json ./
RUN npm ci
# Build the SPA -> /app/dist
COPY . .
RUN npm run build

# ── Stage 2: Python API that also serves the built SPA ───────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app
# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# App source + built frontend
COPY src/ ./src/
COPY --from=frontend /app/dist ./dist
# Render (and most PaaS) inject $PORT; default to 8000 locally.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
