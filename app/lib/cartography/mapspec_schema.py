"""Typed MapSpec — Authoritative Schema (V6, ADR-0120 W2).

MapSpec 契约的**唯一权威 schema**（Python Pydantic v2）。此前契约只存在于
dict 约定 + 手维护的前端 TS 镜像（frontend/lib/mapspec-compiler/types.ts），
无版本验证、无迁移、无兼容策略 —— 本模块收口：

- **typed spine**：文档骨架（version/view/sources/layers/layout/thresholds/
  layout.frames/layout.labels）严格校验；类型不符 → 结构化披露而非静默 coerce
  （coerce 会翻转渲染语义 —— R1-C2：``"false"``→``False``、``"2000"``→2000）。
- **open surfaces（诚实登记）**：paint/layout/legend_spec/style/options 等
  工具面保持 dict 开放（MapLibre 表达式与工具生态是开放词表，强类型化是
  独立 Epic）。开放面不是第二事实源 —— 权威形状以本模块为准。
- **unknown fields policy**：全模型 ``extra="allow"``（round-trip 保真，绝不
  丢用户数据）；未知键路径结构化收集，消费方 = vector-pdf 响应元数据 /
  report 元数据 / export sidecar（R1-Min3）。
- **canonical serialization**：输入 dict 的**保序深拷贝**（非 model_dump ——
  dump 会重排键序且受 lax 转换影响，R1-M1）；corpus golden 锁定
  ``dumps(canonicalize(x)) == dumps(x)``。
- **version / migration**：已知版本 {1.0, 1.1, 1.2}；1.1/1.2 纯 additive
  （1.1：frames、labels.collision、组件 options 扩展；1.2：
  layout.component_links 组件图显式边）。迁移注册表显式声明升级路径；
  未注册路径（更新版本）→ forward_version 标记（publication 消费方拒绝，
  不静默）。spec 自身 ``version`` 字段永不改写 —— 迁移是语义升级而非
  存储改写（desired-state 事实源仍是 lifecycle_engine/store）。

本模块只接**冷路径**（导出/报告/ publication 编译边界）；MapSpec mutation
热路径不经过这里（Issue #1082 读放大对齐）。
"""
from __future__ import annotations

import copy as _copy
import json as _json
from dataclasses import dataclass, field as _dc_field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

#: 数字脊柱原语：接受 int|float，拒绝 str/bool（不 coerce —— 保真披露）。
Number = Union[StrictInt, StrictFloat]

#: 已知 MapSpec 契约版本。1.0 = V5 既有面；1.1 = V6 additive；
#: 1.2 = V7 additive（layout.component_links 组件图显式边）。
KNOWN_VERSIONS: Tuple[str, ...] = ("1.0", "1.1", "1.2")
LATEST_VERSION = "1.2"

#: 版本缺省口径：lifecycle_engine 既有写入恒带 "1.0"；缺失视为 1.0 并披露。
DEFAULT_VERSION = "1.0"

#: spec 级帧数上限（与前端 frame-composer atlas ≤50 页同口径）。
MAX_SPEC_FRAMES = 50

#: 组件图显式边上限（V7：组合关系声明稀有，32 条远超合法构图需求）。
MAX_COMPONENT_LINKS = 32


class _SpecModel(BaseModel):
    """全 schema 基类：未知键保留（round-trip 保真），无静默丢弃。"""

    model_config = ConfigDict(extra="allow")


# ── 文档骨架 ──────────────────────────────────────────────────────────────


class MapView(_SpecModel):
    center: Optional[List[Number]] = None  # [lng, lat]
    zoom: Optional[Number] = None
    pitch: Optional[Number] = None
    bearing: Optional[Number] = None


class ClusterSourceConfig(_SpecModel):
    radius: Optional[Number] = None
    maxzoom: Optional[Number] = None


