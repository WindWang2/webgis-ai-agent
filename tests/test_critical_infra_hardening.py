"""PR 5 — 基础设施加固回归测试。

覆盖：
- I6: Alembic env.py 读 DATABASE_URL + initial revision 可 upgrade + downgrade
- I4: k8s ConfigMap 不再含 Secret 资源
- I7: Dockerfile.prod 用精确 COPY 而非 COPY . .
- I9: 两 Dockerfile 用 trap-kill-wait 模式
- I11: /metrics 端点暴露（main.py 接入 instrumentator）
- I28: dependabot.yml 存在
"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ── I6: Alembic ──────────────────────────────────────────────────────────


def test_i6_env_py_reads_database_url():
    """env.py 必须从 DATABASE_URL 环境变量读取并覆盖 alembic.ini 占位符。"""
    env_py = (REPO_ROOT / "migrations" / "env.py").read_text()
    assert "DATABASE_URL" in env_py, "env.py 必须读 DATABASE_URL 环境变量"
    assert "config.set_main_option" in env_py, "env.py 必须用 set_main_option 覆盖 url"


def test_i6_versions_dir_has_initial_revision():
    """migrations/versions/ 必须存在且包含 initial schema revision。"""
    versions_dir = REPO_ROOT / "migrations" / "versions"
    assert versions_dir.exists(), "migrations/versions/ 不存在"
    py_files = list(versions_dir.glob("*.py"))
    assert len(py_files) >= 1, f"versions/ 内无 .py revision: {py_files}"
    # 至少一个是 initial（down_revision == None）
    found_initial = False
    for f in py_files:
        text = f.read_text()
        if "down_revision" in text and (
            "down_revision: Union[str, Sequence[str], None] = None" in text
            or "down_revision = None" in text
        ):
            found_initial = True
            break
    assert found_initial, "未找到 down_revision=None 的 initial revision"


def test_i6_alembic_upgrade_then_downgrade_round_trip(tmp_path):
    """端到端：在临时 SQLite 上跑 upgrade head 然后 downgrade base，必须无错。"""
    db_path = tmp_path / "alembic_test.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}

    # upgrade head
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"upgrade head 失败:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"

    # 验证表已创建
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    expected = {"users", "conversations", "messages", "layers", "reports"}
    assert expected.issubset(tables), f"缺少表: {expected - tables}; 实际={tables}"

    # downgrade base
    result = subprocess.run(
        ["python", "-m", "alembic", "downgrade", "base"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"downgrade base 失败:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"

    # 验证表已删除（仅剩 alembic_version）
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "users" not in tables, "downgrade 后 users 表仍存在"
    assert "alembic_version" in tables, "alembic_version 应保留"


# ── I4: Secret 从 ConfigMap 移除 ─────────────────────────────────────────


def test_i4_configmap_no_plaintext_secret():
    """01-configmap.yaml 不应再含真正的 Secret 资源 / 明文凭证。

    注意：解释性注释里会出现 "stringData" 这个词（用于说明旧 bug），所以
    只看 yaml 实际的资源类型。
    """
    configmap = (REPO_ROOT / "deploy" / "k8s" / "01-configmap.yaml").read_text()
    # 不应再有 kind: Secret 资源
    assert "kind: Secret" not in configmap, "ConfigMap 仍含 Secret 资源"
    # 不应有旧弱密码的实际值
    assert "ChangeMeStrongPwd123" not in configmap, "ConfigMap 仍含旧弱密码"
    assert "your-secure-jwt-secret-key-min-32-chars" not in configmap, "ConfigMap 仍含旧 JWT 占位符"
    # 应有操作指引注释
    assert "kubectl create secret" in configmap


def test_i4_configmap_cors_not_wildcard():
    """ConfigMap 在生产环境不应使用 CORS_ORIGINS='*'。"""
    configmap = (REPO_ROOT / "deploy" / "k8s" / "01-configmap.yaml").read_text()
    # 不应有 CORS_ORIGINS: "*" 这一行（注释里的解释 OK，但不能在 data: 区）
    # 简单检查：去掉注释行后不应有
    non_comment = "\n".join(
        line for line in configmap.splitlines()
        if not line.strip().startswith("#")
    )
    assert 'CORS_ORIGINS: "*"' not in non_comment, "ConfigMap 生产 CORS 仍是通配符"


# ── I7: Dockerfile.prod 精确 COPY ────────────────────────────────────────


def test_i7_dockerfile_prod_no_copy_all():
    """Dockerfile.prod 不应用 COPY . . —— 会把 screenshots/进度文档带进 runner。"""
    dockerfile_prod = (REPO_ROOT / "Dockerfile.prod").read_text()
    # 不应有独立的 "COPY . ." 行（COPY --from=xxx 可以保留）
    for line in dockerfile_prod.splitlines():
        stripped = line.strip()
        if stripped.startswith("COPY") and stripped.endswith(". ."):
            pytest.fail(f"Dockerfile.prod 仍含 COPY . .: {stripped}")
    # 应有精确 COPY
    assert "COPY app/ ./app/" in dockerfile_prod
    assert "COPY main.py" in dockerfile_prod or "COPY main.py ./" in dockerfile_prod


def test_i7_dockerignore_excludes_screenshots_and_planning():
    """.dockerignore 应排除 screenshots 和内部规划文档。"""
    dockerignore = (REPO_ROOT / ".dockerignore").read_text()
    assert "screenshot" in dockerignore.lower()
    # 至少一些 planning artifacts
    assert "MEMORY.md" in dockerignore or "TODOS.md" in dockerignore or "findings.md" in dockerignore


# ── I9: trap-kill-wait ──────────────────────────────────────────────────


def test_i9_dockerfile_uses_trap_kill_wait():
    """两 Dockerfile 的 CMD 必须用 trap-kill-wait 模式正确传播信号。"""
    for dockerfile in ["Dockerfile", "Dockerfile.prod"]:
        text = (REPO_ROOT / dockerfile).read_text()
        # 必须含 trap 'kill 0' 和 wait
        assert "trap 'kill 0'" in text or 'trap "kill 0"' in text, (
            f"{dockerfile} CMD 未用 trap-kill-wait 模式"
        )
        assert "& wait" in text or '"wait"' in text, f"{dockerfile} CMD 缺 wait"


# ── I11: Prometheus endpoint ─────────────────────────────────────────────


def test_i11_main_py_wires_prometheus_instrumentator():
    """main.py 应注册 prometheus-fastapi-instrumentator 暴露 /metrics。"""
    main_py = (REPO_ROOT / "app" / "main.py").read_text()
    assert "prometheus_fastapi_instrumentator" in main_py or "Instrumentator" in main_py, (
        "main.py 未注册 Prometheus instrumentator"
    )
    assert "/metrics" in main_py, "main.py 未暴露 /metrics endpoint"


def test_i11_requirements_has_prometheus():
    """requirements.txt 应含 prometheus-fastapi-instrumentator。"""
    requirements = (REPO_ROOT / "requirements.txt").read_text()
    assert "prometheus-fastapi-instrumentator" in requirements


def test_i11_prometheus_yml_uses_correct_path():
    """prometheus.yml 的 metrics_path 必须是 /metrics。"""
    prom_yml = (REPO_ROOT / "deploy" / "prometheus.yml").read_text()
    assert "metrics_path: '/metrics'" in prom_yml or 'metrics_path: "/metrics"' in prom_yml
    # 不应残留旧的 /api/v1/metrics
    assert "/api/v1/metrics" not in prom_yml


# ── I28: Dependabot ──────────────────────────────────────────────────────


def test_i28_dependabot_configured():
    """.github/dependabot.yml 必须存在并覆盖至少 pip + npm。"""
    dependabot_path = REPO_ROOT / ".github" / "dependabot.yml"
    assert dependabot_path.exists(), ".github/dependabot.yml 不存在"

    text = dependabot_path.read_text()
    ecosystems = [
        line.strip().split('"')[1] for line in text.splitlines()
        if 'package-ecosystem:' in line and '"' in line
    ]
    assert "pip" in ecosystems, f"dependabot 未配置 pip: {ecosystems}"
    assert "npm" in ecosystems, f"dependabot 未配置 npm: {ecosystems}"
