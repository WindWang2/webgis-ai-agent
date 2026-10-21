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
#
# #1676（SEC-04）：口令**不进 argv**。此前 `exec redis-server ... --requirepass
# "$requirepass"` 把明文送进容器进程命令行 —— `docker inspect` 的 entrypoint、
# `docker top`、宿主机 `/proc/<pid>/cmdline`（任何能连 Docker socket 或读
# 宿主机 proc 的身份，含被攻破的低权容器经 socket 逃逸）都能取到生产 Redis
# 口令，而它同时是 celery broker + result backend + session store 的钥匙。
# deploy/redis.conf「本文件不存任何凭据」的努力也被这一行抵消。
#
# 现在：把 `requirepass <value>` 写进**运行时生成的 0400 文件**（tmpfs
# /dev/shm —— 不落盘、不进镜像层、容器停止即消失），再以 redis-server 的
# 第二个配置文件参数传入（redis-server 接受多个配置文件，后者覆盖前者）。
# 任何进程的命令行里都不出现口令；deploy/redis.conf 仍是唯一被跟踪的配置，
# 凭据只存在于 tmpfs 与容器 env（env 是 compose `:?` fail-fast 与
# healthcheck `$$REDIS_PASSWORD` 的共同前提，见服务定义注释）。
set -e

requirepass="${REDIS_PASSWORD:?ERROR: REDIS_PASSWORD is not set. Copy .env.Priv.example to .env.Priv and set REDIS_PASSWORD.}"

# tmpfs 运行时配置：umask 077 + chmod 400 双保险，仅文件所有者可读。
rtconf=/dev/shm/redis-runtime.conf
umask 077
printf 'requirepass %s\n' "$requirepass" > "$rtconf"
chmod 400 "$rtconf"

# Mirror the stock redis image entrypoint: drop to the redis user when root.
if [ "$(id -u)" = '0' ] && command -v gosu >/dev/null 2>&1; then
	find /data \! -user redis -exec chown redis '{}' +
	chown redis:redis "$rtconf"
	exec gosu redis redis-server /usr/local/etc/redis/redis.conf "$rtconf"
fi

exec redis-server /usr/local/etc/redis/redis.conf "$rtconf"
