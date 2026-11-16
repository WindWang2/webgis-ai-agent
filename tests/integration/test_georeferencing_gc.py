"""G48 WP10: Cross-session BlobStore refcounting & ArtifactRegistry GC protection tests (R08-R09)."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.core.config import settings
from app.schemas.georeferencing_schema import GcpInputItem
from app.services.artifact_registry import list_artifacts, sweep_statuses
from app.services.durable_blob_store import get_filesystem_blob_store, reset_filesystem_blob_store
from app.services.georeferencing.intake import ingest_source_bytes
from app.services.georeferencing.refs import blob_has_live_ref, blob_key_for_sha, purge_session_georef_state
from app.services.georeferencing.service import bind_mapspec_layer, create_plan, delete_source, execute_plan, revise_draft
from app.services.raster_authoring.service import current_revision
from app.services.session_data import session_data_manager


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap
    import app.services.mapspec.store as ms_store
    from app.services.raster_authoring.service import _engine

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    monkeypatch.setattr(ms_store, "BASE_STORAGE_DIR", (data_dir / ".webgis-agent").resolve(), raising=False)
    _engine().store._invalidate_process_cache("sess-gc-artifact-prot")
    reset_filesystem_blob_store()
    yield data_dir
    reset_filesystem_blob_store()


def _textured_png() -> bytes:
    arr = np.random.default_rng(7).integers(0, 255, (40, 40, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_r08_cross_session_blob_refcount_preserves_shared_and_cleans_last_release(isolated_env):
    sid_a = "sess-gc-shared-a"
    sid_b = "sess-gc-shared-b"
    png_bytes = _textured_png()

    try:
        src_a = await ingest_source_bytes(session_id=sid_a, filename="shared.png", raw_bytes=png_bytes)
        src_b = await ingest_source_bytes(session_id=sid_b, filename="shared.png", raw_bytes=png_bytes)
        assert src_a.original_sha256 == src_b.original_sha256
        assert src_a.normalized_sha256 == src_b.normalized_sha256

        sha_orig = src_a.original_sha256
        sha_norm = src_a.normalized_sha256
        store = get_filesystem_blob_store()

        assert blob_has_live_ref(sha_orig, sid_a)
        assert blob_has_live_ref(sha_orig, sid_b)
        assert store.get_blob(blob_key_for_sha(sha_orig)) == png_bytes

        # Delete Session A source -> Session B MUST still have intact blob
        await delete_source(sid_a, src_a.source_id)
        assert not blob_has_live_ref(sha_orig, sid_a)
        assert blob_has_live_ref(sha_orig, sid_b)
        assert store.get_blob(blob_key_for_sha(sha_orig)) == png_bytes
        assert store.get_blob(blob_key_for_sha(sha_norm)) is not None

        # Clear Session B via session_data_manager.clear_session -> last ref dropped, blob GC'd
        await session_data_manager.clear_session(sid_b)
        assert not blob_has_live_ref(sha_orig, sid_b)
        assert store.get_blob(blob_key_for_sha(sha_orig)) is None
        assert store.get_blob(blob_key_for_sha(sha_norm)) is None
    finally:
        purge_session_georef_state(sid_a)
        purge_session_georef_state(sid_b)


@pytest.mark.asyncio
async def test_r09_artifact_registry_protection_until_source_deleted(isolated_env):
    sid = "sess-gc-artifact-prot"
    try:
        src = await ingest_source_bytes(session_id=sid, filename="prot.png", raw_bytes=_textured_png())
        plan = create_plan(session_id=sid, source_id=src.source_id, name="GC Test", target_crs="EPSG:3857")
        gcps = [
            GcpInputItem(gcp_id="1", label="1", px=0.0, py=0.0, map_x=13370000.0, map_y=3530000.0),
            GcpInputItem(gcp_id="2", label="2", px=40.0, py=0.0, map_x=13370080.0, map_y=3530000.0),
            GcpInputItem(gcp_id="3", label="3", px=40.0, py=40.0, map_x=13370080.0, map_y=3529920.0),
            GcpInputItem(gcp_id="4", label="4", px=0.0, py=40.0, map_x=13370000.0, map_y=3529920.0),
        ]
        plan = revise_draft(session_id=sid, plan_id=plan.plan_id, expected_revision=1, gcps=gcps)
        res = await execute_plan(
            session_id=sid,
            plan_id=plan.plan_id,
            expected_revision=plan.revision,
            background=False,
        )
        art = res["artifact"]
        assert art is not None
        rref = art.raster_ref

        # Even BEFORE bind_mapspec_layer, active_georef_artifact_ids protects rref in sweep_statuses
        sw_pre = await sweep_statuses(sid)
        assert rref in sw_pre["valid"]
        assert rref not in sw_pre["stale"]
        assert rref not in sw_pre["expired"]

        # Bind into MapSpec -> remains valid
        rev = await current_revision(sid)
        await bind_mapspec_layer(session_id=sid, plan_id=plan.plan_id, expected_mapspec_revision=rev)
        sw_post = await sweep_statuses(sid)
        assert rref in sw_post["valid"]

        # Deleting source deletes session ref and marks artifact superseded
        await delete_source(sid, src.source_id)
        recs = await list_artifacts(sid)
        matching = [r for r in recs if r.artifact_id == rref]
        assert matching and matching[0].status == "superseded"
        assert await session_data_manager.get(sid, rref) is None
    finally:
        purge_session_georef_state(sid)
