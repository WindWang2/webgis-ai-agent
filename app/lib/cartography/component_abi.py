"""Component ABI v1 — 组件类型的 versioned 契约投影（ADR-0214 D1）.

F11 基座的第一块：把散落在 descriptor / renderer 真值矩阵 / 组件模板
default_options 里的**类型级契约**投影为一份单一、versioned、可校验的
``ComponentABIRecord``（id/type/version/slots/layout intent/export
support/accessibility/compatibility/dependencies/defaults）。

定位红线（ADR-0214 D1）：

- **投影，不是第二注册表**——单一事实仍在 component_registry（descriptor）、
  component_renderers（live/export 真值矩阵）、component_templates
  （variant/default_options）；本模块只读它们，从不改写。
- props schema 采用**有界受限词汇**（type/enum/default/min/max/max_len），
  只覆盖 agent 写入面（options）；不做全量 JSON Schema，不核验运行时
  open dict。字段声明必须有写入面证据（seed 模板 default_options 或
  前端 renderer 消费键），自检测试锁漂移。
- fail-closed：每个注册的 native 组件类型必须有 ABI 元数据条目
  （``validate_component_abi``；由 ``ComponentRegistry.validate`` 接线），
  条目引用未注册类型 → ``abi_meta_orphan``。本表不扩 ComponentType 词表
  （ADR-0101/0103 非目标）。

纯函数、确定性、有界载荷；本模块不做布局、不做渲染、不改 MapSpec。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field

#: ABI 形状版本：本模块产出的记录结构/语义变化时 bump（当前 v1）。
COMPONENT_ABI_VERSION = 1

#: 默认组件语义版本（首个发布态；props/渲染契约破坏性变化时按类型 bump）。
_DEFAULT_COMPONENT_VERSION = "1.0.0"

#: 组件实例 id 惯例（type → 实例 id 基名；ADR-0214 D2 起为跨模块单一事实，
#: 原 gis_harness/component_composer._component_id_for_type 映射提升至此，
#: 未列类型回退 type 本身）。实例 id 是 MapSpec 存储契约的一部分：改表
#: 会改变新实例的默认寻址（存量 spec 不受影响）。
INSTANCE_ID_BY_TYPE: Dict[str, str] = {
    "title": "title",
    "subtitle": "subtitle",
    "legend": "legend-main",
    "continuous_colorbar": "colorbar-main",
    "categorical_legend": "legend-categorical",
    "north_arrow": "north-arrow",
    "scale_bar": "scale-bar",
    "attribution": "attribution",
    "graticule": "graticule",
    "map_border": "map-border",
    "statistics_panel": "statistics",
    "chart_panel": "chart-panel",
    "export_layout": "export-layout",
    "annotation": "annotation",
    "inset_map": "inset-map",
}


def instance_id_for_type(ctype: str) -> str:
    """实例 id 基名（契约 apply 与 harness composer 共用单一真值）。"""
    return INSTANCE_ID_BY_TYPE.get(ctype, ctype)

#: 有界词表封顶。
MAX_PROPS_FIELDS = 16
MAX_SLOTS = 8
MAX_ENUM = 16
MAX_ABI_ISSUES = 32

#: 受限类型词汇（ deliberately 不开放任意嵌套 —— agent 写入面够用且有界 ）。
PropTypeName = Literal["str", "int", "float", "bool", "str_list"]
_PROP_TYPE_MAP = {
    "str": str, "int": int, "float": float, "bool": bool, "str_list": list,
}


class PropsFieldSpec(BaseModel):
    """一个 options 字段的有界声明。"""

    type: PropTypeName
    required: bool = False
    default: Optional[Union[str, int, float, bool, None]] = None
    enum: Tuple[Union[str, int, float], ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None
    max_len: int = 64          # str 长度 / str_list 元素数封顶
    description: str = ""


class ComponentABIRecord(BaseModel):
    """组件类型的 versioned 契约投影（agent discovery / replace 消费面）。"""

    id: str
    type: str
    version: str                       # 组件语义版本（ABI meta 单一事实）
    abi_version: int = COMPONENT_ABI_VERSION
    category: str
    semantic_role: str
    #: 该类型可担任的 composition slot id（从 composition 模板注册表投影；
    #: 空 = 未被任何模板槽位引用）。
    slots: Tuple[str, ...] = ()
    placement_domain: str = "overlay"
    allowed_positions: Tuple[str, ...] = ()
    default_position: str = "none"
    collision_class: str = "panel"
    responsive: str = "none"
    size_range: Dict[str, Any] = Field(default_factory=dict)
    renderer_support: Tuple[str, ...] = ()
    exporter_support: Tuple[str, ...] = ()
    supported_outputs: Tuple[str, ...] = ()
    accessibility: Dict[str, Any] = Field(default_factory=dict)
    compatible_map_models: Tuple[str, ...] = ()
    runtime_status: str = "native"
    deprecated: bool = False
    deprecated_by: str = ""
    dependencies: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()
    default_variant: str = "default"
    variants: Tuple[str, ...] = ()
    states: Tuple[str, ...] = ()
    #: options 默认值投影（来自该类型全部 seed 模板 default_options 的
    #: 键级并集；值冲突不裁 —— 只投影键与无冲突值，冲突键值省略）。
    default_props: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        """有界序列化（agent 工具载荷契约；字段封顶、字符串截断）。"""
        return {
            "id": self.id[:48], "type": self.type[:32],
            "version": self.version[:16],
            "abi_version": self.abi_version,
            "category": self.category[:48],
            "semantic_role": self.semantic_role[:24],
            "slots": [s[:48] for s in self.slots[:MAX_SLOTS]],
            "placement_domain": self.placement_domain[:16],
            "allowed_positions": [p[:16] for p in self.allowed_positions[:8]],
            "default_position": self.default_position[:16],
            "renderer_support": list(self.renderer_support[:8]),
            "exporter_support": list(self.exporter_support[:8]),
            "runtime_status": self.runtime_status[:16],
            "deprecated": self.deprecated,
            "deprecated_by": self.deprecated_by[:48],
            "dependencies": [d[:32] for d in self.dependencies[:8]],
            "conflicts": [c[:32] for c in self.conflicts[:8]],
            "default_variant": self.default_variant[:32],
            "variants": [v[:32] for v in self.variants[:16]],
        }


class ComponentABIMeta(BaseModel):
    """每类型的版本 + props schema（ABI 表静态事实；手审、测试锁漂移）。"""

    version: str = _DEFAULT_COMPONENT_VERSION
    props_schema: Dict[str, PropsFieldSpec] = Field(default_factory=dict)


def _f(
    type_: PropTypeName,
    *,
    required: bool = False,
    default: Optional[Union[str, int, float, bool]] = None,
    enum: Tuple[Union[str, int, float], ...] = (),
    min: Optional[float] = None,
    max: Optional[float] = None,
    max_len: int = 64,
    description: str = "",
) -> PropsFieldSpec:
    return PropsFieldSpec(
        type=type_, required=required, default=default, enum=enum,
        min=min, max=max, max_len=max_len, description=description)


_NORTH_ARROW_VARIANTS = (
    "compass_minimal_black", "arrow_simple", "compass_needle", "compass_rose")

#: props schema 单一事实（options 键级）。证据基线：
#: - seed 组件模板 ``default_options``（component_templates.py，写入面）；
#: - 前端 chrome renderer 实际消费键（title/north-arrow/scale-bar/legends/
#:   colorbar/annotation/attribution/statistics-panel/graticule/map-border/
#:   inset-map.tsx；如 north_arrow.showDeclination / colorbar.ticks）。
#: 新增键必须同时有写入面/消费面证据并更新本表（validate_component_abi
#: 与自检测试双锁）。
COMPONENT_ABI_META: Dict[str, ComponentABIMeta] = {
    "north_arrow": ComponentABIMeta(props_schema={
        "variant": _f("str", enum=_NORTH_ARROW_VARIANTS, default="compass_minimal_black",
                      description="指北针样式变体"),
        "showDeclination": _f("bool", default=False, description="显示磁偏角"),
    }),
    "scale_bar": ComponentABIMeta(props_schema={
        "orientation": _f("str", enum=("horizontal", "vertical"), default="horizontal"),
        "unit": _f("str", enum=("metric", "imperial"), default="metric"),
        "style": _f("str", enum=("minimal", "boxed", "academic"), default="minimal"),
    }),
    "legend": ComponentABIMeta(props_schema={
        "orientation": _f("str", enum=("horizontal", "vertical"), default="vertical"),
        "style": _f("str", enum=("academic", "compact", "report"), default="academic"),
    }),
    "categorical_legend": ComponentABIMeta(props_schema={
        "orientation": _f("str", enum=("horizontal", "vertical"), default="vertical"),
        "style": _f("str", max_len=32, description="分类图例样式变体"),
    }),
    "continuous_colorbar": ComponentABIMeta(props_schema={
        "orientation": _f("str", enum=("horizontal", "vertical"), default="horizontal"),
        "style": _f("str", enum=("default", "slim"), max_len=32, default="default"),
        "ticks": _f("int", min=2, max=16, default=5, description="刻度数"),
    }),
    "annotation": ComponentABIMeta(props_schema={
        "variant": _f("str", max_len=32, description="注记形态变体"),
        "positionHint": _f("str", max_len=32, description="位置提示（renderer 消费）"),
    }),
    "graticule": ComponentABIMeta(props_schema={
        "variant": _f("str", max_len=32),
        "style": _f("str", max_len=32),
        "opacity": _f("float", min=0.0, max=1.0, default=1.0),
    }),
    "inset_map": ComponentABIMeta(props_schema={
        "variant": _f("str", max_len=32, description="区位插图变体"),
    }),
    "map_border": ComponentABIMeta(props_schema={
        "style": _f("str", max_len=32, description="图框样式（frame/* 模板族）"),
    }),
    "chart_panel": ComponentABIMeta(props_schema={
        "chartType": _f("str", max_len=24, description="图表类型（chart_kinds 词表）"),
    }),
    "export_layout": ComponentABIMeta(props_schema={
        "paperSize": _f("str", max_len=16, default="A4", description="页面规格"),
        "orientation": _f("str", enum=("portrait", "landscape"), default="landscape"),
        "dpi": _f("int", min=72, max=600, default=300),
    }),
    "label_layer": ComponentABIMeta(props_schema={
        "mode": _f("str", max_len=16, description="标注策略模式"),
        "topN": _f("int", min=1, max=200, default=30),
        "auto": _f("bool", default=True),
    }),
    "methodology_note": ComponentABIMeta(props_schema={
        "style": _f("str", max_len=32),
    }),
    # options 键级为空的类型：显式空表（有 ABI 条目 = 版本可被身份块捕获）。
    # 注：basemap 是 renderer 矩阵占位（EXPORT_PARITY_EXEMPT_TYPES），非
    # 注册 descriptor，不入本表 —— 见 validate_component_abi orphan 审计。
    "title": ComponentABIMeta(),
    "subtitle": ComponentABIMeta(),
    "attribution": ComponentABIMeta(),
    "statistics_panel": ComponentABIMeta(),
    "table_panel": ComponentABIMeta(),
    "uncertainty_panel": ComponentABIMeta(),
    "decision_panel": ComponentABIMeta(),
}


# ── props 校验（agent 写入面前置；结构化 reason，不静默丢弃）────────────


def validate_props(
    component_type: str, props: Dict[str, Any]
) -> List[str]:
    """对 agent 提交的 options 载荷做前置校验。

    返回结构化 issue 列表（``props_invalid:<field>:<reason>``）；空列表 =
    通过。语义：声明字段按 schema 校验；**未声明字段不在此否决**（options
    是 open dict，运行时消费面更宽）——本函数只守 agent 写入面的类型/边界/
    枚举契约。
    """
    meta = COMPONENT_ABI_META.get(component_type)
    if meta is None:
        return [f"props_invalid:{component_type[:32]}:abi_meta_missing"]
    issues: List[str] = []
    if not isinstance(props, dict):
        return ["props_invalid:__:not_an_object"]
    # required 缺失键先行（review P2-3：声明 required 从未被消费 → 现在
    # 有界检查；存量 schema 均未用 required=True，零行为变化）。
    for key in sorted(meta.props_schema):
        spec = meta.props_schema[key]
        if spec.required and key not in props:
            issues.append(f"props_invalid:{key}:missing_required")
    for key in sorted(props.keys())[:MAX_PROPS_FIELDS * 2]:
        spec = meta.props_schema.get(str(key))
        if spec is None:
            continue
        value = props[key]
        py_type = _PROP_TYPE_MAP[spec.type]
        if spec.type == "int" and isinstance(value, bool):
            issues.append(f"props_invalid:{key}:bool_not_int")
            continue
        if spec.type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(f"props_invalid:{key}:not_a_number")
                continue
        elif not isinstance(value, py_type):
            issues.append(f"props_invalid:{key}:expected_{spec.type}")
            continue
        if spec.type == "str":
            if len(value) > spec.max_len:
                issues.append(f"props_invalid:{key}:too_long")
                continue
            if spec.enum and value not in spec.enum:
                issues.append(f"props_invalid:{key}:not_in_enum")
                continue
        elif spec.type == "str_list":
            if len(value) > spec.max_len:
                issues.append(f"props_invalid:{key}:too_long")
                continue
            if not all(isinstance(v, str) and len(v) <= 64 for v in value):
                issues.append(f"props_invalid:{key}:bad_element")
                continue
        elif spec.type == "int":
            # review P2-3：int 枚举判定必须先于数值范围（原分支位于
            # ("int","float") 之后，永不可达）。
            if spec.enum and value not in spec.enum:
                issues.append(f"props_invalid:{key}:not_in_enum")
                continue
            if spec.min is not None and value < spec.min:
                issues.append(f"props_invalid:{key}:below_min")
                continue
            if spec.max is not None and value > spec.max:
                issues.append(f"props_invalid:{key}:above_max")
                continue
        elif spec.type == "float":
            num = float(value)
            if spec.min is not None and num < spec.min:
                issues.append(f"props_invalid:{key}:below_min")
                continue
            if spec.max is not None and num > spec.max:
                issues.append(f"props_invalid:{key}:above_max")
                continue
    return issues[:MAX_PROPS_FIELDS]


def component_version(component_type: str) -> str:
    """类型语义版本（身份块/指纹捕获用；未知类型回退默认版本）。"""
    meta = COMPONENT_ABI_META.get(component_type)
    return meta.version if meta is not None else _DEFAULT_COMPONENT_VERSION


def versions_projection(component_types: List[str], *, cap: int = 32) -> Dict[str, str]:
    """type → version 有界投影（layout.composition.component_versions）。"""
    out: Dict[str, str] = {}
    for ctype in component_types[:cap * 2]:
        key = str(ctype)[:32]
        if key and key not in out:
            out[key] = component_version(key)
        if len(out) >= cap:
            break
    return dict(sorted(out.items()))


# ── ABI 记录投影（descriptor + 真值矩阵 + 模板注册表 → 记录）────────────


def _compatible_slots(component_type: str) -> Tuple[str, ...]:
    """类型可担任的 composition slot id（确定性、有界）。

    从 composition 模板注册表扫描 ``allowed_component_types``；slot id
    跨模板去重排序。composition 注册表是模块级确定性单例，扫描只发生在
    投影时（O(模板×槽位)，目录级小常数）。
    """
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    slot_ids: set = set()
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            if component_type in slot.allowed_component_types:
                slot_ids.add(slot.id)
    return tuple(sorted(slot_ids)[:MAX_SLOTS])


def abi_record_for(descriptor: Any) -> ComponentABIRecord:
    """descriptor（+ 真值矩阵投影）→ ABI 记录。descriptor 不可改动。"""
    ctype = descriptor.type
    meta = COMPONENT_ABI_META.get(ctype)
    # props 默认值投影：schema 声明默认值（单一事实；模板级差异值不裁）。
    defaults: Dict[str, Any] = {}
    if meta is not None:
        for key in sorted(meta.props_schema):
            spec = meta.props_schema[key]
            if spec.default is not None:
                defaults[key] = spec.default
    return ComponentABIRecord(
        id=descriptor.id,
        type=ctype,
        version=meta.version if meta else _DEFAULT_COMPONENT_VERSION,
        category=descriptor.category,
        semantic_role=getattr(descriptor, "semantic_role", ""),
        slots=_compatible_slots(ctype),
        placement_domain=descriptor.placement_domain,
        allowed_positions=tuple(descriptor.allowed_positions),
        default_position=descriptor.default_position,
        collision_class=descriptor.collision_class,
        responsive=descriptor.responsive,
        size_range=descriptor.size_range.model_dump() if getattr(descriptor, "size_range", None) else {},
        renderer_support=tuple(descriptor.renderer_support),
        exporter_support=tuple(descriptor.exporter_support),
        supported_outputs=tuple(descriptor.supported_outputs),
        accessibility=descriptor.accessibility.model_dump() if getattr(descriptor, "accessibility", None) else {},
        compatible_map_models=tuple(descriptor.compatible_map_models),
        runtime_status=descriptor.runtime_status,
        deprecated=descriptor.deprecated,
        deprecated_by=descriptor.deprecated_by,
        dependencies=tuple(descriptor.dependencies),
        conflicts=tuple(descriptor.conflicts),
        default_variant=descriptor.default_variant,
        variants=tuple(descriptor.variants),
        states=tuple(descriptor.states),
        default_props=defaults,
    )


# ── fail-closed 校验（ComponentRegistry.validate 接线）──────────────────


def validate_component_abi(registry: Any) -> List[str]:
    """ABI 表 ↔ 注册表交叉审计（fail-closed；返回 issue 字符串列表）。

    - ``abi_meta_missing``：注册表 native 组件类型无 ABI 条目；
    - ``abi_meta_orphan``：ABI 条目引用未注册类型；
    - ``abi_props_ungrounded``：props schema 声明了写入面不存在的键
      （对齐 seed default_options 与已知 renderer 消费键的白名单）。
    """
    issues: List[str] = []
    try:
        native_types = {d.type for d in registry.native_descriptors()}
        for ctype in sorted(native_types):
            if ctype not in COMPONENT_ABI_META:
                issues.append(f"abi_meta_missing: {ctype}")
        for ctype in sorted(COMPONENT_ABI_META):
            if registry.get_by_type(ctype) is None:
                issues.append(f"abi_meta_orphan: {ctype}")
        # props 键接地自检：声明键必须出现在 seed default_options 或
        # renderer 消费键白名单（防 schema 漂移成空头契约）。
        grounded = _grounded_option_keys()
        for ctype, meta in sorted(COMPONENT_ABI_META.items()):
            for key in sorted(meta.props_schema):
                if key not in grounded.get(ctype, set()):
                    issues.append(f"abi_props_ungrounded: {ctype}.{key}")
    except Exception as e:  # pragma: no cover - 防御性
        issues.append(f"abi validation error: {e}")
    return issues[:MAX_ABI_ISSUES]


_RENDERER_CONSUMED_KEYS: Dict[str, set] = {
    # 前端 chrome renderer 实测消费键（超出 seed default_options 的部分）。
    "north_arrow": {"showDeclination"},
    "continuous_colorbar": {"ticks"},
    "graticule": {"opacity"},
}


def _grounded_option_keys() -> Dict[str, set]:
    """type → 写入面/消费面证据键集（default_options ∪ renderer 消费键）。"""
    grounded: Dict[str, set] = {}
    try:
        from app.lib.cartography.component_templates import SEED_COMPONENT_TEMPLATES
        for t in SEED_COMPONENT_TEMPLATES:
            grounded.setdefault(t.component_type, set()).update(
                t.default_options.keys())
    except Exception:  # pragma: no cover - 防御性
        pass
    for ctype, keys in _RENDERER_CONSUMED_KEYS.items():
        grounded.setdefault(ctype, set()).update(keys)
    return grounded


__all__ = [
    "COMPONENT_ABI_VERSION",
    "COMPONENT_ABI_META",
    "INSTANCE_ID_BY_TYPE",
    "ComponentABIRecord",
    "ComponentABIMeta",
    "PropsFieldSpec",
    "abi_record_for",
    "validate_component_abi",
    "validate_props",
    "component_version",
    "instance_id_for_type",
    "versions_projection",
]
