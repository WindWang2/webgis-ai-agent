"""Issue #476: Alembic 必须真正接进部署路径。

审计结论：仓库有 16 个迁移 revision，Dockerfile.prod 也拷贝了 alembic.ini +
migrations/，但任何部署路径（compose / k8s / entrypoint）都不执行
`alembic upgrade head`。应用启动只走 init_db() → create_all，对**已存在**的
Postgres 不加列/索引 —— 存量生产库 schema 停留在首次创建状态，新特性上线即
"column does not exist"，且只在生产暴露（测试全部从全新 create_all 起步）。

本文件是结构守卫（与 test_critical_infra_hardening.py 的 I4/I7/I9 同款）：
  1. 镜像 entrypoint 在启动应用前执行 alembic upgrade head（可跳过）；
  2. Dockerfile.prod 装了该 entrypoint 并挂在 ENTRYPOINT 上；
  3. 两个生产 compose：api 跑迁移、celery-worker 跳过（单一迁移所有者，
     避免两个容器并发 upgrade 竞争 alembic_version）；
  4. k8s api Deployment 用 initContainer 跑迁移（k8s 的 command: 覆盖镜像
     ENTRYPOINT，entrypoint 方案在 k8s 不生效，必须显式 initContainer）；
  5. 迁移链产出与模型元数据一致（drift check；CI 的 db-migrations lane
     会把它指到真实 PostGIS）；
  6. 本地 dev（docker-compose.yml / 直接 uvicorn）不受影响 —— dev 镜像
     （Dockerfile）不装 entrypoint。
"""
from pathlib import Path

import pytest
import yaml

import os
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "deploy" / "docker-entrypoint.sh"


# ── 1. entrypoint 脚本 ───────────────────────────────────────────────────


def test_entrypoint_script_exists_and_is_executable_intent():
    assert ENTRYPOINT.exists(), "deploy/docker-entrypoint.sh 不存在"


def test_entrypoint_runs_alembic_before_exec():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "alembic upgrade head" in source, "entrypoint 必须执行 alembic upgrade head"
    assert 'exec "$@"' in source, "entrypoint 必须以 exec \"$@\" 启动原 CMD"
    # exec 必须在迁移之后（迁移失败不允许带旧 schema 起服务）
    assert source.index("alembic upgrade head") < source.index('exec "$@"')


def test_entrypoint_honors_skip_env_and_retries_transient_failures():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "SKIP_DB_MIGRATIONS" in source, "entrypoint 必须支持 SKIP_DB_MIGRATIONS 跳过"
    assert "DATABASE_URL" in source, "迁移失败信息必须能定位目标 DATABASE_URL（脱敏）"


# ── 1b. entrypoint 行为（真实执行脚本，桩掉 alembic）────────────────────


@pytest.fixture
def alembic_stub(tmp_path, monkeypatch):
    """PATH 上放一个记录调用的假 alembic；真实 alembic 一次都不该跑（慢且会改库）。"""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    calls_file = tmp_path / "alembic-calls.log"

    (stub_dir / "alembic").write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$ALEMBIC_CALLS_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (stub_dir / "alembic").chmod(0o755)
    return stub_dir, calls_file


