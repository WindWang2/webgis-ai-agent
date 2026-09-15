"""Embedding cache 平台集成（Platform 11 / WP-D）。

oracle 独立性：命中/未命中由**计数 provider 包装**独立计量（不读被测
cache 的统计作唯一真值）；向量等值由两次运行的产物 JSON 直接对比。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.modelops.errors import ModelOpsError


def _embed_request(service_uri, *, session):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-chip-embedder",
        source_uri=str(service_uri),
        owner_scope={"session_id": session},
        task_type="embedding",
    )


@pytest.fixture()
def counting_provider(service, monkeypatch):
    """包装 tiny-chip-embedder 的 provider，计量真实推理的 chip 数。"""
    record = service._registry.resolve(
        "tiny-chip-embedder", None, session_id="embed-cache-it"
    )
    provider = service._providers.get(record.descriptor.provider_ref)
    calls = {"chips": 0}
    orig_infer = provider.infer

    def counting(model, batch, ctx):
        calls["chips"] += int(batch.pixels.shape[0])
        return orig_infer(model, batch, ctx)

    monkeypatch.setattr(provider, "infer", counting)
    return calls


def test_embedding_cache_resume_and_invalidation(
    service, synthetic_raster, counting_provider, monkeypatch
):
    # run 1：全 miss，真实推理发生，条目回填。
    result1 = service.run_inference(_embed_request(synthetic_raster, session="embed-cache-it"))
    assert result1.status == "completed"
    tiles = result1.manifest["performance"]["chips_total"]
    assert tiles > 0
    assert counting_provider["chips"] == tiles
    perf1 = result1.manifest["performance"]
    assert perf1["embed_cache_misses"] == tiles
    assert perf1["embed_cache_hits"] == 0
    stats = service.embedding_cache_stats()
    assert stats["enabled"] and stats["entries"] == tiles
    emb1 = json.loads(
        Path(result1.outputs["embeddings"]["path"]).read_text(encoding="utf-8")
    )

    # run 2：屏蔽整 run reuse（单测 cache 语义），必须全命中、零推理。
    monkeypatch.setattr(
        service._reuse, "lookup", lambda key, owner_scope: None
    )
    result2 = service.run_inference(_embed_request(synthetic_raster, session="embed-cache-it"))
    assert result2.status == "completed"
    assert counting_provider["chips"] == tiles  # 没有新增推理
    perf2 = result2.manifest["performance"]
    assert perf2["embed_cache_hits"] == tiles
    assert perf2["embed_cache_misses"] == 0
    emb2 = json.loads(
        Path(result2.outputs["embeddings"]["path"]).read_text(encoding="utf-8")
    )
    assert emb1 == emb2  # resume 产物逐字段一致

    # 部分失效（资产变更）→ 重算。
    from app.lib.data.fingerprints import sha256_of_file

    asset_sha = sha256_of_file(str(synthetic_raster))
    assert service.invalidate_embedding_cache(asset_sha=asset_sha) == tiles
    assert service.embedding_cache_stats()["entries"] == 0
    service.run_inference(_embed_request(synthetic_raster, session="embed-cache-it"))
    assert counting_provider["chips"] == 2 * tiles  # 全部重算


def test_invalidate_requires_argument(service):
    with pytest.raises(ModelOpsError, match="requires model_id or asset_sha"):
        service.invalidate_embedding_cache()


def test_embedding_cache_stats_shape(service):
    stats = service.embedding_cache_stats()
    assert stats["enabled"] is True
    assert {"entries", "bytes", "hits", "misses", "evictions", "digest_failures"} <= set(stats)


def test_cache_disabled_when_entries_zero(synthetic_raster, tmp_path, monkeypatch):
    """MODELOPS_EMBED_CACHE_MAX_ENTRIES=0 → 禁用（engine 无 cache 路径）。"""
    monkeypatch.setenv("MODELOPS_EMBED_CACHE_MAX_ENTRIES", "0")
    monkeypatch.setenv("MODELOPS_REGISTRY_DIR", str(tmp_path / "modelops-disabled"))
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.service import ModelOpsService

    settings = ModelOpsSettings.load()
    assert settings.embed_cache_max_entries == 0
    svc = ModelOpsService(settings)
    assert svc.embedding_cache_stats() == {"enabled": False}
    result = svc.run_inference(_embed_request(synthetic_raster, session="disabled-it"))
    perf = result.manifest["performance"]
    assert perf["embed_cache_hits"] == 0 and perf["embed_cache_misses"] == 0
