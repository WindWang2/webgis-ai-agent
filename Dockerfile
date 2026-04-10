# syntax=docker/dockerfile:1

# Stage 1: Frontend Dependencies
FROM node:20-alpine AS frontend-deps
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Stage 2: Frontend Builder
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY --from=frontend-deps /app/frontend/node_modules ./node_modules
COPY frontend/. .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Stage 3: Backend Dependencies
FROM python:3.11-slim AS backend-deps
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Stage 4: Backend Builder
FROM python:3.11-slim AS backend-builder
WORKDIR /app
COPY --from=backend-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY requirements.txt ./
COPY main.py ./
COPY app/ ./app/
COPY mcp_servers.json ./
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5: Runner
FROM python:3.11-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PYTHONPATH=/app

# Install Node.js for frontend
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# Copy frontend build
COPY --from=frontend-builder /app/frontend/.next ./frontend/.next
COPY --from=frontend-builder /app/frontend/node_modules ./frontend/node_modules
COPY --from=frontend-builder /app/frontend/package.json ./frontend/package.json

# Copy backend
COPY --from=backend-builder /app ./

# Create non-root user
RUN addgroup --system --gid 1001 appgroup && adduser --system --uid 1001 appuser

USER appuser

EXPOSE 3000 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 & cd frontend && npx next start -p 3000"]
