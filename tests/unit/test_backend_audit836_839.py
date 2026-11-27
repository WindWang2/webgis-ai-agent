"""Regression tests for audit-ff9a392 backend findings (#836-#839).

#836: /local-data/* handlers off the event loop (asyncio.to_thread seam).
#837: artifact lifecycle — session purge + age sweep reclaim exports/reports/
      uploads.
#838: MapSpecStore process caches invalidated on clear/discard; fp
      short-circuit requires the sidecar to be alive.
#839: login limiter counts failures only; create_all gated off Postgres.
"""

import time

import pytest


# ─── #836: local-data offload seam ──────────────────────────────────────


class TestAudit836LocalDataOffload:
    def test_handlers_call_to_thread(self):
        import inspect

        from app.api.routes import local_data

        for fn_name in ("get_admin_boundary", "get_admin_children", "get_osm_features"):
            src = inspect.getsource(getattr(local_data, fn_name))
            assert "asyncio.to_thread" in src, (
                f"{fn_name} must offload its sync SHP/GPKG work (#836)"
            )


# ─── #837: artifact lifecycle ───────────────────────────────────────────


class TestAudit837ArtifactLifecycle:
    @pytest.mark.asyncio
    async def test_sweep_removes_aged_exports_and_sidecars(self, tmp_path, monkeypatch):
        from app.services import artifact_lifecycle as al

        export_dir = tmp_path / "exports"
        export_dir.mkdir()
        monkeypatch.setattr("app.services.export_paths.exports_root", lambda: export_dir)
        old = export_dir / "old.png"
        old.write_bytes(b"x")
        old_sidecar = export_dir / "old.png.owner"
        old_sidecar.write_text("user1")
        # backdate
        aged = time.time() - 30 * 86400
        import os

        os.utime(old, (aged, aged))
        os.utime(old_sidecar, (aged, aged))
        fresh = export_dir / "fresh.png"
        fresh.write_bytes(b"y")

        result = await al.sweep_aged_artifacts()
        assert not old.exists() and not old_sidecar.exists()
        assert fresh.exists()
        assert result["exports_removed"] == 1

    @pytest.mark.asyncio
    async def test_purge_session_artifacts_removes_session_scoped(
            self, tmp_path, monkeypatch):
        """DB-backed families need a DB; exercise the export-independent parts
        through a fake session store seam — reports/uploads purge is best
        effort, so a missing DB must not raise (fault isolation)."""
        from app.services import artifact_lifecycle as al

        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        monkeypatch.setattr(al, "UPLOADS_DIR", uploads_dir)
        # no DB configured in this unit context — must not raise
        result = await al.purge_session_artifacts("sess-no-db")
        assert result["session_id"] == "sess-no-db"

    @pytest.mark.asyncio
    async def test_delete_route_wires_artifact_purge(self):
        import inspect

        from app.api.routes import chat

        src = inspect.getsource(chat.clear_session)
        assert "purge_session_artifacts" in src, (
            "session delete must purge session-keyed artifacts (#837)"
        )

    def test_periodic_sweep_wired(self):
        import inspect

        from app.main import _periodic_session_cleanup

        src = inspect.getsource(_periodic_session_cleanup)
        assert "sweep_aged_artifacts" in src


