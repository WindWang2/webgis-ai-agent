"""TC-09 / TC-10 / TC-11：测试套件隔离护栏。

* TC-10：DATABASE_URL 必须是每进程临时 SQLite（不是开发者的 ./data/webgis.db，
  也不是环境里泄漏进来的共享 Postgres）。
* TC-09：任何测试对 app.main.app.dependency_overrides 的修改在测试结束后被恢复。
* TC-11：CI 的单元 lane 打开离线 socket 闸（ADS_FORCE_OFFLINE=1）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_database_url_is_isolated_temp_sqlite():
    if os.environ.get("TEST_DATABASE_URL") or os.environ.get("REAL_SERVICES") == "1":
        return  # 显式 opt-in 的 lane 不适用
    url = os.environ["DATABASE_URL"]
    assert url.startswith("sqlite:///"), url
    db_path = Path(url[len("sqlite:///"):]).resolve()
    assert db_path != (REPO_ROOT / "data" / "webgis.db").resolve()
    assert str(db_path).startswith(str(Path(tempfile.gettempdir()).resolve())), db_path
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    assert f"webgis-test-db-{worker}-" in str(db_path), "每个 xdist worker 必须独享库文件"


def test_settings_database_url_matches_isolated_env():
    from app.core.config import settings

    assert settings.DATABASE_URL == os.environ["DATABASE_URL"]


def test_dependency_override_leak_is_reverted():
    from app.main import app
    from tests.conftest import _preserve_app_dependency_overrides

    def _dep():  # pragma: no cover - 仅作 key
        return None

    before = dict(app.dependency_overrides)
    with _preserve_app_dependency_overrides():
        app.dependency_overrides[_dep] = lambda: {"user_id": "leak"}
    assert _dep not in app.dependency_overrides
    assert app.dependency_overrides == before


def test_dependency_override_restore_keeps_preexisting_entries():
    from app.main import app
    from tests.conftest import _preserve_app_dependency_overrides

    def _pre():  # pragma: no cover
        return None

    app.dependency_overrides[_pre] = lambda: "pre"
    try:
        with _preserve_app_dependency_overrides():
            app.dependency_overrides.clear()
        assert _pre in app.dependency_overrides
    finally:
        app.dependency_overrides.pop(_pre, None)


def test_ci_unit_lanes_force_offline_and_drop_shared_postgres():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/production.yml").read_text(encoding="utf-8"))
    for job in ("test-backend", "order-randomization"):
        text = "\n".join(s.get("run", "") for s in wf["jobs"][job]["steps"])
        assert "ADS_FORCE_OFFLINE=1" in text, f"{job} 必须启用离线 socket 闸"
    backend_env = "\n".join(s.get("run", "") for s in wf["jobs"]["test-backend"]["steps"])
    assert "echo \"DATABASE_URL=" not in backend_env, "test-backend 不得把单元套件指向共享 Postgres"
