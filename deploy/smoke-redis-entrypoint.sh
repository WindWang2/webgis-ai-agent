#!/bin/sh
# Validate the production entrypoint with the production Redis image, before
# spending time on the application image build. No host ports are published.
set -eu

image=${1:?Usage: smoke-redis-entrypoint.sh REDIS_IMAGE}
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
container=
cleanup() {
    if [ -n "$container" ]; then
        docker rm -fv "$container" >/dev/null
    fi
}
trap cleanup EXIT

REDIS_PASSWORD=$(openssl rand -hex 24)
export REDIS_PASSWORD
container=$(docker run -d \
    --entrypoint /bin/sh \
    -e REDIS_PASSWORD \
    -v "$root/deploy/redis.conf:/usr/local/etc/redis/redis.conf:ro" \
    -v "$root/deploy/redis-entrypoint.sh:/usr/local/etc/redis/entrypoint.sh:ro" \
    "$image" /usr/local/etc/redis/entrypoint.sh)

ready=0
for _ in $(seq 1 30); do
    if docker exec "$container" sh -c \
        'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --raw ping' 2>/dev/null | grep -qx PONG; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != 1 ]; then
    echo 'Redis entrypoint did not become ready' >&2
    # Redis parser errors may echo a config line. Redact the disposable test
    # credential without placing it in the filtering process arguments.
    docker logs "$container" 2>&1 | awk \
        '{ gsub(ENVIRON["REDIS_PASSWORD"], "[REDACTED]"); print }' >&2
    exit 1
fi

# Exercise real authentication and base-config preservation, not string matches
# against the shell script. Check without printing the random credential.
docker exec "$container" sh -ec '
    fail() { echo "Redis smoke failed: $1" >&2; exit 1; }
    redis-cli --raw ping | grep -q "NOAUTH" || fail "unauthenticated PING was not rejected"
    export REDISCLI_AUTH="$REDIS_PASSWORD"
    [ "$(redis-cli --raw config get appendonly | tail -n 1)" = yes ] || fail "appendonly is not enabled"
    [ "$(redis-cli --raw config get maxmemory-policy | tail -n 1)" = noeviction ] || fail "maxmemory-policy is not noeviction"
    [ "$(stat -c %a /dev/shm/redis-runtime.conf)" = 400 ] || fail "runtime config mode is not 0400"
    redis_uid=$(id -u redis) || fail "Redis user is missing"
    [ "$redis_uid" != 0 ] || fail "Redis user has root UID"
    [ "$(stat -c %u /dev/shm/redis-runtime.conf)" = "$redis_uid" ] || fail "runtime config is not owned by Redis user"
    # /proc directory ownership is not the process UID (e.g. dumpability).
    # Check real/effective/saved/filesystem UIDs directly, all must be Redis.
    process_uids=$(awk "/^Uid:/ {print \$2, \$3, \$4, \$5}" /proc/1/status) || fail "cannot read Redis process identity"
    [ "$process_uids" = "$redis_uid $redis_uid $redis_uid $redis_uid" ] || fail "Redis process did not drop privileges"
    cmdline=$(tr "\000" " " < /proc/1/cmdline) || fail "cannot read Redis command line"
    [ -n "$cmdline" ] || fail "Redis command line is empty"
    case "$cmdline" in
        *"$REDIS_PASSWORD"*)
            fail "Redis password leaked into argv"
            ;;
    esac
'
echo 'Redis entrypoint smoke passed: authenticated, noeviction, private config, unprivileged process'