class TestArtifactLifecycleRemovalFailureRegressions:
    """P3: 删除失败不得先删 DB 行 / 边车空主件名不得中断导出清扫。"""

    @pytest.mark.asyncio
    async def test_sweep_survives_sidecar_named_like_the_suffix(
            self, tmp_path, monkeypatch):
        """名字恰为 ".owner"/".diagnostics.json" 的文件会让 with_name("")
        抛 ValueError（不被 except OSError 捕获），此前每轮导出清扫中断、
        导出只增不减。空主件名 = 无主件 = 超龄孤儿，应被清除。"""
        import os

        from app.services import artifact_lifecycle as al

        export_dir = tmp_path / "exports"
        export_dir.mkdir()
        monkeypatch.setattr("app.services.export_paths.exports_root",
                            lambda: export_dir)
        aged = time.time() - 30 * 86400
        odd_owner = export_dir / ".owner"
        odd_owner.write_text("u1")
        odd_diag = export_dir / ".diagnostics.json"
        odd_diag.write_text("{}")
        os.utime(odd_owner, (aged, aged))
        os.utime(odd_diag, (aged, aged))

        result = await al.sweep_aged_artifacts()
        assert result["exports_removed"] == 2
        assert not odd_owner.exists()
        assert not odd_diag.exists()

    @staticmethod
    def _seed_aged_report(tmp_path, monkeypatch, tag):
        import uuid
        from datetime import datetime, timedelta, timezone

        from app.core.database import Base, Engine, SessionLocal
        from app.models.report import Report
        from app.services import artifact_lifecycle as al

        Base.metadata.create_all(Engine)
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(al, "REPORT_DIR", reports_dir)
        sid = f"sess-retry-{tag}-{uuid.uuid4().hex}"
        rid = f"rep-{tag}-{uuid.uuid4().hex}"
        report_file = reports_dir / f"{rid}.pdf"
        report_file.write_bytes(b"pdf")
        with SessionLocal() as db:
            db.add(Report(
                id=rid, session_id=sid, title="t", format="pdf",
                status="completed", file_path=str(report_file),
                created_at=datetime.now(timezone.utc) - timedelta(days=30),
            ))
            db.commit()
        return al, Report, SessionLocal, sid, rid, report_file

    @pytest.mark.asyncio
    async def test_purge_keeps_report_row_when_confined_unlink_fails(
            self, tmp_path, monkeypatch):
        """unlink 失败且文件仍在 REPORT_DIR 内时必须保留 DB 行，否则残留
        文件无任何清扫入口；恢复后下一轮 purge 同时回收文件与行。"""
        al, Report, SessionLocal, sid, rid, report_file = self._seed_aged_report(
            tmp_path, monkeypatch, "purge")
        monkeypatch.setattr(al, "UPLOADS_DIR", tmp_path / "uploads")
        try:
            real_unlink = al._safe_unlink
            monkeypatch.setattr(al, "_safe_unlink", lambda path: False)
            result = await al.purge_session_artifacts(sid)
            assert result["reports_removed"] == 0
            assert report_file.exists()
            with SessionLocal() as db:
                assert db.get(Report, rid) is not None

            monkeypatch.setattr(al, "_safe_unlink", real_unlink)
            retried = await al.purge_session_artifacts(sid)
            assert retried["reports_removed"] == 1
            assert not report_file.exists()
            with SessionLocal() as db:
                assert db.get(Report, rid) is None
        finally:
            with SessionLocal() as db:
                row = db.get(Report, rid)
                if row is not None:
                    db.delete(row)
                    db.commit()

    @pytest.mark.asyncio
    async def test_sweep_keeps_report_row_when_confined_unlink_fails(
            self, tmp_path, monkeypatch):
        """sweep 侧同纪律：文件残留则保留超龄行，下一轮重试。"""
        al, Report, SessionLocal, _sid, rid, report_file = self._seed_aged_report(
            tmp_path, monkeypatch, "sweep")
        monkeypatch.setattr(al, "UPLOADS_DIR", tmp_path / "uploads")
        monkeypatch.setattr("app.services.export_paths.exports_root",
                            lambda: tmp_path / "exports")
        monkeypatch.setattr("app.lib.artifact_cache.ARTIFACT_DIR",
                            str(tmp_path / "artifacts"))
        monkeypatch.setattr(
            "app.services.project_artifact_promotion.content_store_root",
            lambda: tmp_path / "pstore")

        async def _noop_async():
            return {}

        monkeypatch.setattr(al, "_sweep_promotion_store", _noop_async)
        monkeypatch.setattr(al, "_sweep_retention_and_orphans", _noop_async)
        try:
            real_unlink = al._safe_unlink
            monkeypatch.setattr(al, "_safe_unlink", lambda path: False)
            result = await al.sweep_aged_artifacts()
            assert result["report_rows_removed"] == 0
            assert report_file.exists()
            with SessionLocal() as db:
                assert db.get(Report, rid) is not None

            monkeypatch.setattr(al, "_safe_unlink", real_unlink)
            retried = await al.sweep_aged_artifacts()
            assert retried["report_rows_removed"] == 1
            assert not report_file.exists()
            with SessionLocal() as db:
                assert db.get(Report, rid) is None
        finally:
            with SessionLocal() as db:
                row = db.get(Report, rid)
                if row is not None:
                    db.delete(row)
                    db.commit()


# ─── #838: MapSpecStore process cache invalidation ──────────────────────


class TestAudit838MapSpecStoreCache:
    @pytest.fixture()
    def store(self, tmp_path, monkeypatch):
        import app.services.mapspec.store as store_mod
        from app.services.mapspec.store import MapSpecStore

        monkeypatch.setattr(store_mod, "session_data_manager", _FakeSessionMgr())
        monkeypatch.setattr(store_mod, "BASE_STORAGE_DIR", tmp_path)
        return MapSpecStore()

    @pytest.mark.asyncio
    async def test_clear_invalidates_process_cache(self, store):
        spec = {"version": 1, "layers": [{"id": "a"}]}
        await store.save_mapspec("s1", spec)
        assert "s1" in store._persisted_fp and "s1" in store._persisted_obj
        await store.clear_session_files("s1")
        assert "s1" not in store._persisted_fp
        assert "s1" not in store._persisted_obj

    @pytest.mark.asyncio
    async def test_session_id_reuse_after_clear_persists(self, store):
        spec = {"version": 1, "layers": [{"id": "a"}]}
        await store.save_mapspec("s1", spec)
        await store.clear_session_files("s1")
        # same id reused with an equal spec must PERSIST (not short-circuit)
        await store.save_mapspec("s1", dict(spec))
        assert (store.get_session_dir("s1") / "mapspec.json").exists()
        assert "s1" in store._persisted_fp

    @pytest.mark.asyncio
    async def test_discard_invalidates_cache(self, store):
        spec = {"version": 1, "layers": [{"id": "a"}]}
        await store.save_mapspec("s1", spec)
        await store.discard_mapspec("s1")
        assert "s1" not in store._persisted_fp