def _run_entrypoint(tmp_path, stub_dir, calls_file, extra_env):
    env = {
        **os.environ,  # 继承 DATABASE_URL（monkeypatch.setenv 设在父进程）
        # python3 解析到当前解释器（探测脚本需要 sqlalchemy；系统 python3 可能没有）
        "PATH": f"{stub_dir}:{os.path.dirname(sys.executable)}:{os.environ.get('PATH', '')}",
        "ALEMBIC_CALLS_LOG": str(calls_file),
        "SKIP_DB_MIGRATIONS": "false",
        **extra_env,
    }
    return subprocess.run(
        ["/bin/sh", str(ENTRYPOINT), "echo", "app-started"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )


def _calls(calls_file):
    if not calls_file.exists():
        return []
    return [line for line in calls_file.read_text().splitlines() if line.strip()]


def test_entrypoint_fresh_db_runs_upgrade_only(tmp_path, alembic_stub, monkeypatch):
    """全新库（无表）：直接 upgrade head，不做 stamp。"""
    stub_dir, calls_file = alembic_stub
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fresh.db")
    result = _run_entrypoint(tmp_path, stub_dir, calls_file, {})
    assert result.returncode == 0, result.stderr
    assert _calls(calls_file) == ["upgrade head"]
    assert "app-started" in result.stdout


def test_entrypoint_legacy_db_stamps_then_upgrades(tmp_path, alembic_stub, monkeypatch):
    """存量 create_all 库（有表、无 alembic_version）：先 stamp head 收编再 upgrade。"""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.commit()

    stub_dir, calls_file = alembic_stub
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    result = _run_entrypoint(tmp_path, stub_dir, calls_file, {})
    assert result.returncode == 0, result.stderr
    calls = _calls(calls_file)
    assert calls == ["stamp head", "upgrade head"], calls
    assert "adopting legacy" in result.stderr


def test_entrypoint_managed_db_runs_upgrade_only(tmp_path, alembic_stub, monkeypatch):
    """已有 alembic_version 的库：不 stamp，只 upgrade（幂等）。"""
    import sqlite3

    db_path = tmp_path / "managed.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        conn.commit()

    stub_dir, calls_file = alembic_stub
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    result = _run_entrypoint(tmp_path, stub_dir, calls_file, {})
    assert result.returncode == 0, result.stderr
    assert _calls(calls_file) == ["upgrade head"]


def test_entrypoint_skip_env_runs_nothing(tmp_path, alembic_stub, monkeypatch):
    stub_dir, calls_file = alembic_stub
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/skip.db")
    result = _run_entrypoint(
        tmp_path, stub_dir, calls_file, {"SKIP_DB_MIGRATIONS": "true"}
    )
    assert result.returncode == 0, result.stderr
    assert _calls(calls_file) == []
    assert "app-started" in result.stdout


def test_entrypoint_fails_after_bounded_retries(tmp_path, alembic_stub, monkeypatch):
    """迁移持续失败：有界重试后拒绝起服务（不带旧 schema 上线）。"""
    stub_dir, calls_file = alembic_stub
    (stub_dir / "alembic").write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$ALEMBIC_CALLS_LOG"\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (stub_dir / "alembic").chmod(0o755)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fail.db")
    result = _run_entrypoint(
        tmp_path, stub_dir, calls_file, {"DB_MIGRATION_RETRIES": "2"}
    )
    assert result.returncode == 1
    assert len(_calls(calls_file)) == 2, "重试次数必须遵守 DB_MIGRATION_RETRIES"
    assert "refusing to start" in result.stderr


# ── 2. Dockerfile.prod ──────────────────────────────────────────────────


def test_dockerfile_prod_installs_and_uses_entrypoint():
    df = (REPO_ROOT / "Dockerfile.prod").read_text(encoding="utf-8")
    assert "deploy/docker-entrypoint.sh" in df, "Dockerfile.prod 必须拷贝 entrypoint"
    assert "docker-entrypoint.sh" in df.split("ENTRYPOINT")[1], (
        "ENTRYPOINT 必须经由 docker-entrypoint.sh（tini 之下）再启动 CMD"
    )
    # 审计 I9 的 trap-kill-wait CMD 语义不能被破坏 —— CMD 保持原样
    assert "trap 'kill 0' TERM INT" in df


# ── 3. 生产 compose：单一迁移所有者 ─────────────────────────────────────


def _compose_services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def _env_entries(env) -> list[str]:
    """Normalize compose environment (list-form or mapping-form) to KEY=VALUE strings."""
    if not env:
        return []
    if isinstance(env, dict):
        return [f"{k}={v}" for k, v in env.items()]
    return [str(e) for e in env]


