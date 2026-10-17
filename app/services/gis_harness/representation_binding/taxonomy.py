"""Mutation taxonomy（ADR-0219 C1）——MapSpec mutation 的 requirement 相关性分类。

**派生自既有词表，不新造命令/意图**：分类对象是
``mapspec/mutation_registry`` 注册的全部 intent kind × 既有归因轴
（``MutationOrigin``、actor 词表、``envelope.REPAIR_ACTORS``、
producer_class）。四类语义（+unbound）：

1. ``requirement_significant``：跨轮应保持的用户制图语义——
   layer visibility intent / 图层锁 / 组件开关·样式·位置 / palette /
   representation（geometry_kind）/ 标注策略 / 显式标题内容；
2. ``presentation_ephemeral``：pan/zoom/hover/selection 等永不进入
   requirement 的呈现态（含持久但不具跨轮语义的 opacity-only 调整）；
3. ``system_repair``：自动修复/自愈——**绝不冒充用户偏好**；
4. ``data_science``：数据摄取/分析参数面，不属于 representation binding；
5. ``unbound``：bootstrap/快照管理等与表达绑定无关的组织面操作。

一致性纪律（fail-closed）：本表对 registry 的每个 kind 必须有显式条目；
未知 kind → ``unbound``（不投影、不绑定）并带 reason code，绝不猜测分类。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional

TaxonomyClass = Literal[
    "requirement_significant",
    "presentation_ephemeral",
    "system_repair",
    "data_science",
    "unbound",
]

SemanticDimension = Literal[
    "layer_visibility",     # 显隐意图（requirement.hidden_layer_ids 面）
    "layer_lock",           # workbench 锁（requirement.locked_layer_ids 面）
    "layer_style",          # 逐层 paint（MapSpec 权威；requirement 不镜像）
    "components",           # 组件开关/样式/位置（requirement.components 面）
    "title_content",        # 显式标题/内容
    "palette",              # 色带 pin（grammar face）
    "representation",       # 表达类型 pin（grammar face）
    "undo_rebaseline",      # 用户 undo 后的语义重定基
    "none",
]

#: intent kind → (taxonomy class, semantic dimension)。
#: kind 名 = registry ``IntentDescriptor.kind``（= 类名，provenance 契约）。
_TAXONOMY_TABLE: Dict[str, tuple] = {
    # ── requirement-significant ────────────────────────────────────────
    # 显隐意图（visible 参与）；opacity-only 由 classify_mutation 动态降为
    # presentation_ephemeral（持久但不跨轮）。
    "PatchLayerPresentationIntent": ("requirement_significant", "layer_visibility"),
    "PatchLayerStyleIntent": ("requirement_significant", "layer_style"),
    "PatchComponentIntent": ("requirement_significant", "components"),
    "RemoveComponentIntent": ("requirement_significant", "components"),
    "DuplicateComponentIntent": ("requirement_significant", "components"),
    "RebindComponentIntent": ("requirement_significant", "components"),
    "SetLayoutIntent": ("requirement_significant", "title_content"),
    "SetWorkbenchStateIntent": ("requirement_significant", "layer_lock"),
    "PatchWorkbenchDeltaIntent": ("requirement_significant", "layer_lock"),
    "RemoveLayerIntent": ("requirement_significant", "layer_visibility"),
    # ── presentation-ephemeral（永不进入 requirement） ──────────────────
    "SetViewIntent": ("presentation_ephemeral", "none"),
    # ── system repair（不冒充用户偏好） ────────────────────────────────
    "ApplyVisualHealPatchIntent": ("system_repair", "none"),
    # ── data / science（不属于 representation binding） ────────────────
    "UpsertSourceIntent": ("data_science", "none"),
    "UpsertLayerIntent": ("data_science", "none"),
    "SetTimeIntent": ("data_science", "none"),
    "SetBasemapIntent": ("data_science", "none"),
    # ── unbound（组织面；undo 经 undo_rebaseline 特判见下） ─────────────
    "InitProjectIntent": ("unbound", "none"),
    "CheckpointIntent": ("unbound", "none"),
    "SetScenarioModeIntent": ("unbound", "none"),
    "SetSceneIntent": ("unbound", "none"),
    "ReorderLayersIntent": ("unbound", "none"),
    # Rollback/RestoreStyle：语义随 origin 动态判定——user 的 undo 触发语义
    # 重定基（undo_rebaseline），agent/system 的回滚属组织面不投影。
    "RollbackIntent": ("unbound", "undo_rebaseline"),
    "RestoreStyleIntent": ("unbound", "undo_rebaseline"),
}

#: 动态降级谓词使用的 intent 属性面（见 classify_mutation）。
_REPAIR_ORIGINS = ("system",)


@dataclass(frozen=True)
class MutationTaxonomy:
    """一笔（或一批）mutation 的 requirement 相关性裁决（确定性值对象）。"""

    kind: str
    origin: str
    taxonomy: TaxonomyClass
    dimension: SemanticDimension
    reason_code: str = ""

    @property
    def projects_to_requirement(self) -> bool:
        """是否进入 C4 语义投影（仅 user origin + requirement 显著面）。"""
        return self.origin == "user" and (
            self.taxonomy == "requirement_significant"
            or self.dimension == "undo_rebaseline"
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "origin": self.origin,
            "taxonomy": self.taxonomy,
            "dimension": self.dimension,
            "reason_code": self.reason_code,
        }


def _is_repair(origin: str, actor: Optional[str], producer_class: Optional[str]) -> bool:
    """system repair 判定：origin=system，或 repair/finalizer actor，或
    producer 落在修复/模板/系统默认档（复用 envelope 词表，不另造）。"""
    if origin in _REPAIR_ORIGINS:
        return True
    from app.services.gis_world_state.envelope import (
        PRODUCER_REPAIR_AUTOFILL,
        PRODUCER_SYSTEM_DEFAULT,
        PRODUCER_TEMPLATE,
        REPAIR_ACTORS,
    )

    if actor and actor in REPAIR_ACTORS:
        return True
    return producer_class in (
        PRODUCER_REPAIR_AUTOFILL,
        PRODUCER_TEMPLATE,
        PRODUCER_SYSTEM_DEFAULT,
    )


def classify_mutation(
    kind: str,
    origin: str,
    *,
    actor: Optional[str] = None,
    producer_class: Optional[str] = None,
    visible_only_opacity: bool = False,
) -> MutationTaxonomy:
    """分类一笔 mutation（纯函数；输入为服务端已提交 receipt 的归因面）。

    Args:
        kind: intent 类名（registry ``kind`` 契约）。
        origin: ``MutationOrigin``（agent/user/system）。
        actor/producer_class: 信封归因（repair 判定）。
        visible_only_opacity: 调用方对 ``PatchLayerPresentationIntent`` 的
            预判——intent 只带 opacity 不带 visible 时为 True（该维度不
            跨轮，降为 presentation_ephemeral）。

    未知 kind fail-closed → unbound（reason ``taxonomy_unknown_kind``），
    绝不猜测（新增 intent 未登记分类时不会误投影）。
    """
    entry = _TAXONOMY_TABLE.get(kind)
    if entry is None:
        return MutationTaxonomy(
            kind=kind, origin=origin, taxonomy="unbound", dimension="none",
            reason_code="taxonomy_unknown_kind",
        )
    taxonomy, dimension = entry

    # system repair 覆盖：修复/自动回填不得冒充用户偏好（C1 第 3 类）。
    # 注意 origin="system" 的 Rollback/RestoreStyle 仍是组织面（不升级）。
    if _is_repair(origin, actor, producer_class) and taxonomy != "unbound":
        return MutationTaxonomy(
            kind=kind, origin=origin, taxonomy="system_repair",
            dimension=dimension, reason_code="taxonomy_repair_actor",
        )

    # opacity-only 显隐调整：持久于 spec，但不构成跨轮用户语义。
    if kind == "PatchLayerPresentationIntent" and visible_only_opacity:
        return MutationTaxonomy(
            kind=kind, origin=origin, taxonomy="presentation_ephemeral",
            dimension="none", reason_code="taxonomy_opacity_only",
        )

    # undo_rebaseline 的生效条件：仅 user 的 rollback/restore 触发重定基。
    if dimension == "undo_rebaseline" and origin != "user":
        return MutationTaxonomy(
            kind=kind, origin=origin, taxonomy="unbound", dimension="none",
            reason_code="taxonomy_undo_not_user",
        )
    return MutationTaxonomy(kind=kind, origin=origin, taxonomy=taxonomy, dimension=dimension)


def registered_taxonomy_kinds() -> frozenset:
    """taxonomy 表覆盖的 kind 集（一致性 gate 用）。"""
    return frozenset(_TAXONOMY_TABLE)


def assert_taxonomy_covers_registry() -> None:
    """一致性 gate：registry 每个 kind 都必须有显式 taxonomy 条目。

    新增 intent 未登记 → 本函数 raise（测试 ``test_taxonomy.py`` 钉住）——
    fail-closed：宁可让新增意图显式声明"unbound"也不允许静默漏分。
    """
    from app.services.mapspec.mutation_registry import MUTATION_REGISTRY

    registry_kinds = {cls.__name__ for cls in MUTATION_REGISTRY.intent_classes()}
    missing = registry_kinds - set(_TAXONOMY_TABLE)
    if missing:
        raise RuntimeError(
            f"representation_binding taxonomy 缺少 registry intent 条目: {sorted(missing)}")
