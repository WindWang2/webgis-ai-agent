"""P3 review 回归：manifest 分节 redaction 不得静默丢节。"""
from __future__ import annotations

from app.services.modelops.manifest import build_inference_manifest


def _build(**overrides):
    kwargs = dict(
        descriptor_payload={"model_id": "m"},
        provider_payload={},
        input_payload={},
        preprocess_payload={},
        tile_plan_payload={},
        postprocess_payload={},
        reproject_payload=None,
        owner_scope={"session_id": "s"},
        task_type="semantic_segmentation",
        outputs=[{"role": "classes"}],
        perf={},
        reuse_key=None,
        reused=False,
        compatibility={},
        run_id="run-1",
        device_plan={},
    )
    kwargs.update(overrides)
    return build_inference_manifest(**kwargs)


def test_non_json_section_is_marked_not_silently_dropped():
    """tuple 等非 JSON 基类型节：保留可见标记（指纹按原值计算，不得丢节）。"""
    manifest = _build(outputs=({"role": "classes"},))
    assert manifest["outputs"] == {
        "redacted_type": "tuple",
        "note": "non-JSON section replaced with marker after redaction",
    }


def test_json_base_sections_unchanged():
    manifest = _build()
    assert manifest["outputs"] == [{"role": "classes"}]
    assert manifest["reproject"] is None
    assert "manifest_fingerprint" in manifest
