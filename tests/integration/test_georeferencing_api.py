"""G48 WP10: Full HTTP API integration tests (R23-R25, R29, R30, R31, R40)."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Tuple

import httpx
import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.services.history_service_async import AsyncHistoryService
from app.services.durable_blob_store import reset_filesystem_blob_store
from app.services.georeferencing.refs import purge_session_georef_state


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap
    import app.services.mapspec.store as ms_store

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    monkeypatch.setattr(ms_store, "BASE_STORAGE_DIR", (data_dir / ".webgis-agent").resolve(), raising=False)
    reset_filesystem_blob_store()
    yield data_dir
    reset_filesystem_blob_store()


async def _create_owned_session(sid: str) -> Tuple[str, str]:
    async with AsyncSessionLocal() as db:
        conv, created = await AsyncHistoryService(db).get_or_create_conversation_with_created(
            sid, user_id=None
        )
        assert created is True
        tok = getattr(conv, "_issued_owner_token", None)
        assert isinstance(tok, str) and len(tok) > 10
        return sid, tok


def _err_code(resp) -> str:
    body = resp.json()
    for node in (body.get("data"), body.get("detail"), body):
        if isinstance(node, dict) and isinstance(node.get("code"), str):
            return node["code"]
    return ""


def _sample_png(w: int = 64, h: int = 48) -> bytes:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = np.linspace(30, 240, w, dtype=np.uint8)[None, :]
    arr[:, :, 1] = np.linspace(220, 40, h, dtype=np.uint8)[:, None]
    arr[:, :, 2] = 90
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_api_lifecycle_tenant_isolation_cas_tamper_download_and_partial_bind(
    isolated_storage,
):
    sid_a, tok_a = await _create_owned_session("g48-api-sess-a")
    sid_b, tok_b = await _create_owned_session("g48-api-sess-b")

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            h_a = {"X-Session-Token": tok_a}
            h_b = {"X-Session-Token": tok_b}

            # R29: Unauthenticated / cross-tenant access fails closed with 404 (SEC-08)
            no_tok = await client.get(f"/api/v1/georeferencing/sources?session_id={sid_a}")
            assert no_tok.status_code == 404
            wrong_tok = await client.get(
                f"/api/v1/georeferencing/sources?session_id={sid_a}", headers=h_b
            )
            assert wrong_tok.status_code == 404

            # Upload textured PNG in Session A
            up = await client.post(
                f"/api/v1/georeferencing/sources/upload?session_id={sid_a}",
                headers=h_a,
                files={"file": ("map_scan.png", _sample_png(64, 48), "image/png")},
            )
            assert up.status_code == 200, up.text
            up_body = up.json()
            source_id = up_body["source"]["source_id"]
            assert up_body["source"]["width"] == 64
            assert up_body["source"]["height"] == 48

            # Cross-session source image access denied
            assert (
                await client.get(
                    f"/api/v1/georeferencing/sources/{source_id}/image?session_id={sid_a}",
                    headers=h_b,
                )
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/v1/georeferencing/sources/{source_id}/image?session_id={sid_b}",
                    headers=h_b,
                )
            ).status_code == 404

            # Valid session fetches normalized PNG image
            src_img = await client.get(up_body["image_url"], headers=h_a)
            assert src_img.status_code == 200
            assert src_img.content.startswith(b"\x89PNG")

            # Create plan (revision starts at 1)
            cp = await client.post(
                f"/api/v1/georeferencing/plans?session_id={sid_a}",
                headers=h_a,
                json={
                    "session_id": sid_a,
                    "source_id": source_id,
                    "name": "API 集成配准",
                    "target_crs": "EPSG:3857",
                    "resampling": "bilinear",
                    "generate_cog": True,
                },
            )
            assert cp.status_code == 200, cp.text
            plan = cp.json()["plan"]
            plan_id = plan["plan_id"]
            assert plan["revision"] == 1

            # Cross-session plan lookup rejected
            assert (
                await client.get(
                    f"/api/v1/georeferencing/plans/{plan_id}?session_id={sid_b}",
                    headers=h_b,
                )
            ).status_code == 404

            # Prepare 4 known WGS84 -> EPSG:3857 affine GCPs:
            # map_x = 13370000 + 2.0 * px; map_y = 3530000 - 2.0 * py
            to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            pts_px = [(4.0, 4.0), (60.0, 4.0), (60.0, 44.0), (4.0, 44.0)]
            gcps_payload = []
            for idx, (px, py) in enumerate(pts_px, start=1):
                mx = 13370000.0 + 2.0 * px
                my = 3530000.0 - 2.0 * py
                lon, lat = to_4326.transform(mx, my)
                gcps_payload.append(
                    {
                        "gcp_id": f"p{idx}",
                        "label": f"CP-{idx}",
                        "px": px,
                        "py": py,
                        "wgs84_lon": lon,
                        "wgs84_lat": lat,
                        "role": "fit",
                        "enabled": True,
                    }
                )

            draft = await client.put(
                f"/api/v1/georeferencing/plans/{plan_id}/draft?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 1, "gcps": gcps_payload},
            )
            assert draft.status_code == 200, draft.text
            plan = draft.json()["plan"]
            assert plan["revision"] == 2
            assert plan["fit_metrics"]["status"] == "valid"
            assert plan["fit_metrics"]["fit_rmse"] < 1e-4

            # R23: CAS conflict when using stale revision=1
            stale_draft = await client.put(
                f"/api/v1/georeferencing/plans/{plan_id}/draft?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 1, "name": "Stale edit"},
            )
            assert stale_draft.status_code == 409
            assert _err_code(stale_draft) == "CAS_CONFLICT"

            # R24: Generate preview -> preview_stale becomes False -> fetch preview PNG
            prev = await client.post(
                f"/api/v1/georeferencing/plans/{plan_id}/preview?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 2},
            )
            assert prev.status_code == 200, prev.text
            prev_body = prev.json()
            assert prev_body["preview_available"] is True
            assert prev_body["plan"]["preview_stale"] is False
            prev_img = await client.get(prev_body["preview_image_url"], headers=h_a)
            assert prev_img.status_code == 200
            assert prev_img.content.startswith(b"\x89PNG")

            # Editing draft marks preview stale & GET preview/image rejects with PREVIEW_STALE (409)
            gcps_edited = list(gcps_payload)
            gcps_edited[0] = {**gcps_edited[0], "label": "CP-1-edited"}
            draft2 = await client.put(
                f"/api/v1/georeferencing/plans/{plan_id}/draft?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 2, "gcps": gcps_edited},
            )
            assert draft2.status_code == 200
            rev3 = draft2.json()["plan"]["revision"]
            assert rev3 == 3
            assert draft2.json()["plan"]["preview_stale"] is True

            stale_prev = await client.get(
                f"/api/v1/georeferencing/plans/{plan_id}/preview/image?session_id={sid_a}",
                headers=h_a,
            )
            assert stale_prev.status_code == 409
            assert _err_code(stale_prev) == "PREVIEW_STALE"

            # R25: CSV & JSON export -> import roundtrip into a second plan
            csv_exp = await client.get(
                f"/api/v1/georeferencing/plans/{plan_id}/gcps/export?session_id={sid_a}&fmt=csv",
                headers=h_a,
            )
            assert csv_exp.status_code == 200
            assert "CP-1-edited" in csv_exp.text
            json_exp = await client.get(
                f"/api/v1/georeferencing/plans/{plan_id}/gcps/export?session_id={sid_a}&fmt=json",
                headers=h_a,
            )
            assert json_exp.status_code == 200
            assert len(json.loads(json_exp.text)["gcps"]) == 4

            cp2 = await client.post(
                f"/api/v1/georeferencing/plans?session_id={sid_a}",
                headers=h_a,
                json={"session_id": sid_a, "source_id": source_id, "name": "Import target"},
            )
            plan2_id = cp2.json()["plan"]["plan_id"]
            imp_csv = await client.post(
                f"/api/v1/georeferencing/plans/{plan2_id}/gcps/import?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 1, "format": "csv", "content": csv_exp.text},
            )
            assert imp_csv.status_code == 200, imp_csv.text
            assert len(imp_csv.json()["plan"]["gcps"]) == 4
            assert imp_csv.json()["plan"]["fit_metrics"]["status"] == "valid"

            imp_json = await client.post(
                f"/api/v1/georeferencing/plans/{plan2_id}/gcps/import?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 2, "format": "json", "content": json_exp.text},
            )
            assert imp_json.status_code == 200
            assert imp_json.json()["plan"]["fit_metrics"]["fit_rmse"] < 1e-4

            # Malformed JSON GCP import -> 422 INVALID_GCP_COORDS (never 500)
            bad_json_imp = await client.post(
                f"/api/v1/georeferencing/plans/{plan2_id}/gcps/import?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 3, "format": "json", "content": '[{"px": 10}]'},
            )
            assert bad_json_imp.status_code == 422
            assert _err_code(bad_json_imp) == "INVALID_GCP_COORDS"

            # UTF-8 BOM CSV import + negative coords without escaping -> 200
            bom_csv = "\ufeffpx,py,map_x,map_y,label,note\n4,4,13370008,3529992,P1,ok\n60,4,13370120,3529992,P2,ok\n60,44,13370120,3529912,P3,ok\n"
            bom_imp = await client.post(
                f"/api/v1/georeferencing/plans/{plan2_id}/gcps/import?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 3, "format": "csv", "content": bom_csv},
            )
            assert bom_imp.status_code == 200, bom_imp.text

            # Non-finite / non-positive target_resolution_m rejected with 422
            bad_res_create = await client.post(
                f"/api/v1/georeferencing/plans?session_id={sid_a}",
                headers=h_a,
                json={"session_id": sid_a, "source_id": source_id, "target_resolution_m": 0.0},
            )
            assert bad_res_create.status_code == 422
            bad_res_draft = await client.put(
                f"/api/v1/georeferencing/plans/{plan2_id}/draft?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": 4, "target_resolution_m": -2.5},
            )
            assert bad_res_draft.status_code == 422

            # CSV formula escaping on label & note while preserving numeric coordinates
            formula_draft = await client.put(
                f"/api/v1/georeferencing/plans/{plan2_id}/draft?session_id={sid_a}",
                headers=h_a,
                json={
                    "expected_revision": 4,
                    "gcps": [
                        {"px": 4, "py": 4, "map_x": -100.5, "map_y": 200.0, "label": "=cmd|'/C calc'!A0", "note": "+1+1"},
                        {"px": 60, "py": 4, "map_x": 100.5, "map_y": 200.0, "label": "N2"},
                        {"px": 60, "py": 44, "map_x": 100.5, "map_y": -200.0, "label": "N3"},
                    ],
                },
            )
            assert formula_draft.status_code == 200
            formula_csv = await client.get(
                f"/api/v1/georeferencing/plans/{plan2_id}/gcps/export?session_id={sid_a}&fmt=csv",
                headers=h_a,
            )
            assert formula_csv.status_code == 200
            assert "'=cmd|'/C calc'!A0" in formula_csv.text
            assert "'+1+1" in formula_csv.text
            assert ",-100.5," in formula_csv.text

            # R30: Tampered source file fails closed before warp/execution
            # (#2194 后响应不再回显服务器路径 —— 篡改探针取内部存储记录的真实路径)
            from app.services.georeferencing.store import get_source as _get_source

            internal_src = _get_source(sid_a, source_id)
            assert internal_src is not None and internal_src.normalized_path
            norm_path = Path(internal_src.normalized_path)
            orig_bytes = norm_path.read_bytes()
            norm_path.write_bytes(b"TAMPERED_RASTER_BYTES")
            tampered_exec = await client.post(
                f"/api/v1/georeferencing/plans/{plan_id}/execute?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": rev3, "background": False},
            )
            assert tampered_exec.status_code == 409
            assert _err_code(tampered_exec) == "SOURCE_CORRUPT_OR_TAMPERED"
            # Restore source bytes for clean execution
            norm_path.write_bytes(orig_bytes)

            # R31: Clean sync execution -> GeoTIFF artifact + authorized download
            exec_res = await client.post(
                f"/api/v1/georeferencing/plans/{plan_id}/execute?session_id={sid_a}",
                headers=h_a,
                json={"expected_revision": rev3, "background": False, "idempotency_key": "api-exec-1"},
            )
            assert exec_res.status_code == 200, exec_res.text
            eb = exec_res.json()
            assert eb["status"] == "completed"
            artifact = eb["artifact"]
            assert artifact is not None
            artifact_id = artifact["artifact_id"]
            assert artifact["cog_verified"] is True

            # Wrong session/token cannot download artifact
            assert (
                await client.get(
                    f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid_a}",
                    headers=h_b,
                )
            ).status_code == 404

            # Authorized download returns exact bytes
            dl = await client.get(
                f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid_a}",
                headers=h_a,
            )
            assert dl.status_code == 200
            import hashlib

            assert hashlib.sha256(dl.content).hexdigest() == artifact["sha256"]

            # R40: MapSpec CAS conflict returns partial_success=True leaving GeoTIFF deliverable downloadable,
            # then valid revision succeeds
            bad_bind = await client.post(
                f"/api/v1/georeferencing/plans/{plan_id}/bind-layer?session_id={sid_a}",
                headers=h_a,
                json={"expected_mapspec_revision": 9999},
            )
            assert bad_bind.status_code == 200, bad_bind.text
            bb_bad = bad_bind.json()
            assert bb_bad["bound"] is False
            assert bb_bad["partial_success"] is True
            assert bb_bad["error_code"] == "MAPSPEC_BIND_CONFLICT"
            assert bb_bad["current_mapspec_revision"] == 0

            # GeoTIFF still downloadable despite bind CAS failure
            dl_after = await client.get(
                f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid_a}",
                headers=h_a,
            )
            assert dl_after.status_code == 200

            good_bind = await client.post(
                f"/api/v1/georeferencing/plans/{plan_id}/bind-layer?session_id={sid_a}",
                headers=h_a,
                json={"expected_mapspec_revision": 0, "layer_name": "配准扫描图"},
            )
            assert good_bind.status_code == 200, good_bind.text
            bb = good_bind.json()
            assert bb["bound"] is True
            assert bb["partial_success"] is False
            assert bb["mapspec_revision"] >= 1
            assert bb["mapspec"] is not None
            bound_layer = next(
                (ly for ly in bb["mapspec"].get("layers", []) if ly.get("id") == bb["layer_id"]),
                None,
            )
            assert bound_layer is not None
            assert bound_layer["raster_style"]["style_id"]
            assert "配准扫描图" in bb["message"]

            # R30: Cross-owner 404 isolation on status, cancel, bind-layer & delete_source
            assert (
                await client.get(
                    f"/api/v1/georeferencing/plans/{plan_id}/status?session_id={sid_a}",
                    headers=h_b,
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/georeferencing/plans/{plan_id}/cancel?session_id={sid_a}",
                    headers=h_b,
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/georeferencing/plans/{plan_id}/bind-layer?session_id={sid_a}",
                    headers=h_b,
                    json={"expected_mapspec_revision": 0},
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/georeferencing/sources/{source_id}?session_id={sid_a}",
                    headers=h_b,
                )
            ).status_code == 404

            # R40: Download blocked with 409 ARTIFACT_NOT_READY while a job is active on plan
            from app.core.database import SessionLocal
            from app.services.georeferencing.store import mutate_plan_cas
            from app.services.jobs.store import DurableJobStore

            with SessionLocal() as db:
                jrow = DurableJobStore.create_sync(
                    db,
                    task_type="georeference_warp",
                    kind="georeference",
                    display_name="active guard check",
                    session_id=sid_a,
                    parameters={},
                )
                DurableJobStore.mark_running_sync(db, jrow.id)
                db.commit()
                active_jid = str(jrow.id)
            mutate_plan_cas(
                sid_a,
                plan_id,
                expected_revision=None,
                mutator=lambda c: c.model_copy(update={"active_job_id": active_jid}),
            )
            incomplete_dl = await client.get(
                f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid_a}",
                headers=h_a,
            )
            assert incomplete_dl.status_code == 409
            assert _err_code(incomplete_dl) == "ARTIFACT_NOT_READY"
            mutate_plan_cas(
                sid_a,
                plan_id,
                expected_revision=None,
                mutator=lambda c: c.model_copy(update={"active_job_id": None}),
            )
    finally:
        purge_session_georef_state(sid_a)
        purge_session_georef_state(sid_b)


# ─── issue #2194：HTTP 响应不得回显服务器文件系统路径 ─────────────────────


@pytest.mark.asyncio
async def test_api_2194_responses_strip_server_paths(isolated_storage):
    """upload/plan/preview/status/execute/bind 全链响应剥离 normalized_path /
    preview_path / file_path（内部存储与下载仍依赖原路径，正例照常工作）。"""
    sid, tok = await _create_owned_session("g48-api-sess-paths")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        headers = {"X-Session-Token": tok}

        up = await client.post(
            f"/api/v1/georeferencing/sources/upload?session_id={sid}",
            headers=headers,
            files={"file": ("scan.png", _sample_png(64, 48), "image/png")},
        )
        assert up.status_code == 200, up.text
        up_body = up.json()
        assert up_body["source"]["normalized_path"] == ""
        source_id = up_body["source"]["source_id"]

        # 内部记录仍带真实路径（正例：下载/读图链路不受影响）
        from app.services.georeferencing.store import get_source

        internal = get_source(sid, source_id)
        assert internal is not None and internal.normalized_path

        cp = await client.post(
            f"/api/v1/georeferencing/plans?session_id={sid}",
            headers=headers,
            json={"session_id": sid, "source_id": source_id, "name": "paths",
                  "target_crs": "EPSG:3857"},
        )
        assert cp.status_code == 200, cp.text
        plan_id = cp.json()["plan"]["plan_id"]

        from pyproj import Transformer

        to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        gcps = []
        for idx, (px, py) in enumerate([(4.0, 4.0), (60.0, 4.0), (60.0, 44.0), (4.0, 44.0)], start=1):
            mx = 13370000.0 + 2.0 * px
            my = 3530000.0 - 2.0 * py
            lon, lat = to_4326.transform(mx, my)
            gcps.append({"gcp_id": f"p{idx}", "px": px, "py": py,
                         "wgs84_lon": lon, "wgs84_lat": lat, "role": "fit", "enabled": True})
        draft = await client.put(
            f"/api/v1/georeferencing/plans/{plan_id}/draft?session_id={sid}",
            headers=headers,
            json={"expected_revision": 1, "gcps": gcps},
        )
        assert draft.status_code == 200, draft.text

        prev = await client.post(
            f"/api/v1/georeferencing/plans/{plan_id}/preview?session_id={sid}",
            headers=headers,
            json={"expected_revision": 2},
        )
        assert prev.status_code == 200, prev.text
        prev_plan = prev.json()["plan"]
        assert "preview_path" not in prev_plan or prev_plan["preview_path"] in (None, "")

        # 内部计划记录仍携带 preview_path（正例：preview/image 端点照常 200）
        from app.services.georeferencing.store import get_plan

        internal_plan = get_plan(sid, plan_id)
        assert internal_plan is not None and internal_plan.preview_path
        prev_img = await client.get(prev.json()["preview_image_url"], headers=headers)
        assert prev_img.status_code == 200

        exe = await client.post(
            f"/api/v1/georeferencing/plans/{plan_id}/execute?session_id={sid}",
            headers=headers,
            json={"expected_revision": 2, "background": False},
        )
        assert exe.status_code == 200, exe.text
        exe_body = exe.json()
        assert exe_body["status"] == "completed"
        assert exe_body["artifact"]["file_path"] == ""

        status = await client.get(
            f"/api/v1/georeferencing/plans/{plan_id}/status?session_id={sid}",
            headers=headers,
        )
        assert status.status_code == 200, status.text
        st_body = status.json()
        assert st_body["plan"]["preview_path"] in (None, "")
        assert st_body["artifact"]["file_path"] == ""
        assert st_body["plan"]["latest_artifact"]["file_path"] == ""

        # 内部 artifact 记录仍带真实路径（正例：下载端点照常 200 + sha 头）
        internal_after = get_plan(sid, plan_id)
        assert internal_after is not None
        assert internal_after.latest_artifact is not None
        assert internal_after.latest_artifact.file_path
        artifact_id = st_body["artifact"]["artifact_id"]
        dl = await client.get(
            f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid}",
            headers=headers,
        )
        assert dl.status_code == 200
        assert dl.headers.get("X-Georef-Sha256")
        assert "file-path" not in "-".join(k.lower() for k in dl.headers.keys())

        bind = await client.post(
            f"/api/v1/georeferencing/plans/{plan_id}/bind-layer?session_id={sid}",
            headers=headers,
            json={"expected_mapspec_revision": 0},
        )
        assert bind.status_code == 200, bind.text
        bind_body = bind.json()
        if bind_body.get("artifact"):
            assert bind_body["artifact"]["file_path"] == ""


# ─── issue #2193：georeferencing 全链 sync-in-async 卸载 ──────────────────


@pytest.mark.asyncio
async def test_2193_upload_ingest_runs_off_event_loop_thread(isolated_storage, monkeypatch):
    """ingest_source_bytes 的大图解码/编码/SHA256 必须在工作线程执行，
    绝不占用事件循环线程（issue #2193）。"""
    import threading

    import app.services.georeferencing.intake as intake_mod

    loop_tid = threading.get_ident()
    worker_tid = {"tid": None}
    real_sync = intake_mod._ingest_source_bytes_sync

    def _spy(**kwargs):
        worker_tid["tid"] = threading.get_ident()
        return real_sync(**kwargs)

    monkeypatch.setattr(intake_mod, "_ingest_source_bytes_sync", _spy)

    from app.services.georeferencing.service import upload_source

    big = _sample_png(512, 512)
    src = await upload_source(session_id="g48-2193-thread", filename="big.png", raw_bytes=big)
    assert src.source_id
    assert worker_tid["tid"] is not None and worker_tid["tid"] != loop_tid
    purge_session_georef_state("g48-2193-thread")


