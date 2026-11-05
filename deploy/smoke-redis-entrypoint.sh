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
    docker logs "$container" >&2
    exit 1
fi

# Exercise real authentication and base-config preservation, not string matches
# against the shell script. Check without printing the random credential.
docker exec "$container" sh -ec '
    redis-cli --raw ping | grep -q "NOAUTH"
    export REDISCLI_AUTH="$REDIS_PASSWORD"
    [ "$(redis-cli --raw config get appendonly | tail -n 1)" = yes ]
    [ "$(redis-cli --raw config get maxmemory-policy | tail -n 1)" = noeviction ]
    [ "$(stat -c %a /dev/shm/redis-runtime.conf)" = 400 ]
    [ "$(stat -c %U /dev/shm/redis-runtime.conf)" = redis ]
    [ "$(stat -c %U /proc/1)" = redis ]
    cmdline=$(tr "\000" " " < /proc/1/cmdline)
    [ -n "$cmdline" ]
    case "$cmdline" in
        *"$REDIS_PASSWORD"*)
            echo "Redis password leaked into argv" >&2
            exit 1
            ;;
    esac
'
echo 'Redis entrypoint smoke passed: authenticated, noeviction, private config, unprivileged process'
