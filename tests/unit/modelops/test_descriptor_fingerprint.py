"""GeoModelDescriptor / InferenceFingerprint 契约测试（Wave 2 验收）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.modelops.descriptor import GeoModelDescriptor, REUSE_ELIGIBLE_SEED_POLICIES
from app.lib.modelops.fingerprint import build_reuse_key, software_env_fingerprint


def test_descriptor_roundtrip_and_immutability(tiny_desc_factory):
    d = tiny_desc_factory()
    payload = d.as_dict()
    d2 = GeoModelDescriptor.model_validate(payload)
    assert d2 == d
    with pytest.raises(ValidationError):
        d.model_version = "2.0.0"  # frozen：原地变更即拒


def test_descriptor_rejects_unknown_fields(tiny_desc_factory):
    with pytest.raises(ValidationError):
        tiny_desc_factory(vendor_marketing={"extra": "field"})


def test_descriptor_checksum_shape_required(tiny_desc_factory):
    with pytest.raises(ValidationError):
        tiny_desc_factory(checksum="not-a-sha")


def test_descriptor_version_collision_semantics(tiny_desc_factory):
    """同 identity 不同 checksum = 碰撞（registry 层拒绝；descriptor 自身只暴露 identity）。"""
    d1 = tiny_desc_factory(checksum="a" * 64)
    d2 = tiny_desc_factory(checksum="b" * 64)
    assert d1.identity == d2.identity
    assert d1.checksum != d2.checksum


def test_descriptor_secret_guard(tiny_desc_factory):
    """secret 词根键被拒（pydantic 包装了 typed SecretLeakGuardError）。"""
    with pytest.raises(ValidationError, match="secret"):
        tiny_desc_factory(provenance={"api_key": "sk-123"})


def test_descriptor_segmentation_requires_class_schema(tiny_desc_factory):
    with pytest.raises(ValidationError):
        tiny_desc_factory(class_schema=None)


def test_descriptor_band_order_length(tiny_desc_factory):
    with pytest.raises(ValidationError):
        tiny_desc_factory(input_bands=4)


def test_descriptor_unknown_task_rejected(tiny_desc_factory):
    with pytest.raises(ValidationError):
        tiny_desc_factory(task_types=("yolo",))


def test_descriptor_unseeded_policy_accepted_but_not_reuse_eligible(tiny_desc_factory):
    d = tiny_desc_factory(random_seed_policy="unseeded")
    assert d.random_seed_policy not in REUSE_ELIGIBLE_SEED_POLICIES


# ── fingerprint：逐字段失效性（R1-M3 验收）───────────────────────────


def _key(**overrides):
    base = dict(
        descriptor_payload={"checksum": "a" * 64, "model_version": "1.0.0"},
        provider_payload={"provider_ref": "p1", "semantic_version": "1.0.0"},
        input_content_sha256="c" * 64,
        preprocess_payload={"band_indices": [0, 1, 2]},
        tile_plan_payload={"chip": [16, 16], "tile_count": 4},
        postprocess_payload={"score_threshold": 0.5},
        owner_scope={"session_id": "s1"},
    )
    base.update(overrides)
    return build_reuse_key(**base)


def test_fingerprint_exact_match_stable():
    assert _key() == _key()


@pytest.mark.parametrize(
    "override",
    [
        {"descriptor_payload": {"checksum": "b" * 64, "model_version": "1.0.0"}},   # checksum
        {"descriptor_payload": {"checksum": "a" * 64, "model_version": "2.0.0"}},   # version
        {"provider_payload": {"provider_ref": "p2", "semantic_version": "1.0.0"}},  # provider 身份
        {"provider_payload": {"provider_ref": "p1", "semantic_version": "2.0.0"}},  # 语义版本
        {"input_content_sha256": "d" * 64},                                          # 输入内容
        {"preprocess_payload": {"band_indices": [2, 1, 0]}},                        # 预处理
        {"tile_plan_payload": {"chip": [8, 8], "tile_count": 4}},                   # tile plan
        {"postprocess_payload": {"score_threshold": 0.9}},                          # 阈值
        {"owner_scope": {"session_id": "s2"}},                                      # owner 隔离
    ],
)
def test_fingerprint_invalidation_per_field(override):
    assert _key() != _key(**override)


def test_fingerprint_software_env_changes_key():
    with_key = _key(include_software_env=True)
    without = _key(include_software_env=False)
    assert with_key != without
    assert isinstance(software_env_fingerprint(), str)