class GeoJSONMapSpecSource(_SpecModel):
    """geojson 源。inlineData 是开放 GeoJSON 载荷（不深校验，防 O(payload)）。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["geojson"]
    dataPath: Optional[StrictStr] = None
    url: Optional[StrictStr] = None
    inlineData: Optional[Any] = None
    cluster: Optional[ClusterSourceConfig] = None
    content_revision: Optional[Number] = None


class VectorMapSpecSource(_SpecModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["vector"]
    tiles: List[StrictStr]
    minzoom: Optional[Number] = None
    maxzoom: Optional[Number] = None


class RasterMapSpecSource(_SpecModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["raster"]
    imageRef: StrictStr
    bounds: List[Number]  # [w, s, e, n]
    imageSize: Optional[List[Number]] = None


class DataFabricMapSpecSource(_SpecModel):
    """Data Fabric lazy/materialized 协议源（ADR-0050）；键面以
    ``mapspec_source.store_data`` 实际写入为准，其余开放。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["data_fabric", "wms", "wmts", "pmtiles"]
    catalog_item_id: Optional[StrictStr] = None
    lazy: Optional[StrictBool] = None
    ref_id: Optional[StrictStr] = None
    url: Optional[StrictStr] = None
    dataPath: Optional[StrictStr] = None
    # profiler 载荷（mapspec_source.profile_data 对 fabric 源读 inlineData）
    inlineData: Optional[Any] = None
    profile: Optional[Dict[str, Any]] = None
    profile_fingerprint: Optional[StrictStr] = None
    data_fingerprint: Optional[StrictStr] = None


MapSpecSource = Union[
    GeoJSONMapSpecSource,
    VectorMapSpecSource,
    RasterMapSpecSource,
    DataFabricMapSpecSource,
]


class MapSpecLayerLabel(_SpecModel):
    field: StrictStr
    size: Optional[Any] = None
    color: Optional[Any] = None
    haloColor: Optional[StrictStr] = None
    haloWidth: Optional[Number] = None


class MapSpecLayer(_SpecModel):
    """图层。paint/layout/filter 是 MapLibre 开放面（Dict[Any]）；
    ``legend_spec`` 是工具层写入的专题图例标记（开放 dict，推导见
    render_scene.derive_legend_items —— 单源，不在本层建模）。
    ``visible`` 严格 bool：字符串 ``"false"`` 必须保留原值并披露
    （孪生渲染语义是 ``is False`` 检查 —— coerce 会静默隐藏图层）。"""

    model_config = ConfigDict(extra="allow")

    id: StrictStr
    source: StrictStr
    type: Literal["circle", "line", "fill", "symbol", "heatmap", "raster", "fill-extrusion"]
    paint: Optional[Dict[str, Any]] = None
    layout: Optional[Dict[str, Any]] = None
    label: Optional[MapSpecLayerLabel] = None
    filter: Optional[List[Any]] = None
    sourceLayer: Optional[StrictStr] = None
    cluster: Optional[ClusterSourceConfig] = None
    legend_spec: Optional[Dict[str, Any]] = None
    visible: Optional[StrictBool] = None


class MapThresholds(_SpecModel):
    """导出预算。strict 数值：字符串 ``"2000"`` 在孪生 ``_valid()`` 下被
    忽略回退默认 —— coerce 会无声激活截断（语义翻转，R1-C2 实证）。
    int|float 均合法（与孪生 ``isinstance(val, (int, float))`` 同口径）。"""

    model_config = ConfigDict(extra="allow")

    maxFeatures: Optional[Number] = None
    timeoutMs: Optional[Number] = None


class MapSpecLegendConfig(_SpecModel):
    title: Optional[StrictStr] = None
    position: Optional[Literal["top-right", "top-left", "bottom-right", "bottom-left"]] = None
    visible: Optional[StrictBool] = None


class MapSpecControlConfig(_SpecModel):
    type: Literal["navigation", "scale", "fullscreen"]
    position: Optional[Literal["top-right", "top-left", "bottom-right", "bottom-left"]] = None


class ComponentPlacement(_SpecModel):
    mode: Literal["anchor", "floating"]
    anchor: Optional[StrictStr] = None
    x: Optional[Number] = None
    y: Optional[Number] = None
    width: Optional[Number] = None
    height: Optional[Number] = None
    zIndex: Optional[Number] = None
    collapsed: Optional[StrictBool] = None


