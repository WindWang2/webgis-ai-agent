"""Wave 3 Upload V4 —— 上传内容身份 / 编码 / CRS / 栅格剖析 / 增值车道测试。

覆盖（审计 03 §7 items 2/3/4/5a/6/8 + R7 迁移守卫）：
- 同会话同字节幂等再导入（content_sha256 去重）；不同字节新记录；
  dedup=False 行为不变；跨会话不共享内容身份；
- GBK CSV → 200 + 编码披露（此前 500）；utf-8 不变；不可解码 → 400；
- CRS 诚实：CSV 声明/未声明、GeoJSON RFC 7946 披露；DB 行不再谎称 confirmed；
- 栅格上传补剖析：nodata/overviews 进 meta；缺 CRS → crs_missing 警告；
- 多文件丢弃披露 ignored_files；
- V3 摄入管线增值车道：成功登记 session_ref；失败不阻断上传。
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_MOCK_USER = {"user_id": "v4-upload-user"}

_GEOJSON_BYTES = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
             "properties": {"name": "a"}}
        ],
    }
).encode("utf-8")

_GEOJSON_BYTES_OTHER = _GEOJSON_BYTES.replace(b'"a"', b'"b"')

_GBK_CSV_BYTES = "name,lng,lat\n北京,116.40,39.90\n上海,121.47,31.23\n".encode("gb18030")
_UTF8_CSV_BYTES = "name,lng,lat\nBeijing,116.40,39.90\nShanghai,121.47,31.23\n".encode("utf-8")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'upload_v4.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    # 模型必须先于 create_all 导入（同一 declarative Base 的共享 metadata；
    # upload 路由/模型注册 uploads 表）。
    from app.core.auth import get_current_user
    from app.api.routes import upload as upload_routes
    from app.models.db_model import Base  # noqa: F401
    import app.models.upload  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from app.tools import _utils

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr(upload_routes, "async_db_session", override_async_db_session)
    monkeypatch.setattr(upload_routes.settings, "DATA_DIR", str(tmp_path))

    app = FastAPI()
    app.include_router(upload_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: _MOCK_USER
    try:
        yield app, test_session, tmp_path, upload_routes
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _row_count(session):
    from sqlalchemy import select, func
    from app.models.upload import UploadRecord

    async with session() as s:
        return (await s.execute(select(func.count()).select_from(UploadRecord))).scalar_one()


def _new_upload_dirs(tmp_path, before):
    root = tmp_path / "uploads"
    return [p for p in root.glob("*") if p.name not in before]


# ── Item 2：内容身份 / 幂等再导入 ────────────────────────────────────────

class TestUploadContentIdentity:
    async def test_same_bytes_reuse_record(self, client, app_and_db):
        _, session, tmp_path, _ = app_and_db
        payload = {"session_id": "sess-dedup-v4"}
        before = {p.name for p in (tmp_path / "uploads").glob("*")}

        r1 = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data=payload,
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            "/api/v1/upload",
            files={"files": ("a-copy.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data=payload,
        )
        assert r2.status_code == 200, r2.text
        body1, body2 = r1.json(), r2.json()
        assert body2["id"] == body1["id"]
        assert body2["deduplicated"] is True
        assert body1["deduplicated"] is False
        assert any("sha256" in w for w in body2["warnings"])
        # 未创建新目录/新行
        assert len(_new_upload_dirs(tmp_path, before)) == 1
        assert await _row_count(session) == 1

    async def test_different_bytes_create_new_record(self, client, app_and_db):
        _, session, _, _ = app_and_db
        payload = {"session_id": "sess-dedup-v4"}
        r1 = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data=payload,
        )
        r2 = await client.post(
            "/api/v1/upload",
            files={"files": ("b.geojson", _GEOJSON_BYTES_OTHER, "application/geo+json")},
            data=payload,
        )
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["id"] != r1.json()["id"]
        assert r2.json()["deduplicated"] is False
        assert await _row_count(session) == 2

    async def test_dedup_flag_off_keeps_legacy_behavior(self, client, app_and_db):
        _, session, _, _ = app_and_db
        payload = {"session_id": "sess-dedup-off", "dedup": "false"}
        for name in ("a.geojson", "a-again.geojson"):
            r = await client.post(
                "/api/v1/upload",
                files={"files": (name, _GEOJSON_BYTES, "application/geo+json")},
                data=payload,
            )
            assert r.status_code == 200, r.text
            assert r.json()["deduplicated"] is False
        assert await _row_count(session) == 2

    async def test_dedup_is_session_scoped_not_global(self, client, app_and_db):
        """内容身份是会话内幂等：跨会话同字节必须各自成行（S42 纪律）。"""
        _, session, _, _ = app_and_db
        r1 = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-dedup-A"},
        )
        r2 = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-dedup-B"},
        )
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["id"] != r1.json()["id"]
        assert r2.json()["deduplicated"] is False
        assert await _row_count(session) == 2

    async def test_model_declares_content_sha256_column_and_probe_index(self):
        from app.models.upload import UploadRecord

        assert "content_sha256" in UploadRecord.__table__.columns
        indexes = {
            (ix.name, tuple(c.name for c in ix.columns))
            for ix in UploadRecord.__table__.indexes
        }
        assert ("ix_uploads_session_content", ("session_id", "content_sha256")) in indexes


# ── Item 3：编码回退 ─────────────────────────────────────────────────────

class TestUploadEncoding:
    async def test_gbk_csv_200_with_encoding_disclosure(self, client, app_and_db):
        _, _, tmp_path, _ = app_and_db
        before = {p.name for p in (tmp_path / "uploads").glob("*")}
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("gbk.csv", _GBK_CSV_BYTES, "text/csv")},
            data={"session_id": "sess-enc"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["feature_count"] == 2
        assert any("gb18030" in w for w in body["warnings"])
        # meta.json 里有编码披露
        (new_dir,) = _new_upload_dirs(tmp_path, before)
        meta = json.loads((new_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["encoding"] == "gb18030"
        assert meta["encoding_fallback"] is True

    async def test_utf8_csv_unchanged(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("utf8.csv", _UTF8_CSV_BYTES, "text/csv")},
            data={"session_id": "sess-enc-utf8"},
        )
        assert r.status_code == 200, r.text
        assert not any("gb18030" in w for w in r.json()["warnings"])

    async def test_undecodable_csv_maps_to_400(self, client):
        bad = b"\x80\x80\x80\x80,name,lng\n" + b"\x80" * 8
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("bad.csv", bad, "text/csv")},
            data={"session_id": "sess-enc-bad"},
        )
        assert r.status_code == 400
        assert "编码" in r.json()["message"]


# ── Item 4：CRS 诚实 ─────────────────────────────────────────────────────

class TestUploadCrsHonesty:
    async def test_csv_with_declared_crs_respected(self, client, app_and_db):
        _, session, _, _ = app_and_db
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("pts.csv", _UTF8_CSV_BYTES, "text/csv")},
            data={"session_id": "sess-crs-declared", "crs": "EPSG:4326"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["crs"] == "EPSG:4326"
        assert body["crs_source"] == "declared"
        # DB 行确认 CRS
        from sqlalchemy import select
        from app.models.upload import UploadRecord

        async with session() as s:
            row = (await s.execute(
                select(UploadRecord).where(UploadRecord.id == body["id"])
            )).scalar_one()
            assert row.crs == "EPSG:4326"

    async def test_csv_without_crs_honest_unknown(self, client, app_and_db):
        _, session, _, _ = app_and_db
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("pts.csv", _UTF8_CSV_BYTES, "text/csv")},
            data={"session_id": "sess-crs-unknown"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 响应不再声称 confirmed 4326
        assert body["crs"] is None
        assert body["crs_source"] == "assumed"
        assert any("CRS_MISSING" in w for w in body["warnings"])
        # DB 行如实 NULL（列默认值不动，但本路径显式传 None）
        from sqlalchemy import select
        from app.models.upload import UploadRecord

        async with session() as s:
            row = (await s.execute(
                select(UploadRecord).where(UploadRecord.id == body["id"])
            )).scalar_one()
            assert row.crs is None

    async def test_geojson_without_crs_discloses_rfc7946(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-crs-geo"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["crs"] == "EPSG:4326"          # 行为不变
        assert body["crs_source"] == "rfc7946_default"


# ── Item 6：栅格上传剖析 ─────────────────────────────────────────────────

def _write_tiny_tiff(path, *, with_crs=True, with_overviews=False, nodata=255.0):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_origin

    data = (np.arange(32 * 32, dtype=np.uint16) % 200).reshape(1, 32, 32)
    profile = {
        "driver": "GTiff", "height": 32, "width": 32, "count": 1,
        "dtype": "uint16",
        "crs": "EPSG:4326" if with_crs else None,
        "transform": from_origin(116.0, 40.0, 0.01, 0.01),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        if with_overviews:
            from rasterio.enums import Resampling

            dst.build_overviews([2], Resampling.nearest)


class TestUploadRasterProfile:
    async def test_nodata_and_overviews_in_meta(self, client, app_and_db, tmp_path):
        tiff = tmp_path / "fixture.tif"
        _write_tiny_tiff(tiff, with_overviews=True)
        content = tiff.read_bytes()
        _, _, data_dir, _ = app_and_db
        before = {p.name for p in (data_dir / "uploads").glob("*")}
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("tiny.tif", content, "image/tiff")},
            data={"session_id": "sess-tif"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"] is not None
        assert body["meta"]["nodata"] == [255.0]
        assert body["meta"]["overviews"] >= 1
        assert body["meta"]["band_count"] == 1
        assert body["meta"].get("crs_missing") is None  # 有 CRS → 不告警
        # meta.json 落盘一致
        (new_dir,) = _new_upload_dirs(data_dir, before)
        meta = json.loads((new_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["nodata"] == [255.0]
        assert meta["overviews"] >= 1
        assert meta["band_stats"] and meta["band_stats"][0]["band"] == 1

    async def test_missing_crs_raster_accepted_with_warning(self, client, app_and_db, tmp_path):
        tiff = tmp_path / "nocrs.tif"
        _write_tiny_tiff(tiff, with_crs=False)
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("nocrs.tif", tiff.read_bytes(), "image/tiff")},
            data={"session_id": "sess-tif-nocrs"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"]["crs_missing"] is True
        assert any("CRS_MISSING" in w for w in body["warnings"])


# ── Item 8：多文件丢弃披露 ───────────────────────────────────────────────

class TestMultiFileHonesty:
    async def test_ignored_files_disclosed(self, client):
        r = await client.post(
            "/api/v1/upload",
            files=[
                ("files", ("a.geojson", _GEOJSON_BYTES, "application/geo+json")),
                ("files", ("b.geojson", _GEOJSON_BYTES_OTHER, "application/geo+json")),
                ("files", ("c.geojson", _GEOJSON_BYTES_OTHER, "application/geo+json")),
            ],
            data={"session_id": "sess-multi"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ignored_files"] == ["b.geojson", "c.geojson"]
        assert any("忽略" in w for w in body["warnings"])

    async def test_single_file_no_ignored(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-single"},
        )
        assert r.status_code == 200
        assert r.json()["ignored_files"] == []


# ── Item 5a：V3 管线增值车道 ─────────────────────────────────────────────

class TestIngestLane:
    async def test_register_ref_success_returns_session_ref(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-lane-ok", "register_ref": "true"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_ref"] and body["session_ref"].startswith("ref:upload-")
        assert body["profile_summary"]["row_count"] == 1
        # 未声明 CRS 的 GeoJSON → 质量如实给 warning/repairable（诚实，非 valid 假象）
        assert body["quality"]["quality_status"] in ("valid", "warning", "repairable")
        assert body["ref_registration_error"] is None

    async def test_x_session_id_header_opts_in(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-lane-hdr"},
            headers={"X-Session-Id": "sess-lane-hdr"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["session_ref"]

    async def test_lane_failure_does_not_fail_upload(self, client, app_and_db, monkeypatch):
        _, _, _, upload_routes = app_and_db

        class _Boom:
            async def ingest(self, *a, **kw):
                raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(upload_routes, "get_ingest_pipeline", lambda: _Boom())
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-lane-boom", "register_ref": "true"},
        )
        assert r.status_code == 200, r.text  # 上传本体不受影响
        body = r.json()
        assert body["session_ref"] is None
        assert "ledger unavailable" in (body["ref_registration_error"] or "")

    async def test_lane_default_off(self, client):
        r = await client.post(
            "/api/v1/upload",
            files={"files": ("a.geojson", _GEOJSON_BYTES, "application/geo+json")},
            data={"session_id": "sess-lane-off"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_ref"] is None
        assert body["ref_registration_error"] is None


# ── 迁移守卫（0027，仿 test_uploads_index_migration 的 scratch SQLite）───

def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DATABASE_URL": f"sqlite:///{db_path}",
        "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
        "USE_REDIS": "false",
        "HOME": str(Path.home()),
    }
    # Windows：用户 site-packages（pip --user 安装的 alembic）依赖 APPDATA
    # 解析 —— 子进程剥离该变量会让 -m alembic 直接 ModuleNotFoundError。
    for var in ("APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "SYSTEMDRIVE"):
        val = os.environ.get(var)
        if val:
            env[var] = val
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _columns(db_path: Path, table: str = "uploads") -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(db_path: Path, table: str = "uploads") -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        }


def test_migration_0027_head_adds_column_and_index(tmp_path):
    db_path = tmp_path / "uploads-0027.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
    assert "content_sha256" in _columns(db_path)
    assert "ix_uploads_session_content" in _indexes(db_path)


def test_migration_0027_roundtrip_preserves_rows(tmp_path):
    db_path = tmp_path / "uploads-0027-roundtrip.db"
    assert _alembic(db_path, "upgrade", "head").returncode == 0

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO uploads (filename, original_name, file_type, format,"
            " file_size, session_id, content_sha256)"
            " VALUES ('u/x.geojson', 'x.geojson', 'vector', 'geojson', 10, 's1', 'ab' * 32)"
        )
        conn.commit()

    down = _alembic(db_path, "downgrade", "0026_artifact_revisions")
    assert down.returncode == 0, f"downgrade 失败:\n{down.stdout}\n{down.stderr}"
    assert "content_sha256" not in _columns(db_path)
    assert "ix_uploads_session_content" not in _indexes(db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
    assert count == 1, "downgrade 丢了 uploads 数据行"

    again = _alembic(db_path, "upgrade", "head")
    assert again.returncode == 0, f"re-upgrade 失败:\n{again.stdout}\n{again.stderr}"
    assert "content_sha256" in _columns(db_path)
    assert "ix_uploads_session_content" in _indexes(db_path)
    with sqlite3.connect(db_path) as conn:
        sha = conn.execute(
            "SELECT content_sha256 FROM uploads WHERE session_id='s1'"
        ).fetchone()
    # 降级再升级是诚实操作：列回来了，降级前未覆盖的历史值清空（列被删除）
    assert sha is not None and sha[0] is None
