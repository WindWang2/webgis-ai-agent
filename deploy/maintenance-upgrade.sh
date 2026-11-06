#!/bin/sh
# A schema/credential cutover requires a maintenance window. This only covers
# the writers in this Compose project; stop external writers before invoking it.
set -eu
if [ "$#" -ne 2 ]; then
    echo "usage: sh deploy/maintenance-upgrade.sh ENV_FILE COMPOSE_FILE" >&2
    exit 2
fi
upgrade_env_file=$1
upgrade_compose_file=$2
compose() { docker compose --env-file "$upgrade_env_file" -f "$upgrade_compose_file" "$@"; }

# Warm-stop: workers finish their current tasks before the migration starts.
# A forced stop still quiesces the writer; normal task recovery applies later.
compose stop --timeout 300 api celery-worker
running=$(compose ps --status running --services)
if printf '%s\n' "$running" | grep -Eq '^(api|celery-worker)$'; then
    echo "Old database writers are still running; migration refused." >&2
    exit 1
fi
compose up -d --wait db redis
umask 077
mkdir -p backups
backup_path=$(mktemp backups/pre-migration-XXXXXXXX.dump)
if ! compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_path"; then
    echo "Backup failed; migration refused (partial backup: $backup_path)." >&2
    exit 1
fi
test -s "$backup_path"
echo "Database backup: $backup_path" >&2
# A one-off container is the sole migrator. Do not persist this acknowledgement
# in the service environment; a later ordinary rolling start must fail closed.
compose run --rm --no-deps -e OWNER_TOKEN_MIGRATION_QUIESCED=1 api true
compose up -d