COMPONENT_TYPES = (
    "basemap",
    "legend",
    "continuous_colorbar",
    "categorical_legend",
    "north_arrow",
    "scale_bar",
    "title",
    "subtitle",
    "annotation",
    "graticule",
    "map_border",
    "attribution",
    "statistics_panel",
    "chart_panel",
    "table_panel",
    "export_layout",
    "inset_map",
    "methodology_note",
    "uncertainty_panel",
    "decision_panel",
)


class MapSpecComponent(_SpecModel):
    id: StrictStr
    type: Literal[COMPONENT_TYPES]  # type: ignore[valid-type]
    enabled: Optional[StrictBool] = None
    position: Optional[
        Literal[
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right", "none",
        ]
    ] = None
    priority: Optional[Number] = None
    style: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    compatibility: Optional[Dict[str, Any]] = None
    variant: Optional[StrictStr] = None
    placement: Optional[ComponentPlacement] = None


# ── V6 1.1 additive：spec 级 frames 与 label 配置 ────────────────────────


class LayerOverride(_SpecModel):
    """帧级图层覆写。**逐键 deep-merge** 语义（R1-M5）：只覆盖出现的键，
    未提及键保留基础 spec 值 —— 浅替换是静默数据丢失路径。"""

    model_config = ConfigDict(extra="allow")

    visible: Optional[StrictBool] = None
    opacity: Optional[Number] = None


class FramePageSize(_SpecModel):
    width: Number
    height: Number


class MapSpecFrame(_SpecModel):
    """spec 级 atlas 帧（v1.1 additive，写入面 = layout.frames，经既有
    SetLayoutIntent 字段级 merge 通道，零 lifecycle_engine 改动 —— R1-M4）。"""

    model_config = ConfigDict(extra="allow")

    id: Optional[StrictStr] = None
    title: Optional[StrictStr] = None
    view: Optional[MapView] = None
    extent: Optional[List[Number]] = None  # [w, s, e, n] 优先于 view
    layerOverrides: Optional[Dict[StrictStr, LayerOverride]] = None
    pageSize: Optional[FramePageSize] = None
    enabled: Optional[StrictBool] = None


class MapLabelConfig(_SpecModel):
    """导出标签通道（v1.1 additive）。collision 缺省 = 既有无碰撞放置
    （byte parity 保住）；``"deterministic"`` = 双孪生确定性碰撞求解。"""

    model_config = ConfigDict(extra="allow")

    collision: Optional[Literal["deterministic"]] = None
    maxLabels: Optional[StrictInt] = None


#: 组件图显式边类型（V7 Goal 08 Phase B：Component Graph）。
#: - binds_to：组件 → 图层/源的数据绑定（图例绑主题层、图表绑聚合表）
#: - requires：组件 → 组件的依赖（subtitle 依赖 title 在场）
#: - groups：容器 → 子组件的逻辑分组（版面容器）
#: - annotates：注记 → 被注记对象（callout 指向组件/图层）
#: - under：z 序约束（src 渲染在 dst 之下）
COMPONENT_LINK_TYPES = (
    "binds_to",
    "requires",
    "groups",
    "annotates",
    "under",
)

_COMPONENT_LINK_TARGET_KINDS = ("component", "layer", "source")


class ComponentLinkSpec(_SpecModel):
    """组件图显式边（v1.2 additive，写入面 = layout.component_links）。

    derived 边（options.layerId 等既有语义）不在此登记 —— 它们由
    component_graph.build_component_graph 推导；本表只承载**显式声明**
    （推导规则覆盖不了的组合关系）。src/dst 必须是已注册组件 id 或
    spec 内 layer/source id，悬空由 component_graph.validate 披露。"""

    model_config = ConfigDict(extra="allow")

    src: StrictStr
    dst: StrictStr
    type: Literal[COMPONENT_LINK_TYPES]  # type: ignore[valid-type]
    dst_kind: Optional[Literal[_COMPONENT_LINK_TARGET_KINDS]] = None  # type: ignore[valid-type]


