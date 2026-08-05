#!/bin/sh
# Redis container entrypoint for the secure production stack.
#
# Injects requirepass from the REDIS_PASSWORD env var at container start,
# then execs redis-server. This keeps credentials out of the tracked
# deploy/redis.conf (audit I4/I6) and gives every deployment path — local
# `docker compose up`, the CI Deploy Preview job, and the prod SSH deploy —
# identical behavior with a single source of truth: .env.Priv's
# REDIS_PASSWORD. Fails fast if the var is missing instead of booting an
# unauthenticated or mismatched instance.
set -e

requirepass="${REDIS_PASSWORD:?ERROR: REDIS_PASSWORD is not set. Copy .env.Priv.example to .env.Priv and set REDIS_PASSWORD.}"

# Mirror the stock redis image entrypoint: drop to the redis user when root.
if [ "$(id -u)" = '0' ] && command -v gosu >/dev/null 2>&1; then
	find /data \! -user redis -exec chown redis '{}' +
	exec gosu redis redis-server /usr/local/etc/redis/redis.conf --requirepass "$requirepass"
fi

exec redis-server /usr/local/etc/redis/redis.conf --requirepass "$requirepass"
