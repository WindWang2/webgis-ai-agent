"""语义类映射服务面集成（Platform 11 / WP-E）。

核心断言：默认（未配置 encoder）一切语义面 typed 拒绝——平台不伪装
文本理解（GOAL Oracle 2 的多模态面）；配置 stub 后诚实标注。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.errors import MultimodalUnsupported


@pytest.fixture()
def service_stub(isolated_blobs, tmp_path):
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.service import ModelOpsService

    settings = ModelOpsSettings(
        registry_dir=tmp_path / "modelops-stubsvc", text_encoder="stub"
    )
    return ModelOpsService(settings)


def test_default_service_refuses_semantic_surface(service):
    caps = service.semantic_encoder_caps()
    assert caps == {"wired": False}
    with pytest.raises(MultimodalUnsupported, match="no text encoder wired"):
        service.register_semantic_classes(["water", "forest"])
    with pytest.raises(MultimodalUnsupported, match="no text encoder wired"):
        service.register_semantic_classes(["water"], replace=True)


def test_stub_encoder_wired_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELOPS_TEXT_ENCODER", "stub")
    monkeypatch.setenv("MODELOPS_REGISTRY_DIR", str(tmp_path / "modelops-stub"))
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.service import ModelOpsService

    settings = ModelOpsSettings.load()
    svc = ModelOpsService(settings)
    caps = svc.semantic_encoder_caps()
    assert caps["wired"] is True and caps["stub"] is True

    result = svc.register_semantic_classes(["water", "forest", "urban"])
    assert set(result["registered"]) == {"water", "forest", "urban"}
    assert result["encoder"]["stub"] is True

    from app.lib.modelops.multimodal import StubTextEncoder
    vec = StubTextEncoder().encode(["water"])[0]
    mapping = svc.semantic_zero_shot(vec.tolist(), top_k=3)
    assert mapping["top"][0]["class"] == "water"
    assert mapping["top"][0]["score"] > 0.99  # 原型 = 同 encoder 同文本
    assert mapping["encoder"]["stub"] is True


def test_invalid_encoder_env_falls_back_to_unwired(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELOPS_TEXT_ENCODER", "gpt-9000")
    from app.services.modelops.config import ModelOpsSettings

    settings = ModelOpsSettings(registry_dir=tmp_path / "modelops-bad")
    assert settings.text_encoder == ""  # 非法值保守回退（不接线）


def test_zero_shot_with_real_embedding_run(service_stub, synthetic_raster):
    """geoai embedding → 语义映射的组合路径（stub encoder 下端到端）。

    tiny-chip-embedder 是 6 维向量——组合必须使用同维 stub（不同 encoder
    族维度失配 = typed 拒绝，这正是契约；见上 service 层测试）。
    """
    from app.lib.modelops.multimodal import SemanticClassMap, StubTextEncoder
    from app.services.modelops.engine import InferenceRequest

    result = service_stub.run_inference(InferenceRequest(
        model_id="tiny-chip-embedder", source_uri=str(synthetic_raster),
        owner_scope={"session_id": "sem-e2e"}, task_type="embedding",
    ))
    assert result.status == "completed"
    smap = SemanticClassMap(StubTextEncoder(dimension=6))
    smap.register(["water", "forest"], replace=True)
    import json
    from pathlib import Path

    items = json.loads(
        Path(result.outputs["embeddings"]["path"]).read_text(encoding="utf-8")
    )
    assert items, "embedding run must produce per-chip vectors"
    for item in items[:3]:
        vec = np.asarray(item["vector"], dtype=np.float32).tolist()
        mapping = smap.map_embedding(vec, top_k=2)
        assert {t["class"] for t in mapping["top"]} <= {"water", "forest"}
        assert all(-1.0 <= t["score"] <= 1.0 for t in mapping["top"])