class MapSpecLayoutConfig(_SpecModel):
    legend: Optional[MapSpecLegendConfig] = None
    controls: Optional[List[MapSpecControlConfig]] = None
    margins: Optional[Dict[str, Number]] = None  # top/right/bottom/left
    components: Optional[List[MapSpecComponent]] = None
    frames: Optional[List[MapSpecFrame]] = Field(default=None, max_length=MAX_SPEC_FRAMES)
    labels: Optional[MapLabelConfig] = None
    #: v1.2 additive：组件图显式边（有界 —— 组合关系是稀有声明，不是数据）。
    component_links: Optional[List[ComponentLinkSpec]] = Field(
        default=None, max_length=MAX_COMPONENT_LINKS)


class MapSpecDocument(_SpecModel):
    """authoritative 文档 spine。schema 权威 = 本模型族。"""

    model_config = ConfigDict(extra="allow")

    version: StrictStr
    view: Optional[MapView] = None
    sources: Optional[Dict[StrictStr, MapSpecSource]] = None
    layers: Optional[List[MapSpecLayer]] = None
    layout: Optional[MapSpecLayoutConfig] = None
    thresholds: Optional[MapThresholds] = None


#: TS 投影（W3 生成器）消费的导出面：核心文档类型 → 模型类。
SCHEMA_EXPORT_MODELS: Tuple[Tuple[str, type], ...] = (
    ("MapSpec", MapSpecDocument),
    ("MapView", MapView),
    ("MapSpecSource", None),  # union —— 生成器特判
    ("GeoJSONMapSpecSource", GeoJSONMapSpecSource),
    ("VectorMapSpecSource", VectorMapSpecSource),
    ("RasterMapSpecSource", RasterMapSpecSource),
    ("DataFabricMapSpecSource", DataFabricMapSpecSource),
    ("ClusterSourceConfig", ClusterSourceConfig),
    ("MapSpecLayer", MapSpecLayer),
    ("MapSpecLayerLabel", MapSpecLayerLabel),
    ("MapSpecLegendConfig", MapSpecLegendConfig),
    ("MapSpecControlConfig", MapSpecControlConfig),
    ("ComponentPlacement", ComponentPlacement),
    ("MapSpecComponent", MapSpecComponent),
    ("LayerOverride", LayerOverride),
    ("FramePageSize", FramePageSize),
    ("MapSpecFrame", MapSpecFrame),
    ("MapLabelConfig", MapLabelConfig),
    ("ComponentLinkSpec", ComponentLinkSpec),
    ("MapSpecLayoutConfig", MapSpecLayoutConfig),
    ("MapThresholds", MapThresholds),
)


# ── 解析结果 / 迁移 ──────────────────────────────────────────────────────


@dataclass
class FieldDisclosure:
    """一条 schema 合规披露（unknown / invalid 字段）。"""

    path: str
    kind: Literal["unknown", "invalid"]
    got: str  # 值类型名（invalid）或 ""（unknown）

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "kind": self.kind, "got": self.got}


#: 披露清单上限（R2-M4：50MB 载荷全未知键可产出百万级 disclosure ——
#: 内存/响应双放大；封顶 + truncated 标志，与 DiagnosticSink 同词汇）。
MAX_DISCLOSURES_PER_KIND = 200


@dataclass
class MapSpecParseResult:
    """parse_mapspec 产物。document 恒为输入的保序深拷贝（canonical）。"""

    document: Dict[str, Any]
    original_version: str
    effective_version: str
    migrated: bool
    valid: bool
    forward_version: bool
    unknown_fields: List[FieldDisclosure] = _dc_field(default_factory=list)
    invalid_fields: List[FieldDisclosure] = _dc_field(default_factory=list)
    disclosures_truncated: bool = False

    @property
    def disclosures(self) -> List[FieldDisclosure]:
        return self.unknown_fields + self.invalid_fields

    def disclosures_payload(self) -> List[Dict[str, str]]:
        return [d.to_dict() for d in self.disclosures]


class MapSpecSchemaError(Exception):
    """publication 消费方对不合规 spec 的显式拒绝（forward version 等）。

    携带结构化上下文；绝不静默降级（R1-C2 / R1-M4 纪律）。"""

    def __init__(self, code: str, message: str, result: Optional[MapSpecParseResult] = None):
        super().__init__(message)
        self.code = code
        self.result = result


