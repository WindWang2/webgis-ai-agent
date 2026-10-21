"""MapSpec mutation intent 值对象 — 单一事实源（H02 解巨石）。

全部引擎可执行的 mutation intent dataclass 原生定义点（此前内联在
lifecycle_engine 巨石中）；lifecycle_engine 原样 re-export 保持既有
import 面零破坏。叶子模块：仅依赖 visual_healer 纯函数层的类型。

新增 intent 的登记点见 mutation_registry.py（descriptor + handler +
本文件 Union 各一行 + 测试）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union

from app.services.mapspec.visual_healer import VisualCritiqueItem
# H01×H02 收敛（ADR-0216）：presentation 契约归位 app/contracts（kernel，
# lib/cartography 消费方直取；此处 re-export 保持 leaf 模块 import 面零破坏）。
from app.contracts.mapspec_intents import (  # noqa: F401
    PatchLayerPresentationIntent,
    SetSceneIntent,
    SetViewIntent,
)


# ─── #1550：mutation intent 载荷接缝的具名载体 ─────────────────────────────
#
# intent 携带的 MapSpec 分支此前全是裸 ``Dict[str, Any]`` —— 字段拼错、
# 缺字段、None 传播均无类型期防线。以下 TypedDict 给每个命名载荷一个
# 键集契约（与前端 ``types.generated.ts`` / 后端 pydantic 契约逐字对齐）。
#
# ``total=False`` = **已知键**契约，不是封闭集：MapSpec 载荷是开放文档
# （MapLibre 允许任意附加键、渲染端透传、LLM 可携带扩展字段），TypedDict
# 的关闭性无法表达这一点；运行期仍是普通 dict（既有行为零变化）。
# 真正封闭的载荷走 pydantic 契约（CartographyComponent / CompositionIdentity）。


class MapSpecLayerPayload(TypedDict, total=False):
    """图层条目（MapLibre layer spec + 后端制图扩展键）。

    后端读写的键：id/type/source（摄取与 upsert 匹配）、paint/layout
    （presentation 突变）、cartographic_intent/context_role/role
    （authoring 决策印记）、provenance（ref_id/result_ref 溯源）、
    legend_spec/filter（语义评审）。其余 MapLibre 键透传不干预。
    """

    id: str
    type: str
    source: str
    paint: Dict[str, Any]
    layout: Dict[str, Any]
    filter: Any
    metadata: Dict[str, Any]
    legend_spec: Dict[str, Any]
    cartographic_intent: Dict[str, Any]
    context_role: str
    role: str
    provenance: Dict[str, Any]


class MapSpecSourcePayload(TypedDict, total=False):
    """source 条目（数据载体 + 画像指纹；键集见 pipeline 的存取面）。"""

    type: str
    url: str
    data: Any
    inlineData: Any
    dataPath: str
    ref: str
    ref_id: str
    imageRef: str
    bounds: Any
    profile: Dict[str, Any]
    data_fingerprint: str


class MapSpecControlPayload(TypedDict, total=False):
    """layout.controls 单条（与前端 MapSpecControlConfig 对齐）。"""

    type: str  # navigation | scale | fullscreen
    position: str  # 四槽锚点


class MapSpecComponentPayload(TypedDict, total=False):
    """layout.components 单条（与 CartographyComponent 的 mapspec 形态对齐）。"""

    id: str
    type: str
    enabled: bool
    position: str
    priority: int
    style: Dict[str, Any]
    options: Dict[str, Any]
    compatibility: Dict[str, Any]
    category: str
    variant: str
    templateId: str
    schemaVersion: int


class MapSpecComponentLinkPayload(TypedDict, total=False):
    """layout.component_links 单条（实例边；type 词表见 COMPONENT_LINK_TYPES）。"""

    src: str
    dst: str
    type: str
    dst_kind: str  # component | layer | source


class CompositionIdentityPayload(TypedDict, total=False):
    """layout.composition 身份块（composition_contract.CompositionIdentity 的
    to_bounded_dict 投影；有界 4KB，由 webgis_apply_composition 写入）。"""

    template_id: str
    template_version: str
    contract_id: str
    contract_fingerprint: str
    component_abi_version: int
    component_versions: Dict[str, str]
    applied_revision: int


class MapSpecSnapshotPayload(TypedDict, total=False):
    """RestoreStyleIntent.snapshot —— Map Product 版本携带的表达面子集。

    只含 style-only 恢复触及的分支（view/basemap/time/layout/layers）；
    数据与计算计划不进快照。
    """

    view: Dict[str, Any]
    basemap: Dict[str, Any]
    time: Dict[str, Any]
    layout: Dict[str, Any]
    layers: List[MapSpecLayerPayload]


#: 持久 paint 补丁（PatchLayerStyleIntent）。键集 = 后端实际写面：通用
#: ``opacity`` + 逐图层类型专属透明度键（_OPACITY_PAINT_KEYS 的值域）。
#: 其余 paint 属性由 symbology 管线写入，不经本意图。功能语法是因为
#: MapLibre paint 键含连字符（类语法 TypedDict 只允许标识符键）。
MapSpecPaintPayload = TypedDict(
    "MapSpecPaintPayload",
    {
        "opacity": float,
        "circle-opacity": float,
        "fill-opacity": float,
        "line-opacity": float,
        "raster-opacity": float,
        "heatmap-opacity": float,
        "fill-extrusion-opacity": float,
        "icon-opacity": float,
    },
    total=False,
)


# TypedDict 字段的可变缺省须走具名工厂（mypy 不接受裸 ``dict`` 工厂：
# 其返回 dict[Never, Never]，与 TypedDict 不兼容）。
def _empty_snapshot() -> MapSpecSnapshotPayload:
    return {}


def _empty_paint() -> MapSpecPaintPayload:
    return {}


@dataclass
class ApplyVisualHealPatchIntent:
    """ADR-0186：视觉自愈微变异（缺陷清单 → 单事务 MapSpec patch）。

    defects 为归一化 VisualCritiqueItem 序列；分发分支在**锁内**用权威
    loaded spec 重新规划（TOCTOU 安全）再 apply_heal_plan COW 应用，
    校验/checkpoint/revision 单调/失败回滚全部走既有事务管线。
    attempt 由入口按收敛账本注入（驱动 label/opacity 修复阶梯）。
    """

    defects: Tuple[VisualCritiqueItem, ...] = ()
    quality_score: Optional[float] = None
    attempt: int = 0


@dataclass
class InitProjectIntent:
    view: Optional[Dict[str, Any]] = None
    thresholds: Optional[Dict[str, Any]] = None


@dataclass
class UpsertLayerIntent:
    # #1550：layer 载荷具名化（MapSpecLayerPayload）。
    layer: MapSpecLayerPayload
    source_data: Optional[Any] = None


@dataclass
class UpsertSourceIntent:
    source_id: str
    # #1550：source 载荷具名化（MapSpecSourcePayload）。
    source: MapSpecSourcePayload


@dataclass
class RemoveLayerIntent:
    layer_id: str


@dataclass
class ReorderLayersIntent:
    layer_ids: List[str]


@dataclass
class SetLayoutIntent:
    legend: Optional[Dict[str, Any]] = None
    # #1550：controls/components/component_links/composition 载荷具名化。
    controls: Optional[List[MapSpecControlPayload]] = None
    margins: Optional[Dict[str, Any]] = None
    # CartographyComponent 列表（app/services/gis_harness/components）。
    # live 渲染与 export 共用同一份组件描述；None = 不触碰既有组件。
    components: Optional[List[MapSpecComponentPayload]] = None
    # ADR-0214 D2/D3 additive：契约 apply 的实例边与组合身份块。
    # None = 不触碰既有值（全部既有调用方零行为变化）。锁语义不变：
    # intent_lock_targets 只看 components —— 身份块/边不是锁目标。
    component_links: Optional[List[MapSpecComponentLinkPayload]] = None
    composition: Optional[CompositionIdentityPayload] = None


@dataclass
class CheckpointIntent:
    checkpoint_id: Optional[str] = None


@dataclass
class RollbackIntent:
    checkpoint_id: str


@dataclass
class RestoreStyleIntent:
    """Map Product 版本的样式态恢复（ADR-0099 style-only restore）。

    从版本携带的 mapspec_snapshot 恢复**表达面**：view / layout+components /
    basemap / time / 逐层 paint+visible+opacity（按 layer_id 匹配当前 spec，
    快照里有而当前不存在的层跳过 —— 数据层不由本意图增删）。数据与计算
    计划不动 —— style-only 恢复绝不触发分析重算（五维 diff 的机器读契约）。
    走 apply_mutation 的锁 + CAS + 校验 + 失败回滚全事务，不是裸写。
    """

    # #1550：snapshot 载荷具名化（MapSpecSnapshotPayload —— 表达面分支子集）。
    snapshot: MapSpecSnapshotPayload = field(default_factory=_empty_snapshot)


@dataclass
class PatchLayerStyleIntent:
    """#1077：spec 承载层的持久样式突变（paint 顶层键合并）。

    layer_style_update 此前只改 MapLibre 运行时 paint + HUD 行（不进
    committed MapSpec）—— 下一次同层 recompile 即回滚，「UI 已改色但
    地图随后复原」既是体验缺陷也是观察/修复环的噪声源。该意图把样式
    写入权威 spec；origin=agent 的工具路径与 origin=user 的面板路径
    共用（样式不属于 user-wins 守卫的 presentation 面）。
    """

    layer_id: str
    # #1550：paint 补丁具名化（MapSpecPaintPayload —— 后端写面键集）。
    paint: MapSpecPaintPayload = field(default_factory=_empty_paint)


@dataclass
class PatchComponentIntent:
    """Component-local mutation (UI drag/resize/collapse or agent chrome edit).

    与 SetLayoutIntent（整表替换）相对：只改命中的单个组件，其余组件不动。
    校验/突变逻辑复用 gis_harness.components.mutate_component —— 同一入口
    服务 user route 与 agent 工具，不出现第二套组件突变实现。
    """

    component_id: str
    component_type: Optional[str] = None
    enabled: Optional[bool] = None
    position: Optional[str] = None
    placement: Optional[Dict[str, Any]] = None
    variant: Optional[str] = None
    style: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    upsert: bool = False


@dataclass
class RemoveComponentIntent:
    """Component Lifecycle V3（Runtime V4 §18）：组件真删除。

    与 enabled=False（隐藏）相对：从 layout.components 移除实例。删除后
    dock/selection/renderer 清理由消费侧按「id 离开 spec」既有语义收敛；
    finalize 对「契约仍要求该族」的场景会按 component_missing 重新披露
    （删除单例契约组件是 agent/用户决策，重评估由完成度运行时承接）。
    """

    component_id: str


@dataclass
class DuplicateComponentIntent:
    """Component Lifecycle V3（§19）：复制多实例组件（新 id + floating 偏移）。"""

    component_id: str
    new_id: Optional[str] = None


@dataclass
class RebindComponentIntent:
    """Component Lifecycle V3（§19）：重绑定引用字段（chartRef/tableRef/layerId）。

    目标存在性（artifact ref 活性 / 图层在场）由调用方锁内守卫校验 ——
    引擎只承接 schema 白名单与互斥纪律（纯函数 rebind_component）。
    """

    component_id: str
    bindings: Dict[str, str] = field(default_factory=dict)


@dataclass
class SetBasemapIntent:
    """Basemap chrome mutation (#722): keeps the persisted spec tracking the
    BASE_LAYER_CHANGE command the legacy basemap tools emit, so desired state
    and the runtime map cannot diverge on provider switches."""

    provider_id: Optional[str] = None
    raster_filters: Optional[Dict[str, Any]] = None
    overlays: Optional[List[Any]] = None
    vector_style_url: Optional[str] = None


@dataclass
class SetTimeIntent:
    enabled: Optional[bool] = None
    field: Optional[str] = None
    type: Optional[str] = None
    extent: Optional[List[Any]] = None
    current: Optional[Any] = None
    window: Optional[Any] = None
    playback: Optional[Dict[str, Any]] = None
    step: Optional[float] = None
    speed: Optional[float] = None


@dataclass
class SetScenarioModeIntent:
    """What-If 推演视图协议（ADR-0193）：顶层 ``scenario_mode`` 写入。

    ``split_view`` / ``swipe_compare`` 进入推演对比视图；``None`` 退出
    （键从 spec 移除，非推演语义）。COW 只拷顶层分支；非法值整笔拒绝
    （is_error，last-known-good 不变）。前端按
    ``frontend/lib/mapspec/scenario-mode.ts`` 映射到既有 ComparisonView。
    """

    scenario_mode: Optional[str] = None


@dataclass
class SetWorkbenchStateIntent:
    """Workbench V5 组织态持久化（分组树/成员归属/图层锁/工作台模式）。

    doc 整体替换 ``mapspec['workbench']`` 分支 —— 全量文档语义，无部分合并
    （前端持有完整投影，CAS 串行链保证无丢更新）。结构合法性（version==5、
    组 id 唯一、父子无环、深度 ≤4、成员/锁为字符串键值、mode 封闭词表）
    与体积（64KB）在引擎内确定性校验 —— 非法输入 4xx，不留半更新状态。

    V6（base_workbench_revision）：可选 workbench 级 CAS —— 引擎在每次
    workbench 落盘时盖 ``_rev = mutation_revision``；提供本字段且与存储
    ``_rev`` 不一致 → superseded（409 回灌当前 doc）。这堵住「游标 revision
    被无关 mutation 推进后，陈旧全量 doc 借新鲜 CAS 静默整表覆盖他人组织态」
    的丢更新窗口（R1-C2）。缺省 = V5 语义（旧客户端兼容，风险在 ADR 披露）。
    """

    doc: Dict[str, Any]
    base_workbench_revision: Optional[int] = None


@dataclass
class PatchWorkbenchDeltaIntent:
    """Workbench V6 组织态**增量**补丁（部分更新；绝对值语义，重放幂等）。

    应用管线与引用检查见 ``app/services/collab/delta.py``（纯函数共享语义）：
    setGroups（部分字段 create/patch）→ removeGroupIds（级联）→
    membershipSet（目标必须存在）→ membershipClear → locks。应用结果经
    ``_workbench_doc_error`` 全量校验后整体替换分支并盖 ``_rev``。
    mode 不在 delta 域（V5 R1-M2：mode 不参与组织态增量/撤销）。
    """

    delta: Dict[str, Any]


MutationIntent = Union[
    InitProjectIntent,
    SetViewIntent,
    UpsertSourceIntent,
    UpsertLayerIntent,
    PatchLayerPresentationIntent,
    PatchWorkbenchDeltaIntent,
    PatchComponentIntent,
    RemoveComponentIntent,
    DuplicateComponentIntent,
    RebindComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
    RestoreStyleIntent,
    SetBasemapIntent,
    SetTimeIntent,
    PatchLayerStyleIntent,
    SetWorkbenchStateIntent,
    SetScenarioModeIntent,
    SetSceneIntent,
    ApplyVisualHealPatchIntent,
]
