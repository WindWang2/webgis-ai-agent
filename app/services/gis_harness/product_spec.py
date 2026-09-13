"""MapProductSpec v1 —— 语义地图产品文档（ADR-0183）。

「地图产品」是一等语义对象，位于 User Goal / Recipe 与 MapSpec 之间：

    Goal / Situation
      ↓
    MapProductSpec / 视图关系图          ← 本模块（产品语义真相）
      ↓
    ProductCompileResult（product_compiler，M4）
      ↓
    MapSpec / charts / layout / delivery（渲染真相不变）

职责边界（与 ADR-0076/0085/0092 单一真相纪律对齐）：

- spec 表达「产品是什么」：product purpose、subject、时空范围、claims、
  views（地图/插图/概览/图表/统计/叙述/对比/时间）、视图间关系、交付意图、
  用户显式覆盖、证据引用；
- spec **不表达**「怎么执行」（那是 MapProductPlan / SessionPlan）也不表达
  「怎么渲染」（paint/legend_spec/placement 留给 MapSpec 与组合模板）；
- 持久化位置 = SessionPlan chapter 的 ``product_spec`` 键（additive，presence
  语义合并）；spec 自带 spec_version + revision + digest，可无损迁独立存储。

不变式：有界（views ≤ MAX_VIEWS 等）、可序列化、fail-closed 校验
（未知 kind / 悬空关系端点 / 重复 id / 环 → errors 非空即拒）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.provenance.fingerprint import canonical_dumps

PRODUCT_SPEC_VERSION = "1.0"

#: 有界纪律（与 SessionPlan envelope / ProductGraph 的 bounded 精神一致）
MAX_VIEWS = 12
MAX_RELATIONS = 24
MAX_CLAIMS = 6
MAX_OVERRIDES = 16
MAX_DECISIONS_PER_VIEW = 4
MAX_FILTER_KEYS = 8
_MAX_STR = 200

#: 视图种类（产品语义词表；渲染组件类型映射在 product_compiler 单一登记，
#: 不复制 COMPONENT_TYPES）
VIEW_KINDS = (
    "map",            # 主地图视图
    "inset",          # 插图（主城区/区位）
    "overview",       # 概览（更大范围参照）
    "chart",          # 图表视图（kind 由 chart_kinds 词表约束）
    "stats_panel",    # 统计面板
    "narrative",      # 叙述/方法注记
    "comparison",     # 对比面板（双视图/双时期）
    "time_panel",     # 时间面板
)
ViewKind = Literal[VIEW_KINDS]  # type: ignore[valid-type]

#: 视图角色（对齐 PlannedLayer.role 词汇，但语义是产品面不是图层绑定面）
VIEW_ROLES = ("primary", "secondary", "context")

#: 视图间关系词表（ADR-0183 §3；方向性见 _DIRECTIONAL_RELATIONS）
RELATION_KINDS = (
    "same_dataset",        # 两视图同源数据
    "derived_statistic",   # dst 由 src 派生（聚合/统计）
    "comparison",          # 对比（对称）
    "overview_detail",     # src 概览 / dst 细节（方向性）
    "chart_linked_to_map", # 图表联动地图（方向性）
    "shared_legend",       # 共享图例（对称）
    "shared_extent",       # 共享范围（对称）
    "source_attribution",  # 共享来源归属（对称）
)
RelationKind = Literal[RELATION_KINDS]  # type: ignore[valid-type]

#: 方向性关系（参与有向环检测；对称关系只做端点/重复校验）
_DIRECTIONAL_RELATIONS = frozenset({
    "derived_statistic", "overview_detail", "chart_linked_to_map",
})

#: 交付目标词表（并集自产品模板 exports 与组合模板 output_targets 的既有词汇）
DELIVERY_TARGETS = ("interactive", "png", "pdf", "svg", "csv")

#: 产品编辑操作词表（M6；apply_product_edit 的唯一入口语义）
EDIT_OPS = (
    "remove_view",
    "add_view",
    "set_view_filter",
    "toggle_view",
    "replace_component",
    "toggle_component",
    "set_caption",
    "set_delivery",
)
EditOp = Literal[EDIT_OPS]  # type: ignore[valid-type]

#: ── 单一真相映射（ADR-0183 review 收敛：compiler / graph / tools 统一引用，
#: 不再各持一份 kind→词表映射 —— CA-P1-3 不加重）────────────────────────
#: spec 视图 kind → 支撑的 MapSpec 组件族（渲染组件类型）
VIEW_KIND_COMPONENT_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "chart": ("chart_panel",),
    "stats_panel": ("statistics_panel",),
    "narrative": ("methodology_note",),
    "inset": ("inset_map",),
    "comparison": ("chart_panel",),
    "time_panel": ("chart_panel",),
}
#: spec 视图 kind → ProductGraph 投影 facet kind（None = 不投影）
SPEC_KIND_TO_FACET: Dict[str, str] = {
    "chart": "chart",
    "stats_panel": "statistics",
    "inset": "inset",
    "comparison": "comparison",
    "time_panel": "time_panel",
}
#: 编辑载荷允许键（按 op 白名单 —— payload 是用户可控 dict，落账前消毒）
_EDIT_PAYLOAD_KEYS: Dict[str, Tuple[str, ...]] = {
    "remove_view": (),
    "add_view": ("view",),
    "set_view_filter": ("filter",),
    "toggle_view": (),
    "replace_component": ("chart_kind", "component_hint"),
    "toggle_component": ("component_type", "enabled"),
    "set_caption": ("text",),
    "set_delivery": ("delivery",),
}
_MAX_PAYLOAD_VALUE_STR = 200


def _sanitize_payload(op: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """按 op 白名单过滤键 + 深度 1 值消毒（str 截断；bool 原样；其余转 str 截断）。

    返回 (clean_payload, errors)。防御用户可控 payload 撑大 chapter 载荷
    或注入未知键（review 轴4）。
    """
    errors: List[str] = []
    if not isinstance(payload, dict):
        return {}, [f"payload must be a dict for op {op!r}"]
    allowed = _EDIT_PAYLOAD_KEYS.get(op)
    if allowed is None:
        return {}, [f"unknown op {op!r}"]
    clean: Dict[str, Any] = {}
    for k, v in payload.items():
        if k not in allowed:
            errors.append(f"payload key {k!r} not allowed for op {op!r}")
            continue
        if k == "view" and isinstance(v, dict):
            clean[k] = v  # add_view 的 view 交给 pydantic 校验（fail-closed）
        elif k == "filter" and isinstance(v, dict):
            clean[k] = {
                str(fk)[:80]: (fv if isinstance(fv, (bool, int, float))
                               else str(fv)[:_MAX_PAYLOAD_VALUE_STR])
                for fk, fv in list(v.items())[:MAX_FILTER_KEYS]
            }
        elif k == "delivery" and isinstance(v, dict):
            clean[k] = {
                str(dk)[:40]: (dv if isinstance(dv, (bool, list))
                               else str(dv)[:80])
                for dk, dv in list(v.items())[:4]
            }
        elif isinstance(v, bool):
            clean[k] = v
        else:
            clean[k] = str(v)[:_MAX_PAYLOAD_VALUE_STR]
    return clean, errors


class ProductViewBinding(BaseModel):
    """视图的数据/分析/渲染绑定（引用面 —— 全部是既有真相的指针）。"""

    model_config = ConfigDict(validate_assignment=True)

    dataset_ref: str = Field(default="", max_length=_MAX_STR)   # ref:xxx（会话数据面）
    analysis_ref: str = Field(default="", max_length=_MAX_STR)  # 产物 ref / artifact id
    layer_hint: str = Field(default="", max_length=120)         # MapSpec layer id 提示
    component_hint: str = Field(default="", max_length=120)     # MapSpec component id 提示
    filter: Dict[str, Any] = Field(default_factory=dict)        # 语义过滤（≤8 键）

    def is_bound(self) -> bool:
        return bool(self.dataset_ref or self.analysis_ref or self.layer_hint)


class ViewEvidence(BaseModel):
    """视图级证据引用（M7；只转录既有事实，不推断 —— 与 MapProductEvidence 同哲学）。"""

    model_config = ConfigDict(validate_assignment=True)

    dataset_ref: str = ""
    analysis_ref: str = ""
    selection_reason: str = Field(default="", max_length=_MAX_STR)
    #: 制图决策转录（编译器回填：模板/配方/槽位来源等；有界）
    decisions: List[str] = Field(default_factory=list, max_length=MAX_DECISIONS_PER_VIEW)
    user_override: str = ""   # 命中该视图的 override 摘要（无则空）
    quality_ref: str = ""     # 质量/评审结果指针（verdict/artifact id；缺省空不虚构）


class ProductView(BaseModel):
    """产品的一个语义视图。"""

    model_config = ConfigDict(validate_assignment=True)

    view_id: str = Field(max_length=80)
    kind: ViewKind
    role: str = "primary"     # VIEW_ROLES 之一（校验器把关，容忍历史值披露）
    title: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=_MAX_STR)
    subject: str = Field(default="", max_length=120)
    chart_kind: str = Field(default="", max_length=40)  # chart 视图：chart_kinds 词表
    binding: ProductViewBinding = Field(default_factory=ProductViewBinding)
    enabled: bool = True
    required: bool = False    # 产品应然构成（shape/契约派生；用户点名必真）
    evidence: ViewEvidence = Field(default_factory=ViewEvidence)


class ProductRelation(BaseModel):
    """视图关系边（产品级；组件级显式边仍是 MapSpec component_links 的职责）。"""

    src: str = Field(max_length=80)
    dst: str = Field(max_length=80)
    kind: RelationKind
    note: str = Field(default="", max_length=160)


class ProductDeliveryIntent(BaseModel):
    """交付意图（audience/aspect 语义面；导出版式细节仍在组合/导出模板）。"""

    targets: List[str] = Field(default_factory=lambda: ["interactive"], max_length=5)
    aspect: str = Field(default="", max_length=16)   # 如 "16:9"、"A4"
    audience: str = Field(default="", max_length=80) # 如 "汇报"、"打印"


class ProductOverride(BaseModel):
    """用户显式选择（编译优先级最高；M6 编辑的落点记录）。"""

    op: str = Field(max_length=40)            # EDIT_OPS 之一
    target: str = Field(default="", max_length=120)  # view_id / component type
    payload: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=_MAX_STR)


class MapProductSpec(BaseModel):
    """语义地图产品文档 v1（bounded / serializable / digest-stable）。"""

    spec_id: str = Field(max_length=80)
    spec_version: str = PRODUCT_SPEC_VERSION
    revision: int = 1
    query: str = Field(default="", max_length=300)
    goal: str = Field(default="", max_length=_MAX_STR)       # 产品目的（一句话）
    product_type: str = Field(default="", max_length=48)     # PRODUCT_ARCHETYPES 词表
    task: str = Field(default="", max_length=48)             # intent.task 词表
    scope: str = Field(default="", max_length=120)           # 地理范围名
    temporal: str = Field(default="", max_length=120)        # 时间范围/时期
    subject: str = Field(default="", max_length=120)
    claims: List[str] = Field(default_factory=list, max_length=MAX_CLAIMS)
    views: List[ProductView] = Field(default_factory=list, max_length=MAX_VIEWS)
    relations: List[ProductRelation] = Field(default_factory=list, max_length=MAX_RELATIONS)
    delivery: ProductDeliveryIntent = Field(default_factory=ProductDeliveryIntent)
    overrides: List[ProductOverride] = Field(default_factory=list, max_length=MAX_OVERRIDES)
    #: 引用面（类型库指针 —— 不内嵌模板本体）
    recipe_id: str = Field(default="", max_length=80)
    template_id: str = Field(default="", max_length=80)
    composition_template_id: str = Field(default="", max_length=80)
    plan_id: str = Field(default="", max_length=80)
    provenance_note: str = Field(default="", max_length=_MAX_STR)

    # ── 便捷查询（投影/编译器/验证器共用）────────────────────────────
    def view(self, view_id: str) -> Optional[ProductView]:
        for v in self.views:
            if v.view_id == view_id:
                return v
        return None

    def views_by_kind(self, kind: str) -> List[ProductView]:
        return [v for v in self.views if v.kind == kind and v.enabled]

    def has_kind(self, kind: str) -> bool:
        return bool(self.views_by_kind(kind))


def spec_digest(spec: MapProductSpec) -> str:
    """确定性内容摘要（canonical sha256；排除 revision —— 编辑计数不参与身份）。"""
    payload = spec.model_dump()
    payload.pop("revision", None)
    return hashlib.sha256(
        canonical_dumps(payload).encode("utf-8")).hexdigest()[:32]


def validate_product_spec(spec: MapProductSpec) -> List[str]:
    """fail-closed 结构校验：返回 errors（空 = 合法）。绝不改写 spec。"""
    errors: List[str] = []
    ids: set = set()
    if len(spec.views) > MAX_VIEWS:
        errors.append(f"views exceed MAX_VIEWS({MAX_VIEWS})")
    if len(spec.relations) > MAX_RELATIONS:
        errors.append(f"relations exceed MAX_RELATIONS({MAX_RELATIONS})")
    if len(spec.overrides) > MAX_OVERRIDES + MAX_VIEWS:
        # 软上限 MAX_OVERRIDES + 结构性 remove_view 账（≤MAX_VIEWS，编辑存活的
        # 撤回证据，永不裁掉 —— 裁掉会让已删视图在重组装时复活）。
        errors.append(
            f"overrides exceed MAX_OVERRIDES({MAX_OVERRIDES})+MAX_VIEWS({MAX_VIEWS})")
    if len(spec.claims) > MAX_CLAIMS:
        errors.append(f"claims exceed MAX_CLAIMS({MAX_CLAIMS})")
    for v in spec.views:
        if v.view_id in ids:
            errors.append(f"duplicate view_id: {v.view_id}")
        ids.add(v.view_id)
        if v.kind not in VIEW_KINDS:
            errors.append(f"view {v.view_id}: unknown kind {v.kind!r}")
        if v.role not in VIEW_ROLES:
            errors.append(f"view {v.view_id}: unknown role {v.role!r}")
        if len(v.binding.filter) > MAX_FILTER_KEYS:
            errors.append(f"view {v.view_id}: filter keys exceed {MAX_FILTER_KEYS}")
        if v.kind == "chart" and v.chart_kind:
            # 词表单源 chart_kinds；registry 不可用 → 跳过该检查（诚实降级，
            # 不因增值校验失败而拒合法 spec）。
            try:
                from app.lib.cartography.chart_kinds import CHART_KINDS

                if v.chart_kind not in {k.id for k in CHART_KINDS}:
                    errors.append(
                        f"view {v.view_id}: unknown chart_kind {v.chart_kind!r}")
            except Exception:  # noqa: BLE001 — 增值校验不阻断
                pass
    seen_edges: set = set()
    for r in spec.relations:
        for endpoint in (r.src, r.dst):
            if endpoint not in ids:
                errors.append(
                    f"relation {r.kind} dangling endpoint: {endpoint!r}")
        if r.src == r.dst:
            errors.append(f"relation {r.kind}: self loop at {r.src!r}")
        key = (r.src, r.dst, r.kind)
        if key in seen_edges:
            errors.append(f"duplicate relation {key}")
        seen_edges.add(key)
    for t in spec.delivery.targets:
        if t not in DELIVERY_TARGETS:
            errors.append(f"delivery: unknown target {t!r}")
    for ov in spec.overrides:
        if ov.op not in EDIT_OPS:
            errors.append(f"override: unknown op {ov.op!r}")
    errors.extend(_directional_cycle_errors(spec))
    return errors


def _directional_cycle_errors(spec: MapProductSpec) -> List[str]:
    """方向性关系（derived_statistic / overview_detail / chart_linked_to_map）
    构成有向环 → 产品无法拓扑编译，fail-closed。"""
    adj: Dict[str, List[str]] = {}
    for r in spec.relations:
        if r.kind in _DIRECTIONAL_RELATIONS:
            adj.setdefault(r.src, []).append(r.dst)
    state: Dict[str, int] = {}  # 0=unvisited 1=in-stack 2=done
    errors: List[str] = []

    def dfs(node: str, path: List[str]) -> None:
        state[node] = 1
        path.append(node)
        for nxt in adj.get(node, ()):
            if state.get(nxt, 0) == 1:
                cycle = path[path.index(nxt):] + [nxt]
                errors.append("directional relation cycle: " + " -> ".join(cycle))
            elif state.get(nxt, 0) == 0:
                dfs(nxt, path)
        path.pop()
        state[node] = 2

    for node in adj:
        if state.get(node, 0) == 0:
            dfs(node, [])
    return errors


def view_graph_edges(
    spec: MapProductSpec,
) -> List[Tuple[str, str, str]]:
    """归一化关系边（对称关系按 (min,max) 排序）——投影/校验共用单一视图。"""
    symmetric = set(RELATION_KINDS) - _DIRECTIONAL_RELATIONS
    edges: List[Tuple[str, str, str]] = []
    for r in spec.relations:
        if r.kind in symmetric:
            a, b = sorted((r.src, r.dst))
            edges.append((a, b, r.kind))
        else:
            edges.append((r.src, r.dst, r.kind))
    return edges


def storage_payload(spec: MapProductSpec) -> Dict[str, Any]:
    """chapter 持久化形态（bounded dict；digest 在此计算并随行）。"""
    return {
        "spec_version": spec.spec_version,
        "digest": spec_digest(spec),
        "spec": spec.model_dump(),
    }


def spec_from_storage(payload: Any) -> Optional[MapProductSpec]:
    """容错读取（旧会话/损坏载荷 → None，绝不抛出阻断投影）。"""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("spec") if isinstance(payload.get("spec"), dict) else payload
    try:
        spec = MapProductSpec.model_validate(raw)
    except Exception:  # noqa: BLE001 — 损坏 spec 按缺失处理（诚实降级）
        return None
    if spec.spec_version != PRODUCT_SPEC_VERSION:
        return None
    return spec


def apply_product_edit(
    spec: MapProductSpec,
    op: str,
    target: str = "",
    payload: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> Tuple[Optional[MapProductSpec], List[str], List[str]]:
    """语义编辑：spec 图局部修改 → (new_spec, errors, affected_view_ids)。

    先验证后提交：任何错误 → 返回 (None, errors, [])，原 spec 不变。
    成功 → revision+1、override 落账；未受影响视图的语义与 evidence 原样保留。
    纯函数：输入 spec 不被修改。payload 先经 op 白名单消毒（用户可控面）。
    """
    payload, sanitize_errors = _sanitize_payload(op, dict(payload or {}))
    errors: List[str] = list(sanitize_errors)
    affected: List[str] = []
    draft = spec.model_copy(deep=True)

    def _view_or_err(vid: str) -> Optional[ProductView]:
        v = draft.view(vid)
        if v is None:
            errors.append(f"view not found: {vid!r}")
        return v

    if op == "remove_view":
        v = _view_or_err(target)
        if v is not None:
            # 显式编辑撤回必需性（required 不是编辑的墙 —— 用户改主意是
            # 最高优先信号；override 账留痕，M8 不再把已撤回的构成判缺失）。
            affected = [target] + [
                r.dst if r.src == target else r.src
                for r in draft.relations if target in (r.src, r.dst)
            ]
            draft.views = [x for x in draft.views if x.view_id != target]
            draft.relations = [
                r for r in draft.relations if target not in (r.src, r.dst)]
    elif op == "add_view":
        raw = payload.get("view")
        if not isinstance(raw, dict):
            errors.append("add_view requires payload.view (dict)")
        else:
            try:
                nv = ProductView.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 — fail-closed
                errors.append(f"add_view invalid view: {exc}")
                nv = None
            if nv is not None:
                if draft.view(nv.view_id) is not None:
                    errors.append(f"view_id already exists: {nv.view_id!r}")
                elif len(draft.views) >= MAX_VIEWS:
                    errors.append(f"views exceed MAX_VIEWS({MAX_VIEWS})")
                else:
                    draft.views.append(nv)
                    affected = [nv.view_id]
    elif op == "set_view_filter":
        v = _view_or_err(target)
        filt = payload.get("filter")
        if v is not None and not isinstance(filt, dict):
            errors.append("set_view_filter requires payload.filter (dict)")
        elif v is not None:
            if len(filt) > MAX_FILTER_KEYS:
                errors.append(f"filter keys exceed {MAX_FILTER_KEYS}")
            else:
                v.binding.filter = dict(filt)
                affected = [target]
    elif op == "toggle_view":
        v = _view_or_err(target)
        if v is not None:
            if v.required and v.enabled:
                v.required = False  # 显式禁用撤回必需性（override 账留痕）
            v.enabled = not v.enabled
            affected = [target]
    elif op == "replace_component":
        # 语义面：改视图的呈现类 hint（chart_kind / component_hint）。
        # 渲染组件的物理替换仍由编译器+组件通道完成。
        v = _view_or_err(target)
        if v is None:
            pass
        else:
            if payload.get("chart_kind"):
                v.chart_kind = str(payload["chart_kind"])
                affected.append(target)
            if payload.get("component_hint"):
                v.binding.component_hint = str(payload["component_hint"])
                if target not in affected:
                    affected.append(target)
            if not affected:
                errors.append("replace_component requires chart_kind or component_hint")
    elif op == "toggle_component":
        # 组件族开/关只落 override 账（编译器按 toggle_off 抑制组件族）——
        # 视图语义保持（spec 不复制渲染态，避免第二真相）。affected = 承载
        # 该组件族的视图（披露面）。
        ctype = str(payload.get("component_type") or "")
        if not ctype:
            errors.append("toggle_component requires payload.component_type")
        else:
            for v in draft.views:
                if v.binding.component_hint == ctype or (
                    v.kind == "chart" and ctype == "chart_panel"
                ) or (v.kind == "stats_panel" and ctype == "statistics_panel"):
                    if v.required and not bool(payload.get("enabled", True)):
                        v.required = False  # 显式关闭撤回必需性（账留痕）
                    affected.append(v.view_id)
            # 组件族无视图承载（图例/指北针等 chrome）→ affected 空，纯账面。
    elif op == "set_caption":
        v = _view_or_err(target)
        text = payload.get("text")
        if v is not None and not isinstance(text, str):
            errors.append("set_caption requires payload.text (str)")
        elif v is not None:
            v.title = str(text)[:160]
            affected = [target]
    elif op == "set_delivery":
        d = payload.get("delivery")
        if not isinstance(d, dict):
            errors.append("set_delivery requires payload.delivery (dict)")
        else:
            try:
                draft.delivery = ProductDeliveryIntent.model_validate(d)
                affected = []
            except Exception as exc:  # noqa: BLE001 — fail-closed
                errors.append(f"set_delivery invalid: {exc}")
    else:
        errors.append(f"unknown edit op: {op!r}")

    if errors:
        return None, errors, []

    # Override 账裁剪：结构性账目（remove_view —— 编辑存活的撤回证据）豁免
    # 裁剪（天然有界 ≤ MAX_VIEWS）；软账目保留最近 MAX_OVERRIDES 条。
    # 否则 remove_view 账被裁后重组装会让已删视图复活（review 轴3）。
    draft.overrides.append(ProductOverride(
        op=op, target=target, payload=payload, reason=reason))
    structural_by_target: Dict[str, ProductOverride] = {}
    soft: List[ProductOverride] = []
    for ov in draft.overrides:
        if ov.op == "remove_view" and ov.target:
            structural_by_target[ov.target] = ov  # 同目标只留最新一条
        else:
            soft.append(ov)
    if len(soft) > MAX_OVERRIDES:
        soft = soft[-MAX_OVERRIDES:]
    structural = list(structural_by_target.values())
    draft.overrides = (structural + soft)[-(MAX_OVERRIDES + MAX_VIEWS):]
    draft.revision = spec.revision + 1
    structured_errors = validate_product_spec(draft)
    if structured_errors:
        return None, structured_errors, []
    return draft, [], affected