def _version_of(payload: Any) -> Tuple[str, bool]:
    """(version, missing)。非字符串 version 视为缺失（并交由 invalid 披露）。"""
    if isinstance(payload, dict):
        v = payload.get("version")
        if isinstance(v, str):
            return v, False
        return DEFAULT_VERSION, True
    return DEFAULT_VERSION, True


_UPGRADERS: Dict[Tuple[str, str], Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # 1.1 相对 1.0 纯 additive（frames/labels 组件 options 均可选）：
    # 语义升级 = 通过校验，不需要改写文档（identity 拷贝）。
    ("1.0", "1.1"): lambda doc: doc,
    # 1.2 相对 1.1 纯 additive（layout.component_links 可选；缺省无图 =
    # 组件图全部由 derived 通道推导 —— 存量 spec 语义不变）。
    ("1.1", "1.2"): lambda doc: doc,
}


def register_upgrader(fr: str, to: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """声明显式升级路径（破坏性变更必须走这里 + 测试，禁止隐式）。"""
    _UPGRADERS[(fr, to)] = fn


def _find_upgrade_path(fr: str, to: str) -> Optional[List[Tuple[str, str]]]:
    if fr == to:
        return []
    steps: List[Tuple[str, str]] = []
    cur = fr
    seen = {cur}
    while cur != to:
        nxt = next((t for (f, t) in _UPGRADERS if f == cur and t not in seen), None)
        if nxt is None:
            return None
        steps.append((cur, nxt))
        seen.add(nxt)
        cur = nxt
    return steps


def _walk_schema_tree(annotation: Any, payload: Any, path: str, out: List[FieldDisclosure]) -> None:
    """沿 schema 已知形状收集 unknown 键（extra="allow" 值保留在文档中）。"""
    origin = _annotation_origin(annotation)
    if origin is dict:
        # Dict[str, X]：值节点按 X 递归；未知键合法（开放 dict 面）。
        args = _args_of(annotation)
        if args and len(args) == 2 and isinstance(payload, dict):
            for k, v in payload.items():
                _walk_schema_tree(args[1], v, f"{path}.{k}" if path else k, out)
        return
    if origin is list:
        args = _args_of(annotation)
        if args and isinstance(payload, list):
            for i, item in enumerate(payload):
                _walk_schema_tree(args[0], item, f"{path}[{i}]", out)
        return
    if origin is Union:
        # union：剥掉 NoneType 后单支直入；多支按"最优 BaseModel 匹配"
        # （对象载荷）或首个匹配容器分支（列表载荷）继续。
        args = [a for a in (_args_of(annotation) or ()) if a is not type(None)]
        if len(args) == 1:
            _walk_schema_tree(args[0], payload, path, out)
            return
        if isinstance(payload, dict):
            sub_models = [a for a in args if isinstance(a, type) and issubclass(a, BaseModel)]
            if sub_models:
                best, best_overlap = None, -1
                for m in sub_models:
                    overlap = len(set(payload) & set(m.model_fields))
                    if overlap > best_overlap:
                        best, best_overlap = m, overlap
                _walk_model(best, payload, path, out)
            return
        if isinstance(payload, list):
            for a in args:
                if _annotation_origin(a) is list:
                    _walk_schema_tree(a, payload, path, out)
                    return
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(payload, dict):
            _walk_model(annotation, payload, path, out)


def _walk_model(model: type, payload: Dict[str, Any], path: str, out: List[FieldDisclosure]) -> None:
    known = set(model.model_fields)
    for k, v in payload.items():
        child_path = f"{path}.{k}" if path else k
        if k not in known:
            out.append(FieldDisclosure(path=child_path, kind="unknown", got=""))
            continue
        _walk_schema_tree(model.model_fields[k].annotation, v, child_path, out)


def _annotation_origin(annotation: Any) -> Any:
    return getattr(annotation, "__origin__", None)


def _args_of(annotation: Any) -> Optional[Tuple[Any, ...]]:
    args = getattr(annotation, "__args__", None)
    return tuple(args) if args else None


#: pydantic union 失败会在 loc 尾部追加成员类型标签（'int'/'float'/...）；
#: 多成员同一处失败会生成重复 loci —— 归一到基础路径再去重。
_UNION_TAG_SEGMENTS = {"int", "float", "str", "bool", "bytes", "none", "list", "dict", "tuple"}


def _format_loc(loc: Tuple[Any, ...]) -> str:
    """ValidationError loc → 稳定路径；保留索引（layers[3].visible）。"""
    parts: List[str] = []
    for p in loc:
        if isinstance(p, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{p}]"
            else:
                parts.append(f"[{p}]")
        else:
            parts.append(str(p))
    if len(parts) >= 2 and (
        parts[-1] in _UNION_TAG_SEGMENTS or parts[-1].startswith(("literal[", "function[", "is-instance", "union_tag"))
    ):
        parts = parts[:-1]
    return ".".join(parts) if parts else "<root>"


def _collect_invalid(payload: Any) -> List[FieldDisclosure]:
    from pydantic import ValidationError

    try:
        MapSpecDocument.model_validate(payload)
        return []
    except ValidationError as e:
        out: List[FieldDisclosure] = []
        seen = set()
        for err in e.errors():
            loc = _format_loc(tuple(err.get("loc", ())))
            if loc in seen:
                continue
            seen.add(loc)
            out.append(FieldDisclosure(path=loc, kind="invalid", got=err.get("type", "")))
        return out


def parse_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> MapSpecParseResult:
    """冷路径解析：canonical 保序拷贝 + 严格校验披露 + 显式迁移。

    - 永不抛异常（结构化结果）；``forward_version`` 由调用方决定拒绝
      （publication 链用 :class:`MapSpecSchemaError` 显式拒绝）。
    - document 恒为深拷贝；调用方改写不影响输入。
    """
    if not isinstance(payload, dict):
        raise MapSpecSchemaError(
            "mapspec_not_an_object",
            f"MapSpec payload must be an object, got {type(payload).__name__}",
        )
    original_version, version_missing = _version_of(payload)
    if version_missing:
        original_version = DEFAULT_VERSION
    forward = original_version not in KNOWN_VERSIONS
    path = None if forward else _find_upgrade_path(original_version, target_version)

    doc = _copy.deepcopy(payload)
    migrated = bool(path)
    if path:
        for fr, to in path:
            doc = _UPGRADERS[(fr, to)](doc)

    unknown: List[FieldDisclosure] = []
    _walk_model(MapSpecDocument, doc, "", unknown)
    invalid = _collect_invalid(doc)
    truncated = len(unknown) > MAX_DISCLOSURES_PER_KIND or len(invalid) > MAX_DISCLOSURES_PER_KIND
    unknown = unknown[:MAX_DISCLOSURES_PER_KIND]
    invalid = invalid[:MAX_DISCLOSURES_PER_KIND]

    return MapSpecParseResult(
        document=doc,
        original_version=original_version,
        effective_version=target_version if path else original_version,
        migrated=migrated,
        valid=not invalid,
        forward_version=forward,
        unknown_fields=unknown,
        invalid_fields=invalid,
        disclosures_truncated=truncated,
    )


def require_parseable_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> MapSpecParseResult:
    """publication 消费入口：forward version / 非对象 → 显式拒绝。"""
    result = parse_mapspec(payload, target_version=target_version)
    if result.forward_version:
        raise MapSpecSchemaError(
            "mapspec_forward_version",
            f"MapSpec version {result.original_version!r} is newer than supported "
            f"{sorted(KNOWN_VERSIONS)}; refusing to compile with wrong semantics",
            result,
        )
    return result


def canonicalize_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> Dict[str, Any]:
    """canonical 形式 = 校验 + 迁移后的**保序深拷贝**。

    有意不使用 ``model_dump()``：dump 会按声明序重排键（声明字段先、
    extras 后）且受 lax 转换影响 —— canonical 合同是"输入序忠实拷贝"
    （R1-M1），golden 锁定 ``dumps(canonicalize(x)) == dumps(x)``。
    """
    return require_parseable_mapspec(payload, target_version=target_version).document


def dumps_canonical(document: Dict[str, Any]) -> str:
    """canonical JSON 序列化（稳定格式：紧凑分隔 + 不转义非 ASCII）。"""
    return _json.dumps(document, ensure_ascii=False, separators=(",", ":"))
