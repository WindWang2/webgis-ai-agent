#!/bin/sh
# Render deploy/alertmanager.yml's receiver webhook URL (#1687).
#
# Alertmanager does NOT expand environment variables in its config file, so
# the tracked file carries the placeholder __ALERT_WEBHOOK_URL__ and the
# deploy flow substitutes the real URL on the runner before scp'ing the
# rendered file to ~/deploy/alertmanager.yml on the host (the bind-mount
# source of the alertmanager service in docker-compose.prod.secure.yml).
#
# Usage: sh deploy/render-alertmanager-config.sh [output-path]
#   output-path defaults to /tmp/alertmanager.yml (never the tracked file —
#   the repo copy must keep the placeholder).
#
# ALERT_WEBHOOK_URL (GitHub secret or var) is the webhook endpoint that
# receives alert notifications. When it is unset the placeholder is replaced
# with an RFC 2606 .invalid host: delivery fails LOUDLY in the alertmanager
# UI / logs instead of being silently dropped — the same honest-degradation
# choice #1636 made for the scrape target. Configure the secret/var and the
# next deploy picks it up.
#
# Python (not sed) does the substitution: a webhook URL may contain & and
# other sed replacement metacharacters — the same warning
# deploy/ci-generate-env-priv.sh carries for secret values.
set -eu

SRC=deploy/alertmanager.yml
OUT=${1:-/tmp/alertmanager.yml}
URL=${ALERT_WEBHOOK_URL:-http://alertmanager-webhook.invalid/}

command -v python3 >/dev/null 2>&1 || {
    echo "render-alertmanager-config: python3 not found" >&2
    exit 1
}

python3 - "$SRC" "$OUT" "$URL" <<'PY'
import pathlib
import sys

src_path, out_path, url = sys.argv[1], sys.argv[2], sys.argv[3]
placeholder = "__ALERT_WEBHOOK_URL__"
src = pathlib.Path(src_path).read_text(encoding="utf-8")
assert placeholder in src, f"{src_path} 丢失 receiver URL 占位符 {placeholder}"
pathlib.Path(out_path).write_text(
    src.replace(placeholder, url), encoding="utf-8"
)
PY

echo "render-alertmanager-config: wrote $OUT (url=${URL%%://*}://… redacted)"
