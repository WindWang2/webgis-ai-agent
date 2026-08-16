#!/bin/sh
# ============================================================================
# WebGIS AI Agent 容器入口（Issue #476）
#
# 之前任何部署路径都不执行 Alembic：应用只靠 init_db() 的 create_all 引导
# —— 对全新库够用，但对**已存在**的 Postgres 一列不加（create_all 从不
# ALTER 已有表；app/core/database.py 的运行时迁移助手对非 SQLite 直接
# early-return，提示"Postgres 部署请用 Alembic"）。结果：升级部署后新
# 列/新索引缺失，只有生产库在运行期报 "column does not exist"。
#
# 本脚本在启动原 CMD 前执行 `alembic upgrade head`（幂等：已应用的
# revision 直接跳过）。迁移失败则容器退出 —— 带旧 schema 起服务只会把
# 失败推迟成运行期 500，编排层重试/回滚比静默漂移更诚实。
#
# 存量库收编（adoption）：本修复之前部署的库由 create_all 引导，没有
# alembic_version 表 —— 直接 upgrade 会在 initial revision 上撞
# "relation already exists"。对这种库先 `alembic stamp head` 收编（本仓库
# 模型与迁移保持等价，见 tests/test_job_migration.py 的列/索引守卫），之后
# 每次部署都走正常 upgrade。收编会打明显日志。
#
# 环境开关：
#   SKIP_DB_MIGRATIONS=true  跳过迁移（celery-worker 等非迁移所有者服务用；
#                            并发 upgrade 会竞争 alembic_version 表，迁移
#                            只由 api 容器 / k8s initContainer 执行一次）。
#   DB_MIGRATION_RETRIES     连接类失败的重试次数（默认 5），每次间隔 3s ——
#                            编排健康检查通过 ≠ 100% 可立刻认证。
# ============================================================================
set -e

log() {
    echo "[docker-entrypoint] $*" >&2
}

redact_url() {
    # 打日志不泄漏口令：scheme://user:***@host/db
    printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@/]+@#\1:***@#g'
}

# 探测目标库状态：输出 "fresh"（无业务表）/"legacy"（有业务表但无
# alembic_version）/"managed"（已有 alembic_version）/"error"。
# exit 0 恒定 —— 探测失败按 fresh 处理，交给 alembic upgrade 暴露真实错误。
_db_state() {
    DATABASE_URL="${DATABASE_URL:-}" python3 - <<'PYEOF'
import os
import sys

url = os.environ.get("DATABASE_URL", "")
if not url:
    print("error")
    sys.exit(0)
try:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
except Exception:
    # 连不上/方言缺失：交给随后的 alembic upgrade 报真错并重试
    print("error")
    sys.exit(0)

if "alembic_version" in names:
    print("managed")
elif names:
    print("legacy")
else:
    print("fresh")
PYEOF
}

if [ "${SKIP_DB_MIGRATIONS:-false}" = "true" ]; then
    log "SKIP_DB_MIGRATIONS=true — skipping schema migration"
    exec "$@"
fi

DB_MIGRATION_RETRIES="${DB_MIGRATION_RETRIES:-5}"
DB_URL_DISPLAY="$(redact_url "${DATABASE_URL:-<unset>}")"

state="$(_db_state)"
if [ "$state" = "legacy" ]; then
    log "existing schema without alembic_version (${DB_URL_DISPLAY})"
    log "adopting legacy create_all schema: alembic stamp head"
    alembic stamp head || log "WARNING: alembic stamp head failed — continuing to upgrade head"
fi

attempt=1
while [ "$attempt" -le "$DB_MIGRATION_RETRIES" ]; do
    if alembic upgrade head; then
        log "database schema is up to date (${DB_URL_DISPLAY})"
        exec "$@"
    fi
    log "alembic upgrade head failed (attempt ${attempt}/${DB_MIGRATION_RETRIES}, db=${DB_URL_DISPLAY})"
    attempt=$((attempt + 1))
    sleep 3
done

log "FATAL: schema migration failed after ${DB_MIGRATION_RETRIES} attempts — refusing to start with a drifted schema"
exit 1
