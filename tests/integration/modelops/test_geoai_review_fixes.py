"""评审修复回归测试（Phase 7 remediation）。

每条用例对应一个独立 review finding 的修复语义（P0/P1/P2），oracle
独立于被测实现（手导期望/计数包装/互斥构造）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


# ── P0：embedding cache 的 ROI 语义 ──────────────────────────────────


def _embed_request(uri, *, session, roi=None):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-chip-embedder",
        source_uri=str(uri),
        owner_scope={"session_id": session},
        task_type="embedding",
        roi_bbox=roi,
    )


def test_embedding_roi_cache_correct_and_no_cross_contamination(
    service, tmp_path, monkeypatch
):
    """ROI run：读取平移正确（cache 开 = cache 关同向量）；不同 ROI 不互污。"""
    import rasterio
    from rasterio.transform import from_origin

    # 128px 竖向梯度（上下半区向量必然不同）。
    src = tmp_path / "grad.tif"
    data = np.linspace(0.1, 0.9, 128, dtype=np.float32)
    arr = np.broadcast_to(data[:, None], (3, 128, 128)).copy()
    with rasterio.open(
        src, "w", driver="GTiff", width=128, height=128, count=3, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0.0, 128.0, 1.0, 1.0),
    ) as dst:
        dst.write(arr)

    def run(roi, session):
        result = service.run_inference(_embed_request(src, session=session, roi=roi))
        assert result.status == "completed"
        items = json.loads(
            Path(result.outputs["embeddings"]["path"]).read_text(encoding="utf-8")
        )
        return [tuple(item["vector"]) for item in items]

    top = run((0, 0, 128, 64), "roi-cache")
    # 同 ROI 重跑必须走 cache 且向量一致（屏蔽整 run reuse 以强制真重算路径）。
    monkeypatch.setattr(service._reuse, "lookup", lambda key, owner_scope: None)
    top_again = run((0, 0, 128, 64), "roi-cache")
    assert top_again == top, "cache-on replay must equal cold vectors (ROI shift)"
    # 不同 ROI（下半区）必须给不同向量，且不命中上半区条目。
    bottom = run((0, 64, 128, 128), "roi-cache")
    assert bottom and top and bottom[0] != top[0], (
        "disjoint ROIs must not share cached vectors (absolute-window key)"
    )


# ── P1：EmbeddingCache 同键重写 ─────────────────────────────────────


def test_embedding_cache_double_put_same_key(tmp_path):
    from app.services.modelops.embedding_cache import EmbeddingCache

    cache = EmbeddingCache(tmp_path)
    key = "a" * 64
    v1 = np.ones((4,), dtype=np.float32)
    v2 = np.full((4,), 2.0, dtype=np.float32)
    assert cache.put(key, v1, owner_scope={"session_id": "s"})
    # review fix：第二次写同键不得自毁条目/抛 FileNotFoundError。
    assert cache.put(key, v2, owner_scope={"session_id": "s"})
    got = cache.get(key, owner_scope={"session_id": "s"})
    assert got is not None
    np.testing.assert_array_equal(got, v2)
    assert cache.stats()["entries"] == 1


# ── P1：polygon + sidecar/reference 组合 fail-closed ────────────────


def test_polygon_with_sidecar_rejected():
    from app.lib.modelops.errors import PromptArtifactError
    from app.lib.modelops.geo_prompt import GeoPromptArtifact

    with pytest.raises(PromptArtifactError, match="cannot combine"):
        GeoPromptArtifact(
            polygons=(((2.0, 2.0), (5.0, 2.0), (5.0, 5.0), (2.0, 5.0)),),
            mask_ref={"path": "m.tif", "sha256": "a" * 64},
        )


# ── P1：text/labels 进复用指纹 ─────────────────────────────────────


def test_text_content_differentiates_reuse(service, synthetic_raster, monkeypatch):
    from app.lib.modelops.promptable import PromptSpec

    record = service._registry.resolve(
        "tiny-promptable-seg", None, session_id="txt-fp"
    )
    provider = service._providers.get(record.descriptor.provider_ref)
    caps = provider.capabilities()
    if "text" not in caps.prompt_modes:
        pytest.skip("seeded provider does not declare text prompts")

    def run(text):
        return service.run_inference(_seg_request(
            synthetic_raster, PromptSpec(text=text), session="txt-fp",
        ))

    first = run("water bodies")
    monkeypatch.setattr(service._reuse, "lookup", lambda key, owner_scope: None)
    second = run("water bodies")
    third = run("dense forests")
    assert first.status == second.status == third.status == "completed"
    assert second.reused, "identical text must reuse"
    assert not third.reused, "different text content must miss (fingerprint)"


def _seg_request(uri, prompt, *, session, **kw):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(uri),
        owner_scope={"session_id": session},
        prompt=prompt,
        **kw,
    )


# ── P2：候选缺失 fail-closed ───────────────────────────────────────


def test_provider_returning_no_candidates_fail_closed(
    service, synthetic_raster, monkeypatch
):
    from app.lib.modelops.errors import ProviderError
    from app.lib.modelops.promptable import PromptSpec

    record = service._registry.resolve(
        "tiny-promptable-seg", None, session_id="cand-none"
    )
    provider = service._providers.get(record.descriptor.provider_ref)
    orig_infer = provider.infer

    def strip_candidates(model, batch, ctx):
        out = orig_infer(model, batch, ctx)
        object.__setattr__(out, "mask_candidates", None)
        object.__setattr__(out, "candidate_scores", None)
        object.__setattr__(out, "candidate_sources", None)
        return out

    monkeypatch.setattr(provider, "infer", strip_candidates)
    with pytest.raises(ProviderError, match="returned no candidates"):
        service.run_inference(_seg_request(
            synthetic_raster, PromptSpec(points=((25.0, 15.0),)),
            session="cand-none", return_candidates=True,
        ))


# ── HTTP：artifact 路径门 + 无 session 模型列举 ────────────────────


@pytest.fixture()
def api_client(service, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: service
    )
    from app.core.config import settings as app_settings

    # DATA_DIR = tmp/data 子目录（gate 测试需要在 DATA_DIR 之外放文件）。
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    monkeypatch.setattr(app_settings, "DATA_DIR", str(data_root))

    async def _passthrough_bind(**kwargs):
        from fastapi import HTTPException
        from app.services.modelops.service import normalize_scope

        sid = (kwargs.get("session_id") or "") or None
        pid = (kwargs.get("project_id") or "") or None
        if isinstance(sid, str):
            sid = sid.strip() or None
        if isinstance(pid, str):
            pid = pid.strip() or None
        if sid and pid:
            raise HTTPException(status_code=400, detail="provide exactly one of session_id / project_id")
        if kwargs.get("require") and not sid and not pid:
            raise HTTPException(status_code=400, detail="session_id or project_id is required")
        if sid:
            return normalize_scope(session_id=sid)
        if pid:
            return normalize_scope(project_id=pid)
        return {}

    async def _wide_roots(db, scope):
        from pathlib import Path as _P
        from app.core.config import settings as _settings
        from app.services.modelops.service import get_modelops_service as _gms

        return [
            _P(_settings.DATA_DIR).resolve(),
            _P(_gms()._settings.registry_dir).resolve(),
        ]

    monkeypatch.setattr("app.api.routes.geoai._bind_owner_scope", _passthrough_bind)
    monkeypatch.setattr("app.api.routes.geoai._roots_for_scope", _wide_roots)

    from fastapi.testclient import TestClient

    from app.core.auth import create_access_token
    from app.main import app

    with TestClient(app) as client:
        client.headers.update({
            "Authorization": "Bearer "
            + create_access_token({"sub": "geoai-tester", "username": "geoai-tester", "role": "editor"})
        })
        yield client


def test_models_route_without_session_ok(api_client, synthetic_raster, tmp_path):
    _ = tmp_path
    resp = api_client.get("/api/v1/geoai/models")
    assert resp.status_code == 200
    assert any(m["model_id"] == "tiny-promptable-seg" for m in resp.json()["models"])


def test_artifact_reference_path_gate(api_client, synthetic_raster, tmp_path):
    import shutil

    # source 必须在 DATA_DIR 内；artifact 引用指向 DATA_DIR 之外。
    data_root = tmp_path / "data"
    inside_src = data_root / "synthetic.tif"
    shutil.copyfile(synthetic_raster, inside_src)
    outside = tmp_path / "outside.tif"
    import rasterio

    layer = np.zeros((100, 130), dtype=np.uint8)
    layer[10:30, 20:45] = 1
    with rasterio.open(
        outside, "w", driver="GTiff", width=130, height=100, count=1, dtype="uint8",
    ) as dst:
        dst.write(layer, 1)
    resp = api_client.post(
        "/api/v1/geoai/prompt-segment",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": str(inside_src),
            "artifact": {
                "reference_layer": {"uri": str(outside), "band": 1, "strategy": "nonzero"}
            },
            "session_id": "gate-s1",
        },
    )
    assert resp.status_code == 422
    assert "data directory" in json.dumps(resp.json(), ensure_ascii=False)
