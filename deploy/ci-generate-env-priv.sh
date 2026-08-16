#!/bin/sh
# Generate .env.Priv from the environment. Used by the deploy-prod and
# rollback SSH steps in production.yml so the credential mapping lives in one
# place instead of being duplicated per job.
#
# Credentials arrive as env vars fed from GitHub secrets by the caller step.
# A missing/empty value writes an empty line, and compose's ${VAR:?...}
# interpolation fails fast at `up` — never deploy a weak placeholder.
#
# Prefer this over sed-replacing .env.Priv.example: secret values may contain
# | / & etc., which would corrupt a sed replacement.
#
# #472: 在 GitHub Actions 上追加 WEBGIS_IMAGE（ghcr.io/<repo>:<sha>）。compose
# 通过 `--env-file .env.Priv` 插值 api/celery 的
# `image: ${WEBGIS_IMAGE:-webgis-ai-agent:local}`，使其解析为 `docker load`
# 出的 CI 镜像 —— 主机上没有构建上下文，缺了这一行 compose 只会构建失败或
# 静默复用过期本地镜像。GITHUB_REPOSITORY / GITHUB_SHA / REGISTRY 是 Actions
# 的默认注入（workflow 级 env.REGISTRY=ghcr.io），无需 workflow 额外传参；
# 已导出的 WEBGIS_IMAGE 环境变量优先（供 rollback 等流程指定别的 tag）。
set -eu

printf 'DB_PWD=%s\nREDIS_PASSWORD=%s\nJWT_SECRET_KEY=%s\nLLM_API_KEY=%s\nCORS_ORIGINS=%s\n' \
  "$DB_PWD" "$REDIS_PASSWORD" "$JWT_SECRET_KEY" "$LLM_API_KEY" \
  "${CORS_ORIGINS:-[\"https://your-domain.com\"]}" > .env.Priv

if [ -n "${WEBGIS_IMAGE:-}" ]; then
  printf 'WEBGIS_IMAGE=%s\n' "$WEBGIS_IMAGE" >> .env.Priv
elif [ -n "${GITHUB_SHA:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  repo_lc=$(printf '%s' "$GITHUB_REPOSITORY" | tr '[:upper:]' '[:lower:]')
  printf 'WEBGIS_IMAGE=%s/%s:%s\n' "${REGISTRY:-ghcr.io}" "$repo_lc" "$GITHUB_SHA" >> .env.Priv
fi
