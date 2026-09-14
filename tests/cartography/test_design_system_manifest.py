"""design_system.py 投影契约测试（qc-loop round 1 覆盖率抬升 + 契约锁定）。

本模块是四个 registry + renderer 支持矩阵 + chart 词表的**单份只读投影**
（V4，无独立真相源），当前无生产调用方（qc-loop pool Q012 记录为孤儿），
但契约一旦被 Workflow/Agent 侧消费即成公共面。此处锁定：
- manifest 计数自洽（native + planned = 总模型数）且两次构建逐字节相等；
- requirement 投影不编造（未注册模型/组件/variant 一律 None）；
- validate_design_system 跨 registry 自检在种子数据上为空。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.design_system import (
    DESIGN_SYSTEM_SCHEMA_VERSION,
    build_design_system_manifest,
    resolve_component_requirement,
    resolve_map_model_requirement,
    validate_design_system,
)
from app.lib.cartography.model_library import get_map_model_registry


def test_manifest_counts_are_self_consistent():
    m = build_design_system_manifest()
    assert m["schemaVersion"] == DESIGN_SYSTEM_SCHEMA_VERSION == 4
    c = m["counts"]
    assert c["mapModels"] == (
        len(m["mapModels"]["native"]) + len(m["mapModels"]["planned"]))
    assert c["componentTypes"] == len(m["componentTypes"])
    assert c["compositionTemplates"] == len(m["compositionTemplates"])
    assert c["chartKinds"] >= 1
    ids = [d["id"] for d in m["componentTypes"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    for entry in m["rendererCapability"].values():
        assert isinstance(entry["renderers"], list)
        assert isinstance(entry["exporters"], list)


def test_manifest_projection_is_deterministic():
    assert build_design_system_manifest() == build_design_system_manifest()


def test_validate_design_system_is_clean_on_seed_registries():
    assert validate_design_system() == []


def test_map_model_requirement_projects_registered_model():
    reg = get_map_model_registry()
    native = reg.native_ids()
    assert native
    req = resolve_map_model_requirement(native[0])
    assert req is not None
    assert req.model_id == native[0]
    assert req.runtime_status
    assert isinstance(req.to_dict(), dict)
    # 未注册模型 → None（不编造）
    assert resolve_map_model_requirement("no-such-map-model__qc") is None


def test_graduated_model_gets_graduated_legend_needs():
    for model_id in get_map_model_registry().native_ids():
        model = get_map_model_registry().resolve(model_id)
        if model.classification in (
                "graduated", "quantiles", "equal_interval",
                "natural_breaks", "std_dev", "head_tail"):
            req = resolve_map_model_requirement(model_id)
            assert req is not None and req.legend_needs["kind"] == "graduated"
            return
    pytest.fail("种子模型库中不存在 graduated 族模型（契约无法验证）")


def test_component_requirement_projects_and_rejects_unknown():
    reg = get_component_registry()
    desc = sorted(reg.native_descriptors(), key=lambda d: d.id)[0]
    req = resolve_component_requirement(desc.type)
    assert req is not None
    assert req.descriptor_id == desc.id
    assert req.component_type == desc.type
    assert req.variant == desc.default_variant
    if len(desc.variants) > 1:
        other = next(v for v in desc.variants if v != desc.default_variant)
        assert resolve_component_requirement(desc.type, other) is not None
        assert resolve_component_requirement(
            desc.type, "no-such-variant__qc") is None
    assert resolve_component_requirement("no-such-component__qc") is None
