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
set -eu

printf 'DB_PWD=%s\nREDIS_PASSWORD=%s\nJWT_SECRET_KEY=%s\nLLM_API_KEY=%s\nCORS_ORIGINS=%s\n' \
  "$DB_PWD" "$REDIS_PASSWORD" "$JWT_SECRET_KEY" "$LLM_API_KEY" \
  "${CORS_ORIGINS:-[\"https://your-domain.com\"]}" > .env.Priv