def test_prod_compose_api_runs_migrations_celery_skips():
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        services = _compose_services(REPO_ROOT / name)
        api_env = _env_entries(services["api"].get("environment"))
        celery_env = _env_entries(services["celery-worker"].get("environment"))
        assert not any(
            e.split("=", 1)[0].strip() == "SKIP_DB_MIGRATIONS" for e in api_env
        ), f"{name}: api 必须运行迁移（不得设 SKIP_DB_MIGRATIONS）"
        assert any(
            e.split("=", 1)[0].strip() == "SKIP_DB_MIGRATIONS"
            and e.split("=", 1)[-1].strip().lower() == "true"
            for e in celery_env
        ), f"{name}: celery-worker 必须设 SKIP_DB_MIGRATIONS=true（api 是唯一迁移所有者）"


# ── 4. k8s：initContainer 跑迁移 ────────────────────────────────────────


def _k8s_api_deployment() -> dict:
    docs = list(
        yaml.safe_load_all(
            (REPO_ROOT / "deploy" / "k8s" / "02-api-deployment.yaml").read_text(encoding="utf-8")
        )
    )
    return next(d for d in docs if d and d.get("kind") == "Deployment")


def test_k8s_api_has_migration_init_container():
    dep = _k8s_api_deployment()
    pod = dep["spec"]["template"]["spec"]
    inits = pod.get("initContainers", [])
    assert any(
        "alembic" in " ".join(c.get("command", []) + c.get("args", []))
        for c in inits
    ), "api Deployment 必须有执行 alembic upgrade head 的 initContainer"


def test_k8s_migration_init_container_gets_env_and_writable_tmp():
    dep = _k8s_api_deployment()
    pod = dep["spec"]["template"]["spec"]
    init = next(
        c
        for c in pod.get("initContainers", [])
        if "alembic" in " ".join(c.get("command", []) + c.get("args", []))
    )
    env_from = init.get("envFrom", [])
    kinds = [
        key for ef in env_from for key in ("configMapRef", "secretRef") if key in ef
    ]
    assert "secretRef" in kinds, (
        "initContainer 必须能读到 DATABASE_URL（envFrom secretRef/configMapRef，与应用容器一致）"
    )
    # readOnlyRootFilesystem 下 SQLAlchemy/psycopg 需要可写 /tmp
    mounts = {m["name"]: m["mountPath"] for m in init.get("volumeMounts", [])}
    assert "/tmp" in mounts.values(), "initContainer 需要可写 /tmp（rootfs 只读）"
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    assert volumes[mounts_reverse(mounts, "/tmp")].get("emptyDir") is not None, (
        "/tmp 挂载必须来自 emptyDir 卷"
    )


def mounts_reverse(mounts: dict, mount_path: str) -> str:
    for name, path in mounts.items():
        if path == mount_path:
            return name
    raise AssertionError(f"no volume mounted at {mount_path}")


def test_k8s_celery_has_no_migration_init_container():
    """迁移所有者是 api —— celery 不重复跑（并发 upgrade 会竞争 alembic_version）。"""
    docs = list(
        yaml.safe_load_all(
            (REPO_ROOT / "deploy" / "k8s" / "03-celery-deployment.yaml").read_text(encoding="utf-8")
        )
    )
    dep = next(d for d in docs if d and d.get("kind") == "Deployment")
    inits = dep["spec"]["template"]["spec"].get("initContainers", [])
    assert not any(
        "alembic" in " ".join(c.get("command", []) + c.get("args", [])) for c in inits
    ), "celery Deployment 不应带 alembic initContainer"


# ── 6. 迁移链产出 vs 模型元数据（drift check，#476 验收项）──────────────
#
# 默认在隔离的临时 SQLite 上跑 alembic upgrade head 再比对（hermetic，
# 不碰 CI 的共享 DATABASE_URL）。CI 的 db-migrations lane 会对真实
# PostGIS 先 `alembic upgrade head`，再设 MIGRATION_DRIFT_DB_URL 让本测试
# 比对真实 PG schema —— 模型与迁移的漂移因此在两条路径都被拦下。