@pytest.mark.asyncio
async def test_2193_preview_status_download_off_event_loop(isolated_storage, monkeypatch):
    """preview 的 warp+PNG、status 的 DB 读、download 的 SHA256 校验都在
    工作线程执行（issue #2193；经真实 HTTP 路由面验证）。"""
    import threading

    import app.services.georeferencing.service as svc

    loop_tid = threading.get_ident()
    tids = {"preview": set(), "status": set(), "download": set()}
    real_preview, real_status = svc.generate_preview, svc.get_status
    real_download = svc.resolve_authorized_artifact_download

    def _preview_spy(**kwargs):
        tids["preview"].add(threading.get_ident())
        return real_preview(**kwargs)

    def _status_spy(*args, **kwargs):
        tids["status"].add(threading.get_ident())
        return real_status(*args, **kwargs)

    def _download_spy(*args, **kwargs):
        tids["download"].add(threading.get_ident())
        return real_download(*args, **kwargs)

    monkeypatch.setattr(svc, "generate_preview", _preview_spy)
    monkeypatch.setattr(svc, "get_status", _status_spy)
    monkeypatch.setattr(svc, "resolve_authorized_artifact_download", _download_spy)

    sid, tok = await _create_owned_session("g48-api-sess-threads")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        headers = {"X-Session-Token": tok}
        up = await client.post(
            f"/api/v1/georeferencing/sources/upload?session_id={sid}",
            headers=headers,
            files={"file": ("scan.png", _sample_png(64, 48), "image/png")},
        )
        assert up.status_code == 200, up.text
        source_id = up.json()["source"]["source_id"]
        cp = await client.post(
            f"/api/v1/georeferencing/plans?session_id={sid}",
            headers=headers,
            json={"session_id": sid, "source_id": source_id, "name": "threads"},
        )
        plan_id = cp.json()["plan"]["plan_id"]
        from pyproj import Transformer

        to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        gcps = []
        for idx, (px, py) in enumerate([(4.0, 4.0), (60.0, 4.0), (60.0, 44.0), (4.0, 44.0)], start=1):
            mx = 13370000.0 + 2.0 * px
            my = 3530000.0 - 2.0 * py
            lon, lat = to_4326.transform(mx, my)
            gcps.append({"gcp_id": f"p{idx}", "px": px, "py": py,
                         "wgs84_lon": lon, "wgs84_lat": lat, "role": "fit", "enabled": True})
        draft = await client.put(
            f"/api/v1/georeferencing/plans/{plan_id}/draft?session_id={sid}",
            headers=headers,
            json={"expected_revision": 1, "gcps": gcps},
        )
        assert draft.status_code == 200, draft.text

        prev = await client.post(
            f"/api/v1/georeferencing/plans/{plan_id}/preview?session_id={sid}",
            headers=headers,
            json={"expected_revision": 2},
        )
        assert prev.status_code == 200, prev.text

        exe = await client.post(
            f"/api/v1/georeferencing/plans/{plan_id}/execute?session_id={sid}",
            headers=headers,
            json={"expected_revision": 2, "background": False},
        )
        assert exe.status_code == 200, exe.text
        artifact_id = exe.json()["artifact"]["artifact_id"]

        st = await client.get(
            f"/api/v1/georeferencing/plans/{plan_id}/status?session_id={sid}",
            headers=headers,
        )
        assert st.status_code == 200, st.text

        dl = await client.get(
            f"/api/v1/georeferencing/artifacts/{artifact_id}/download?session_id={sid}",
            headers=headers,
        )
        assert dl.status_code == 200

    for name, seen in tids.items():
        assert seen, f"{name} never invoked"
        assert seen != {loop_tid}, f"{name} ran on the event loop thread"
