# syntax=docker/dockerfile:1

# audit ISSUE-036（#1377）：基础镜像 tag@sha256 双钉 —— tag 保持可读、
# digest 保证同 tag 漂移时构建面不变。更新 digest：
#   docker buildx imagetools inspect node:22-alpine | grep Digest
# 或 registry API（无 docker 环境时）。

# Stage 1: Frontend Dependencies
FROM node:26-alpine@sha256:6f08a523443ddedf52941874c203475b0ad503e859ad70cb26ab8be9a517dbd5 AS frontend-deps
WORKDIR /app/frontend
# pnpm 是唯一包管理器（audit5 #1083：npm lockfile 已删除）。corepack 随
# node:22 内置；pin pnpm@10 与 CI workflow 的 pnpm/action-setup 版本一致。
RUN corepack enable && corepack prepare pnpm@10.15.1 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
# node:22：jest-dom@7 / jsonlint-lines-primitives@2.0.3 等要求 node >=22（engine-strict）。
RUN pnpm install --frozen-lockfile

# Stage 2: Frontend Builder
FROM node:26-alpine@sha256:6f08a523443ddedf52941874c203475b0ad503e859ad70cb26ab8be9a517dbd5 AS frontend-builder
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@10.15.1 --activate
COPY --from=frontend-deps /app/frontend/node_modules ./node_modules
COPY frontend/. .
# transport goal E-F-2 (P0): inject the API base at build time (see Dockerfile.prod).
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# F-04: "" -> collab WebSocket derives ws(s)://<page host> at runtime (nginx
# /api/v1/ws/ location) instead of the dev fallback; override for a
# cross-origin backend.
ARG NEXT_PUBLIC_WS_URL=""
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm run build

# Stage 3: Backend Dependencies
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS backend-deps
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal-dev gdal-bin libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*
# audit ISSUE-033（#1340）：安装面以 uv 编译的 requirements.lock 为准 ——
# 同 SHA 重建得到同一依赖树；requirements.txt 仍随镜像供参考。
COPY requirements.txt requirements.lock ./
# audit ISSUE-034（#1349）：第三方镜像源是完整性信任面 —— 参数化为
# build ARG，默认回官方 PyPI；国内构建仍可 --build-arg 覆盖。
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.lock

# Stage 4: Backend Builder (carries deps + app code)
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS backend-builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal-dev gdal-bin libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=backend-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-deps /usr/local/bin /usr/local/bin
COPY requirements.txt ./
COPY main.py ./
COPY app/ ./app/
# #618-37: Pi RPC entry is vendor/pi/packages/coding-agent/dist/rpc-entry.js
# (pi_rpc_client.PI_RPC_ENTRY). compose api uses this stage as target and
# celery defaults to runner (COPY --from=backend-builder /app). Without this
# COPY, USE_NEW_AGENT cannot find the entry and silently falls back.
# Same path Dockerfile.prod copies; runtime needs vendor/ (submodule).
COPY vendor/ ./vendor/

# Stage 4b: Node runtime for the runner stage
# PLT-03（#1750）：Debian bookworm 的 `nodejs` apt 包是 18.x，而 Next 16
# standalone server 要求 >=20.9（frontend/package.json next 16.4.0；CVE
# 修复也要求新版）—— runner 阶段 apt 装 nodejs 会让默认 CMD 里的 node
# 进程起不来。从官方 node:26-bookworm-slim（与 python:3.12-slim 同 glibc
# 基线；alpine 的 musl 二进制不可用）只复制 node 单二进制 —— 运行时只需
# `node server.js` 与 Pi RPC 的 `node rpc-entry.js`，npm/corepack 不入
# 镜像以控制体积。digest 与 Dockerfile.prod 的 node-runtime 阶段一致。
FROM node:26-bookworm-slim@sha256:86f07bc9c5dce4578cf37e5a418b7bfc7f817cda25cde66e2b66e95ed86c4567 AS node-runtime

# Stage 5: Runner
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PYTHONPATH=/app

# Install system libs + Node.js
# 审计 I18：runner 只装 GDAL runtime libs（不带编译头），
# dev 头只存在于 backend-deps / backend-builder 阶段。
# 镜像减小 + 攻击面缩小。
# 注：使用 bookworm 包名（libgdal32 libgeos-c1v5 libproj25），与 Dockerfile.prod 一致；
# 之前误用 trixie 的 t64 后缀包名（libgdal32t64 libgeos3.11.1t64）在 bookworm 上不存在。
# 审计 P2：运行时不走 apt 的 nodejs（18.x，Next 16 起不来，#1750）——
# node 单二进制来自上方 node-runtime 阶段（与 Dockerfile.prod 的 PLT-03
# 方案一致）；npm/corepack 同样不入镜像。
# libstdc++6：官方 node 二进制链接的共享库（与 Dockerfile.prod 一致）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgdal32 libgeos-c1v5 libproj25 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# PLT-03（#1750）：官方 Node 单二进制（FROM node:26-bookworm-slim above）。
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

# Copy frontend standalone output.
# Next standalone 的 server.js 相对自身目录解析静态资源：
# <standalone>/.next/static 与 <standalone>/public（#1706：此前 static/public
# 落在 standalone 目录之外，容器内 /_next/static/* 与 /public 全部 404，
# 前端是无 JS/CSS 的空壳，而 /health 仍绿）。
COPY --from=frontend-builder /app/frontend/.next/standalone ./frontend/.next/standalone
COPY --from=frontend-builder /app/frontend/.next/static ./frontend/.next/standalone/.next/static
COPY --from=frontend-builder /app/frontend/public ./frontend/.next/standalone/public
# 构建期断言：摆错即构建失败，防复发（复制失败时 Docker COPY 本身会报错）。
RUN test -d frontend/.next/standalone/.next/static && test -d frontend/.next/standalone/public

# Copy backend app code
COPY --from=backend-builder /app ./
# Copy installed Python packages and binaries
COPY --from=backend-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-builder /usr/local/bin /usr/local/bin

# Create non-root user
RUN addgroup --system --gid 1001 appgroup && adduser --system --uid 1001 appuser

USER appuser

EXPOSE 3000 8000

# #1694 SEC-13：--ws-max-size 与 app/api/routes/ws.py:29 / ws_collab.py:51 的
# _MAX_INBOUND_BYTES=16*1024 对齐（应用层长度检查在整帧 receive_text() 缓冲之后，
# 协议层先限 size 才不会把超大帧整体读进内存）。与 Dockerfile.prod 的 CMD 一致。
CMD ["sh", "-c", "trap 'kill 0' TERM INT; uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws-max-size 16384 & node frontend/.next/standalone/server.js -p 3000 & wait"]