def test_migrated_schema_matches_models(tmp_path):
    import os
    import sqlite3
    import subprocess

    override = os.environ.get("MIGRATION_DRIFT_DB_URL")
    if override:
        from sqlalchemy import create_engine, inspect

        engine = create_engine(override)
        insp = inspect(engine)
        try:
            migrated_tables = set(insp.get_table_names())
            columns_of = lambda t: {c["name"] for c in insp.get_columns(t)}  # noqa: E731
            index_cols_of = lambda t: {tuple(c["column_names"]) for c in insp.get_indexes(t)}  # noqa: E731
        finally:
            engine.dispose()
    else:
        db_path = tmp_path / "drift.db"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(REPO_ROOT),
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "DATABASE_URL": f"sqlite:///{db_path}",
                "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
                "USE_REDIS": "false",
                "HOME": str(Path.home()),
            },
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
        conn = sqlite3.connect(db_path)
        migrated_tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } - {"alembic_version"}
        conn.close()
        from sqlalchemy import create_engine, inspect

        engine = create_engine(f"sqlite:///{db_path}")
        insp = inspect(engine)
        columns_of = lambda t: {c["name"] for c in insp.get_columns(t)}  # noqa: E731
        index_cols_of = lambda t: {tuple(c["column_names"]) for c in insp.get_indexes(t)}  # noqa: E731
        engine.dispose()

    from app.core.database import Base

    import app.models.db_model  # noqa: F401  (registers tables)
    import app.models.report  # noqa: F401
    import app.models.upload  # noqa: F401

    model_tables = set(Base.metadata.tables.keys())

    drift = []
    for t in sorted(model_tables - migrated_tables):
        drift.append(f"模型有表但迁移链没建: {t}")
    for t in sorted(migrated_tables - model_tables):
        drift.append(f"迁移链建了表但模型没有: {t}")
    for t in sorted(model_tables & migrated_tables):
        mig_cols = columns_of(t)
        model_cols = {c.name for c in Base.metadata.tables[t].columns}
        if mig_cols != model_cols:
            drift.append(
                f"{t}: 迁移列={sorted(mig_cols)} 模型列={sorted(model_cols)}"
            )
        # 索引按列元组比对（create_all 与迁移的索引命名不同）。只断言
        # "模型声明的索引迁移链必须建出"这一方向 —— 迁移多建的历史索引
        # 不构成运行期漂移。
        mig_idx = index_cols_of(t)
        for model_idx in Base.metadata.tables[t].indexes:
            cols = tuple(c.name for c in model_idx.columns)
            if cols not in mig_idx:
                drift.append(f"{t}: 模型索引 {model_idx.name}{cols} 未被迁移链创建")

    assert not drift, "模型与迁移 schema 漂移:\n" + "\n".join(drift)


# ── 6b. CI：db-migrations lane（对真实 PostGIS 执行迁移链）─────────────


def _workflow_jobs() -> dict:
    doc = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "production.yml").read_text(encoding="utf-8")
    )
    return doc["jobs"]


def test_ci_has_db_migration_gate_against_postgis():
    jobs = _workflow_jobs()
    assert "db-migrations" in jobs, "CI 必须有对真实 PostGIS 执行 alembic 的 lane"
    job = jobs["db-migrations"]
    services = job.get("services", {})
    assert any(
        "postgis" in str(s.get("image", "")) for s in services.values()
    ), "db-migrations 必须用 postgis service container（0011 迁移只在 PostGIS 上可验证）"
    run_text = "\n".join(
        str(s.get("run", "")) + "\n" + "\n".join(f"{k}={v}" for k, v in s.get("env", {}).items())
        for s in job.get("steps", [])
    )
    assert "alembic upgrade head" in run_text, "lane 必须执行 alembic upgrade head"
    assert "MIGRATION_DRIFT_DB_URL" in run_text, (
        "lane 必须跑模型 vs 迁移 drift check（test_migrated_schema_matches_models）"
    )


def test_ci_db_migration_gate_is_release_blocking():
    jobs = _workflow_jobs()
    needs = jobs["release-gate"]["needs"]
    assert "db-migrations" in needs, "db-migrations 必须进入 release DAG"


# ── 7. 本地 dev 不受影响 ─────────────────────────────────────────────────


def test_dev_dockerfile_untouched_by_entrypoint():
    """dev 镜像（本地 compose 用）不装 entrypoint —— 本地 SQLite + create_all 语义不变。"""
    df = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "docker-entrypoint.sh" not in df