class _FakeSessionMgr:
    """Minimal async seam satisfying MapSpecStore's usage."""

    async def set_map_state(self, sid, key, value, seq=None):
        return True

    async def get_map_state(self, sid):
        return {}

    async def get_map_spec_fingerprint(self, sid):
        return None

    async def set_map_spec_fingerprint(self, sid, fp):
        return True


# ─── #839: login limiter + create_all gate ──────────────────────────────


class TestAudit839LoginLimiter:
    @pytest.mark.asyncio
    async def test_successes_do_not_consume_failure_budget(self):
        from app.core.rate_limiter import MemoryRateLimiter

        limiter = MemoryRateLimiter()

        # six successful logins from one IP: the failure ledger stays empty
        for _ in range(6):
            # simulate the route's gate logic directly (no DB): count < 5
            assert await limiter.count("auth_login_fail:1.2.3.4", 300) < 5
            await limiter.is_allowed("auth_login_attempt:1.2.3.4",
                                     max_requests=30, window_seconds=300)
        assert await limiter.count("auth_login_fail:1.2.3.4", 300) == 0

    @pytest.mark.asyncio
    async def test_failures_lock_after_five(self):
        from app.core.rate_limiter import MemoryRateLimiter

        limiter = MemoryRateLimiter()
        for _ in range(5):
            await limiter.record("auth_login_fail:9.9.9.9", 300)
        assert await limiter.count("auth_login_fail:9.9.9.9", 300) >= 5

    def test_login_route_uses_fail_ledger(self):
        import inspect

        from app.api.routes import auth as auth_route

        src = inspect.getsource(auth_route.login)
        assert "auth_login_fail" in src and "limiter.record" in src


class TestAudit839CreateAllGate:
    def test_create_all_gated_for_postgres(self, monkeypatch):
        """init_db against a Postgres URL must NOT call create_all."""
        import app.core.database as db_mod

        calls = {"create_all": 0}

        class _FakeMeta:
            def create_all(self, bind):
                calls["create_all"] += 1

        monkeypatch.setattr(db_mod.settings, "DATABASE_URL",
                            "postgresql://u:p@localhost/db", raising=False)
        monkeypatch.setenv("ALLOW_CREATE_ALL_ON_POSTGRES", "")
        monkeypatch.setattr(db_mod, "Base", type("B", (), {"metadata": _FakeMeta()}))
        monkeypatch.setattr(db_mod, "_apply_runtime_migrations", lambda: None)
        db_mod.init_db()
        assert calls["create_all"] == 0, (
            "create_all must not run against Postgres outside Alembic (#839)"
        )

    def test_create_all_still_runs_for_sqlite(self, tmp_path, monkeypatch):
        import app.core.database as db_mod

        calls = {"create_all": 0}

        class _FakeMeta:
            def create_all(self, bind):
                calls["create_all"] += 1

        monkeypatch.setattr(db_mod.settings, "DATABASE_URL",
                            f"sqlite:///{tmp_path}/t.db", raising=False)
        monkeypatch.setattr(db_mod, "Base", type("B", (), {"metadata": _FakeMeta()}))
        monkeypatch.setattr(db_mod, "_apply_runtime_migrations", lambda: None)
        db_mod.init_db()
        assert calls["create_all"] == 1


def test_persisted_identity_cache_releases_evicted_and_expired_objects(monkeypatch):
    import gc
    import types
    import weakref
    from app.services.mapspec import store as module

    class Marker:
        pass

    now = [0.0]
    monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(module, "_PERSISTED_CACHE_LIMIT", 2)
    store = module.MapSpecStore()
    marker = Marker()
    reference = weakref.ref(marker)
    store._remember_persisted("cold", "fp1", {"marker": marker})
    del marker
    store._remember_persisted("hot", "fp2", {})
    store._cached_persisted("hot")
    store._remember_persisted("new", "fp3", {})
    gc.collect()
    assert reference() is None
    assert set(store._persisted_fp) == {"hot", "new"}
    now[0] = module._PERSISTED_CACHE_TTL + 1
    assert store._cached_persisted("hot") == (None, None)
    assert not store._persisted_obj and not store._persisted_fp
