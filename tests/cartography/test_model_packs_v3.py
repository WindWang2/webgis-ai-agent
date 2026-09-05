"""Model Packs V3 — 模型库域包扩容契约测试（ADR-0101 D1/D2）。

锁定：
- pack 加载：确定性、无 id 冲突、seed 不变；
- 诚实性：native 模型图层必须在前端运行时支持族内；planned 必须登记
  pitfalls；接受 artifact 类型必须真实注册；
- 降级链：fallback_model_id 可解析、无环；
- 推荐组件：必须是 ComponentType 词表成员；
- 别名：全局唯一指向。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.model_library import (
    MAPLIBRE_LAYER_TYPES,
    SEED_MAP_MODELS,
    get_map_model_registry,
    reset_map_model_registry,
    validate_model_library,
)
from app.lib.cartography.model_packs import FRONTEND_RUNTIME_LAYER_TYPES, MODEL_PACK_MODELS

pytestmark = pytest.mark.cartography


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_map_model_registry()
    yield
    reset_map_model_registry()


def test_pack_loads_and_extends_seed() -> None:
    reg = get_map_model_registry()
    seed_ids = {m.id for m in SEED_MAP_MODELS}
    # seed 全部仍在
    for mid in seed_ids:
        assert reg.get(mid) is not None, f"seed model {mid} 丢失"
    # pack 模型全部注册
    for model in MODEL_PACK_MODELS:
        assert reg.get(model.id) is not None, f"pack model {model.id} 未注册"
    # 总量显著扩容（14 seed + 30+ pack）
    assert reg.count == len(seed_ids) + len(MODEL_PACK_MODELS)
    assert len(MODEL_PACK_MODELS) >= 30


def test_pack_ids_unique_and_disjoint_from_seed() -> None:
    seed_ids = {m.id for m in SEED_MAP_MODELS}
    pack_ids = [m.id for m in MODEL_PACK_MODELS]
    assert len(pack_ids) == len(set(pack_ids)), "pack 内部 id 重复"
    assert not seed_ids & set(pack_ids), "pack 与 seed 撞 id"


def test_pack_alias_targets_resolve() -> None:
    reg = get_map_model_registry()
    for model in MODEL_PACK_MODELS:
        for alias in model.aliases:
            target = reg._alias.get(alias)
            assert target == model.id, f"别名 {alias} 未指向 {model.id}"
    # 别名不与任何模型 id 撞车
    all_ids = set(reg.all_ids)
    for alias in reg._alias:
        assert alias not in all_ids, f"别名 {alias} 与模型 id 冲突"


def test_native_models_use_frontend_runtime_layer_types() -> None:
    for model in MODEL_PACK_MODELS:
        assert model.maplibre_layer_type in MAPLIBRE_LAYER_TYPES
        if model.runtime_status == "native":
            assert model.maplibre_layer_type in FRONTEND_RUNTIME_LAYER_TYPES, (
                f"{model.id}: native 但图层 {model.maplibre_layer_type} "
                f"不在前端运行时支持族内（应 planned）"
            )


def test_planned_models_disclose_reasons() -> None:
    for model in MODEL_PACK_MODELS:
        if model.runtime_status == "planned":
            assert model.pitfalls_zh, f"{model.id}: planned 必须登记 pitfalls"
            assert any("planned" in p for p in model.pitfalls_zh), (
                f"{model.id}: pitfalls 须显式声明 planned 原因"
            )


def test_accepted_artifact_types_are_registered() -> None:
    from app.lib.gis.artifacts import get_artifact_type_registry

    artifact_reg = get_artifact_type_registry()
    for model in MODEL_PACK_MODELS:
        for at in model.accepted_artifact_types:
            assert artifact_reg.get(at) is not None, (
                f"{model.id}: accepted_artifact_type '{at}' 未注册（虚构契约）"
            )


def test_recommended_components_are_component_types() -> None:
    from typing import get_args

    from app.services.gis_harness.components import ComponentType

    valid = set(get_args(ComponentType))
    for model in MODEL_PACK_MODELS:
        for comp in model.recommended_components:
            assert comp in valid, f"{model.id}: 推荐组件 '{comp}' 非 ComponentType"


def test_fallback_chain_resolves_and_acyclic() -> None:
    reg = get_map_model_registry()
    for model in reg._by_id.values():
        seen = {model.id}
        cur = model
        while cur.fallback_model_id:
            nxt = reg.resolve(cur.fallback_model_id)
            assert nxt is not None, f"{cur.id}: fallback '{cur.fallback_model_id}' 未注册"
            assert nxt.id not in seen, f"{model.id}: fallback 链成环 {seen | {nxt.id}}"
            seen.add(nxt.id)
            cur = nxt


def test_validate_model_library_clean() -> None:
    assert validate_model_library() == []


def test_registry_determinism() -> None:
    a = get_map_model_registry().all_ids
    reset_map_model_registry()
    b = get_map_model_registry().all_ids
    assert a == b


def test_native_and_planned_partition() -> None:
    reg = get_map_model_registry()
    assert set(reg.native_ids()) | set(reg.planned_ids()) == set(reg.all_ids)
    assert not set(reg.native_ids()) & set(reg.planned_ids())
    # 扩容后 native 仍占多数（库的可用性），planned 是诚实少数派
    assert len(reg.native_ids()) >= len(reg.planned_ids())
