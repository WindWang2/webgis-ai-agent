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
FROM python:3.12-slim AS backend-deps
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal-dev gdal-bin libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# Stage 4: Backend Builder (carries deps + app code)
FROM python:3.12-slim AS backend-builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal-dev gdal-bin libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=backend-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-deps /usr/local/bin /usr/local/bin
COPY requirements.txt ./
COPY main.py ./
COPY app/ ./app/

# Stage 5: Runner
FROM python:3.12-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PYTHONPATH=/app

# Install system libs + Node.js
# 审计 I18：runner 只装 GDAL runtime libs（不带编译头），
# dev 头只存在于 backend-deps / backend-builder 阶段。
# 镜像减小 + 攻击面缩小。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal32t64 libgeos3.11.1t64 libproj25 \
    nodejs npm && rm -rf /var/lib/apt/lists/*

# Copy frontend standalone output
COPY --from=frontend-builder /app/frontend/.next/standalone ./frontend/.next/standalone
COPY --from=frontend-builder /app/frontend/.next/static ./frontend/.next/static
COPY --from=frontend-builder /app/frontend/public ./frontend/public

# Copy backend app code
COPY --from=backend-builder /app ./
# Copy installed Python packages and binaries
COPY --from=backend-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-builder /usr/local/bin /usr/local/bin

# Create non-root user
RUN addgroup --system --gid 1001 appgroup && adduser --system --uid 1001 appuser

USER appuser

EXPOSE 3000 8000

CMD ["sh", "-c", "trap 'kill 0' TERM INT; uvicorn app.main:app --host 0.0.0.0 --port 8000 & node frontend/.next/standalone/server.js -p 3000 & wait"]
