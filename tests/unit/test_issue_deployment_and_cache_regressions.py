"""Behavioral regressions for the November issue verification pass."""
import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml


def test_redis_l1_compound_operations_are_thread_safe():
    from app.services.session_data_redis import RedisSessionStore

    store = RedisSessionStore(redis_url="redis://localhost:1/0")

    def exercise(worker):
        for index in range(300):
            session = f"s{index % 20}"
            store._l1_put(session, "map_state", {"worker": worker})
            store._l1_get(session, "map_state")
            store._l1_invalidate_session(session)
            if index % 17 == 0:
                store.clear_l1_cache()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(exercise, range(8)))
    assert len(store._l1_order) == len(set(store._l1_order))
    assert set(store._l1_order) == set(store._l1)


def test_explorer_concurrent_threads_share_supervised_loop():
    from app.tasks.explorer.task_chain import _run_async

    async def identify():
        await asyncio.sleep(0)
        return id(asyncio.get_running_loop())

    with ThreadPoolExecutor(max_workers=8) as pool:
        loops = list(pool.map(lambda _: _run_async(identify()), range(24)))
    assert len(set(loops)) == 1


@pytest.mark.asyncio
async def test_async_db_missing_driver_fails_before_yield(monkeypatch):
    from app.core import database

    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    with pytest.raises(RuntimeError, match="Async DB support not available"):
        await anext(database.get_async_db())


def test_compose_services_keep_volume_arrays_and_bounded_logs():
    root = Path(__file__).resolve().parents[2]
    for name in ("docker-compose.yml", "docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        stack = yaml.safe_load((root / name).read_text())
        for service in stack["services"].values():
            assert isinstance(service.get("volumes", []), list)
            assert service["logging"]["options"]["max-size"] == "10m"
            assert int(service["logging"]["options"]["max-file"]) <= 3


@pytest.mark.asyncio
async def test_legacy_excel_has_actionable_rejection():
    from app.adapters.gov.gov_data_adapter import GovDataAdapter
    from app.services.explorer.models import RawContent

    with pytest.raises(ValueError, match="convert.*xlsx"):
        await GovDataAdapter().parse(RawContent(
            data=b"legacy", content_type="application/vnd.ms-excel", encoding="utf-8",
        ))


def test_excel_values_are_streamed_and_formula_cache_used():
    import io
    import openpyxl
    from app.adapters.gov.gov_data_adapter import GovDataAdapter
    from app.services.explorer.models import RawContent

    workbook = openpyxl.Workbook()
    workbook.active.append(["name", "value"])
    workbook.active.append(["A", "=1+1"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    result = GovDataAdapter()._parse_excel(RawContent(
        data=buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        encoding="utf-8",
    ))
    assert result.rows == [{"name": "A", "value": None}]


def test_debt_ratchet_recognizes_numpy_assertions_and_ignores_fixtures(tmp_path):
    from scripts.debt_ratchet import collect_assertless_tests

    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_numpy.py").write_text(
        "import pytest\nimport numpy as np\n"
        "@pytest.fixture()\ndef test_data():\n    return [1]\n"
        "def test_numpy():\n    np.testing.assert_allclose([1], [1])\n"
        "def test_missing():\n    run()\n",
    )
    assert [item.detail for item in collect_assertless_tests(tmp_path)] == ["test_missing"]


def test_skill_integrity_cli_rejects_missing_and_tampered_locks(tmp_path):
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts/verify_skills_lock.py"
    agent = tmp_path / "agents/example"
    agent.mkdir(parents=True)
    (agent / "SKILL.md").write_text("original")
    lock = tmp_path / "skills-lock.json"

    def run():
        return subprocess.run([
            sys.executable, str(script), "--lock", str(lock),
            "--skills-dir", str(tmp_path / "runtime"),
            "--agent-skills-dir", str(agent.parent),
        ], capture_output=True, text=True, check=False)

    assert run().returncode == 1
    lock.write_text(json.dumps({
        "runtime_skills": {}, "skills": {"example": {
            "computedHash": hashlib.sha256(b"original").hexdigest(),
        }},
    }))
    assert run().returncode == 0
    (agent / "SKILL.md").write_text("tampered")
    assert run().returncode == 1


def test_pin_image_checks_registry_before_manifest_mutation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    for name, script in {
        "docker": "#!/bin/sh\nexit 1\n",
        "kustomize": '#!/bin/sh\ntouch "$PROBE_FILE"\n',
    }.items():
        path = tmp_path / name
        path.write_text(script)
        path.chmod(0o755)
    probe = tmp_path / "mutated"
    result = subprocess.run([
        "bash", str(root / "deploy/k8s/pin-image.sh"), "sha256:" + "1" * 64,
    ], env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "PROBE_FILE": str(probe)},
        capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert not probe.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_postgres_migration_lock_spans_migrations_and_releases_on_failure(monkeypatch, fail):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    import runpy
    import sqlalchemy
    from alembic import context
    from alembic.config import Config

    events = []
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    connection.__enter__.return_value = connection

    def execute(statement, *args):
        text = str(statement)
        if "pg_advisory_unlock" in text:
            events.append("unlock")
        elif "pg_advisory_lock" in text:
            events.append("lock")

    connection.execute.side_effect = execute
    connection.commit.side_effect = lambda: events.append("commit")
    connection.rollback.side_effect = lambda: events.append("rollback")
    engine = MagicMock()
    engine.connect.return_value = connection
    config = Config()

    def migrate():
        events.append("migrate")
        if fail:
            raise ValueError("migration failed")

    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture@localhost/fixture")
    monkeypatch.setattr(sqlalchemy, "engine_from_config", lambda *args, **kwargs: engine)
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "configure", lambda **kwargs: None)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", migrate)
    path = Path(__file__).resolve().parents[2] / "migrations/env.py"
    if fail:
        with pytest.raises(ValueError, match="migration failed"):
            runpy.run_path(str(path))
    else:
        runpy.run_path(str(path))
    assert events == ["lock", "commit", "migrate", "rollback", "unlock", "commit"]
