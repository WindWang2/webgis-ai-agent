"""Lakehouse V7 Wave 19 — 安全回归（跨租户 / 路径 / 秘密 / 资源闸）。"""
from __future__ import annotations

import pytest

pytest.importorskip("zarr")
pytest.importorskip("rasterio")

from app.services.lakehouse.cube_service import CubeServiceError
from app.services.lakehouse.data_object import (
    DataObjectError,
    normalize_owner_scope,
    publish_data_object,
    resolve_data_object,
)
from app.services.lakehouse.rs_cube import RSCubeError


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    reset_filesystem_blob_store()
    reset_content_store_root_cache()
    yield tmp_path
    reset_filesystem_blob_store()
    reset_content_store_root_cache()


# ── 跨租户隔离 ────────────────────────────────────────────────────────


def test_cross_session_ref_invisible(env):
    """owner 域外的 ref 不可见（V7 labeled 读与 V6 同闸）。"""
    from app.services.artifact_registry import cube_ref_exists
    from app.services.lakehouse.cube_service import (
        read_session_labeled_window,
    )

    # 未注册 ref：直接以他人 session 查询 → CUBE_REF_MISSING（404 族）。
    with pytest.raises(CubeServiceError, match="not alive"):
        import asyncio

        asyncio.run(read_session_labeled_window(
            "sess-other", "ref:cube/ghost", selection={"index_slices": {"y": [0, 1]}},
        ))
    _ = cube_ref_exists


def test_data_object_owner_isolation(env):
    scope_a = normalize_owner_scope(session_id="sess-a")
    obj = publish_data_object(
        {"d.bin": b"secret-bytes"}, kind="cog_raster", owner_scope=scope_a,
    )
    manifest = resolve_data_object(obj.data_object_id)
    from app.services.lakehouse.data_object import owner_scope_allows

    assert owner_scope_allows(manifest, session_id="sess-a") is True
    assert owner_scope_allows(manifest, session_id="sess-b") is False
    assert owner_scope_allows(manifest, project_id="proj-a") is False


# ── 路径 / 输入遏制 ───────────────────────────────────────────────────


def test_rs_source_outside_data_dir_rejected(env, tmp_path):
    outside = tmp_path / "outside.tif"
    outside.write_bytes(b"x")
    with pytest.raises(CubeServiceError, match="outside data dir"):
        import asyncio

        from app.services.lakehouse.cube_service import _resolve_time_source

        asyncio.run(_resolve_time_source("sess-x", str(outside)))


def test_rs_source_id_charset_guard(env):
    import asyncio

    from app.services.lakehouse.rs_cube import build_rs_cube

    with pytest.raises(RSCubeError, match="invalid session id"):
        asyncio.run(build_rs_cube("../evil", sources=[
            {"time": "t0", "source": "x.tif", "role": "optical", "band": "b"},
        ], title="bad"))


def test_virtual_rejects_non_id_children(env):
    from app.services.lakehouse.virtual_object import (
        VirtualObjectError,
        publish_virtual_object,
    )

    with pytest.raises(VirtualObjectError, match="data object ids"):
        publish_virtual_object(
            ["../../etc/passwd"], kind_label="evil",
            owner_scope=normalize_owner_scope(session_id="sess-a"),
        )


# ── 秘密与身份 ────────────────────────────────────────────────────────


def test_secrets_never_enter_manifest_or_location(env, monkeypatch):
    monkeypatch.setenv("WEBGIS_S3_SECRET_ACCESS_KEY", "super-secret-value")
    from app.services.s3_blob_store import s3_config_from_env

    assert s3_config_from_env()["secret_access_key"] == "super-secret-value"
    obj = publish_data_object(
        {"d.bin": b"x"}, kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-a"),
        producer={"capability": "c", "args": {"token": "super-secret-value"}},
    )
    manifest = resolve_data_object(obj.data_object_id)
    import json as _json

    dumped = _json.dumps(manifest)
    assert "super-secret-value" not in dumped  # producer redact 通道生效


# ── 资源闸（V7 新面）─────────────────────────────────────────────────


def test_publish_object_ids_cap(env):
    from app.services.lakehouse.project_publish import PublishError
    import asyncio

    with pytest.raises(PublishError, match="1..200"):
        async def _run():
            from app.services.lakehouse.project_publish import (
                publish_to_project,
            )

            await publish_to_project(
                None, session_id="s", project_id="p",
                object_ids=[f"{i:064x}" for i in range(201)],
                actor_id=None,
            )

        asyncio.run(_run())


def test_data_object_error_contract():
    err = DataObjectError("x")
    assert err.to_dict() == {
        "success": False, "code": "DATA_OBJECT_INVALID", "message": "x",
    }
