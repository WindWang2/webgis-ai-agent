"""Mutation Registry 一致性 gate + fail-closed + 扩展演示（H02）。

三个 gate 面：
1. consistency — registry ↔ MutationIntent Union ↔ HTTP codec ↔ 旗标
   旗标投影（auto_checkpoint / touches_layers / effect_class / op_label /
   collab event）↔ blocking codes ↔ parity corpus 覆盖，任一脱节即红
   （新增 intent 漏登记者在 CI 拦截，不再靠人肉同步 10+ 文件）。
2. fail-closed — 未注册 intent 进入 apply_mutation 必须被拒绝且不触碰
   状态（原行为：静默落空 → review 段隐晦崩溃）。
3. extension demo — 新增一个测试 intent 只需 descriptor + handler +
   register 三步，经完整引擎事务（锁/CAS/校验/提交/回滚）跑通，证明
   不再跨 10+ 文件 shotgun surgery（issue #1547 验收）。
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

import app.services.mapspec.store as mapspec_store_module
from app.services.mapspec.intents import MutationIntent, SetViewIntent
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.mutation_contracts import (
    MutationContext,
    MutationPlan,
)
from app.services.mapspec.mutation_registry import MUTATION_REGISTRY
from app.services.session_data import session_data_manager


# ── 1. 一致性 gate ────────────────────────────────────────────────────────────
def test_registry_covers_mutation_intent_union_exactly():
    import typing

    assert set(typing.get_args(MutationIntent)) == set(
        MUTATION_REGISTRY.intent_classes()
    )


def test_registry_kinds_unique_and_class_named():
    kinds = [d.kind for d in MUTATION_REGISTRY.all()]
    assert len(kinds) == len(set(kinds)), "provenance kind 必须唯一"
    for d in MUTATION_REGISTRY.all():
        assert d.kind == d.intent_cls.__name__, (
            "kind 恒等类名 —— provenance/journal 字符串消费面兼容契约"
        )


def test_auto_checkpoint_flags_match_baseline():
    """提交期 auto checkpoint 的 7 类（重构前分支旗标逐字对应）。"""
    expected = {
        "UpsertLayerIntent",
        "PatchLayerPresentationIntent",
        "PatchLayerStyleIntent",
        "UpsertSourceIntent",
        "RemoveLayerIntent",
        "ReorderLayersIntent",
        "ApplyVisualHealPatchIntent",
    }
    actual = {
        d.intent_cls.__name__ for d in MUTATION_REGISTRY.all() if d.auto_checkpoint
    }
    assert actual == expected


def test_touches_layers_flags_match_cow_baseline():
    """COW layers deepcopy 判定的 8 类（原 _layers_touching 元组）。"""
    expected = {
        "UpsertLayerIntent",
        "PatchLayerPresentationIntent",
        "RemoveLayerIntent",
        "ReorderLayersIntent",
        "InitProjectIntent",
        "RollbackIntent",
        "RestoreStyleIntent",
        "ApplyVisualHealPatchIntent",
    }
    actual = {
        d.intent_cls.__name__ for d in MUTATION_REGISTRY.all() if d.touches_layers
    }
    assert actual == expected


def test_effect_class_matches_presentation_baseline():
    """presentation 9 类（原 _PRESENTATION_INTENT_TYPES）。"""
    expected = {
        "PatchLayerPresentationIntent",
        "PatchLayerStyleIntent",
        "SetLayoutIntent",
        "PatchComponentIntent",
        "RemoveComponentIntent",
        "DuplicateComponentIntent",
        "RebindComponentIntent",
        "ApplyVisualHealPatchIntent",
        "SetSceneIntent",
    }
    actual = {
        d.intent_cls.__name__
        for d in MUTATION_REGISTRY.all()
        if d.effect_class == "presentation"
    }
    assert actual == expected


def test_op_labels_match_legacy_table():
    """journal 标签 = 原 gis_world_state._OP_LABELS 的 20 条（行为零漂移锚）。

    缺表意图（SetScene / ApplyVisualHealPatch / PatchWorkbenchDelta）保持
    回退类名 —— 与重构前行为一致；补词属行为变更，另行评审。
    """
    legacy = {
        "SetViewIntent": "调整视图",
        "SetLayoutIntent": "调整版面",
        "SetTimeIntent": "调整时间维度",
        "SetBasemapIntent": "切换底图",
        "InitProjectIntent": "初始化项目",
        "UpsertLayerIntent": "挂载图层",
        "UpsertSourceIntent": "挂载数据源",
        "RemoveLayerIntent": "移除图层",
        "ReorderLayersIntent": "调整图层顺序",
        "PatchLayerStyleIntent": "修改图层样式",
        "PatchLayerPresentationIntent": "调整显隐/透明度",
        "PatchComponentIntent": "调整组件",
        "RemoveComponentIntent": "移除组件",
        "DuplicateComponentIntent": "复制组件",
        "RebindComponentIntent": "重绑定组件",
        "CheckpointIntent": "创建检查点",
        "RollbackIntent": "回滚",
        "RestoreStyleIntent": "恢复样式版本",
        "SetWorkbenchStateIntent": "更新工作台组织",
        "SetScenarioModeIntent": "切换推演模式",
    }
    for d in MUTATION_REGISTRY.all():
        if d.intent_cls.__name__ in legacy:
            assert d.op_label == legacy[d.intent_cls.__name__], d.kind
        else:
            assert d.op_label is None, f"{d.kind} 新增标签属行为变更"


def test_collab_events_match_legacy_routing():
    """协作事件路由 3 条（doc/delta/presentation），其余仅 op journal。"""
    expected = {
        "SetWorkbenchStateIntent": "doc",
        "PatchWorkbenchDeltaIntent": "delta",
        "PatchLayerPresentationIntent": "presentation",
    }
    actual = {
        d.intent_cls.__name__: d.collab_event
        for d in MUTATION_REGISTRY.all()
        if d.collab_event is not None
    }
    assert actual == expected


def test_http_body_codec_coverage_and_roundtrip():
    """schema 的全部 mutation Body 都有 codec，且 codec 产出对应 intent。"""
    from typing import get_args

    from app.schemas import mapspec_mutation_schema as ms

    # Annotated[Union[...], Field(discriminator=...)]：第一参即 Union
    union_members = {
        m.__name__ for m in get_args(ms.UserMapSpecMutationRequest.__args__[0])
    }

    body_classes = [
        v for k, v in vars(ms).items() if isinstance(v, type) and k.endswith("Body")
    ]
    assert len(body_classes) == 14
    # discriminated union 的 14 成员与 codec 表完全重合（漏登记在 CI 拦截）
    assert union_members == {c.__name__ for c in body_classes}, union_members ^ {
        c.__name__ for c in body_classes
    }
    for body_cls in body_classes:
        assert body_cls in MUTATION_REGISTRY._body_codecs, (
            f"{body_cls.__name__} 缺 Body codec 登记"
        )


def test_intent_codec_and_registry_same_table():
    """intent_codec 与 registry 消费同一 codec 表（单一事实源断言）。"""
    from app.schemas.mapspec_mutation_schema import SetTimeBody
    from app.services.mapspec.intent_codec import body_to_intent

    body = SetTimeBody(intent="set_time", enabled=True, step=3.0, expected_revision=0)
    intent = body_to_intent(body)
    assert type(intent).__name__ == "SetTimeIntent"
    assert intent.step == 3.0


def test_blocking_codes_single_source_and_emitter_subset():
    """blocking codes：lifecycle 闸与 primitives 同源；coordinator 发射的
    全部 codes ⊆ 表（发射面漂移在 CI 拦截）。"""
    import asyncio
    import inspect

    from app.services.mapspec.lifecycle_engine import BLOCKING_VALIDATION_CODES
    from app.services.mapspec.mutation_primitives import (
        BLOCKING_VALIDATION_CODES as _PRIM_CODES,
    )

    assert BLOCKING_VALIDATION_CODES is _PRIM_CODES
    assert BLOCKING_VALIDATION_CODES == {
        "INVALID_SOURCE_REF",
        "INVALID_STOPS_COUNT",
        "NON_INCREASING_STOPS",
        "SCENE_TERRAIN_SOURCE_REF",
        "SCENE_TERRAIN_SOURCE_TYPE",
    }
    # emitter 面：coordinator 源内出现的校验码字面量必须都在表内
    import app.services.mapspec.coordinator as coordinator

    emitted = set(inspect.getsource(coordinator).split('"')) | set(
        inspect.getsource(coordinator).split("'")
    )
    # 取 coordinator 模块源码中形如校验码的字符串（大写下划线 token）
    tokens = set(
        __import__("re").findall(
            r"\b[A-Z][A-Z0-9_]{5,}\b", inspect.getsource(coordinator)
        )
    )
    validation_tokens = {t for t in tokens if t in emitted or t.isupper()}
    leak = {
        t
        for t in validation_tokens
        if t.endswith(("_REF", "_COUNT", "STOPS", "_TYPE"))
        and t not in BLOCKING_VALIDATION_CODES
    }
    assert not leak, f"coordinator 出现未登记校验码: {leak}"
    assert asyncio is not None  # noqa — 保持 import 语义显式


# ── 2. fail-closed：未注册 intent ─────────────────────────────────────────────
@dataclass
class UnregisteredGhostIntent:
    """registry 外的未知意图（模拟第三方/未来代码直接构造）。"""

    payload: Dict[str, Any] = field(default_factory=dict)


@pytest.mark.asyncio
async def test_unknown_intent_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    engine = MapSpecLifecycleEngine()
    session_id = f"ghost-{uuid.uuid4().hex[:10]}"
    res = await engine.apply_mutation(session_id, UnregisteredGhostIntent())
    assert res.is_error is True
    assert res.error_code == "UNSUPPORTED_MUTATION_INTENT"
    assert "UnregisteredGhostIntent" in res.error_msg
    # 未触碰状态：无 spec 落盘、revision 未推进
    assert await engine.store.get_mapspec(session_id) is None
    state = await session_data_manager.get_map_state(session_id)
    assert int(state.get("_cartographic_mutation_revision", 0) or 0) == 0


# ── 3. 扩展演示：新 intent = descriptor + handler + register ─────────────────
@dataclass
class DemoPinAnnotationIntent:
    """演示用新意图：顶层 annotations 追加一条 pin（测试装配，非生产词表）。"""

    pin_id: str
    coordinates: tuple = ()


async def _demo_pin_handler(ctx: MutationContext):
    intent: DemoPinAnnotationIntent = ctx.intent
    loaded = ctx.loaded
    mapspec = {**loaded} if loaded else {}
    annotations = list(mapspec.get("annotations") or [])
    annotations.append(
        {
            "id": intent.pin_id,
            "coordinates": list(intent.coordinates),
        }
    )
    mapspec["annotations"] = annotations
    return MutationPlan(mapspec=mapspec)


@pytest.fixture()
def demo_pin_registered():
    from app.services.mapspec.mutation_registry import IntentDescriptor

    descriptor = IntentDescriptor(
        intent_cls=DemoPinAnnotationIntent,
        handler=_demo_pin_handler,
        effect_class="semantic",
        touches_layers=False,
        auto_checkpoint=False,
        # op_label / collab_event / provenance_detail 缺省 —— 全部回退安全值
    )
    MUTATION_REGISTRY.register(descriptor)
    yield descriptor
    MUTATION_REGISTRY.unregister(DemoPinAnnotationIntent)


@pytest.mark.asyncio
async def test_new_intent_needs_no_shotgun_surgery(
    demo_pin_registered, tmp_path, monkeypatch
):
    """三步接入即获得完整事务语义 —— 无需改 lifecycle/codec/事件/溯源。"""
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    engine = MapSpecLifecycleEngine()
    session_id = f"demo-pin-{uuid.uuid4().hex[:10]}"

    res = await engine.apply_mutation(
        session_id, SetViewIntent(center=[104.0, 30.6], zoom=10.0)
    )
    assert res.is_error is False
    revision = res.mutation_revision

    res2 = await engine.apply_mutation(
        session_id,
        DemoPinAnnotationIntent(pin_id="pin-1", coordinates=(104.0, 30.6)),
        origin="user",
        expected_revision=revision,
    )
    assert res2.is_error is False
    assert res2.mutation_revision == revision + 1
    assert res2.mapspec["annotations"] == [
        {"id": "pin-1", "coordinates": [104.0, 30.6]}
    ]
    # CAS 链保持：陈旧 revision → superseded
    res3 = await engine.apply_mutation(
        session_id,
        DemoPinAnnotationIntent(pin_id="pin-2", coordinates=(0.0, 0.0)),
        origin="user",
        expected_revision=revision,
    )
    assert res3.superseded is True
    # journal/provenance 投影缺省回退（类名标签、无协作事件）——无需注册面
    from app.services.mapspec.mutation_registry import MUTATION_REGISTRY as REG

    assert (
        REG.op_label(DemoPinAnnotationIntent(pin_id="x")) == "DemoPinAnnotationIntent"
    )
    assert REG.collab_event(DemoPinAnnotationIntent(pin_id="x")) is None
