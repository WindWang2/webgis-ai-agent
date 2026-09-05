"""声明式工具参数归一化层（ADR-0101 Wave 2, §10）。

历史回顾：registry._normalize_tool_arguments 此前是按工具硬编码的 if 链
（geojson 家族、POI、行政区划、缓冲区、专题图、地图产品、图层 upsert），
行为正确但不可维护：新增工具接 geojson 需改 registry 核心；别名与 schema
漂移只能靠回归测试兜底；无修复留痕（LLM 为什么传错名、系统改了什么，
trace 不可见）。

本模块把同一行为重写为**声明式规则表**：

- ``TOOL_NAME_ALIASES``：入向工具名别名 → canonical 名（原 _TOOL_NAME_ALIASES）。
- ``GLOBAL_GEOJSON_RULE``：geojson 别名家族 —— 适用条件是「args model 声明了
  ``geojson`` 字段」或「工具在显式名单内」（与旧硬编码名单逐字一致）。
- ``FIELD_RULES_BY_TOOL``：每工具的字段折叠/取值矫正规则，声明式、有序、
  有界（每条规则至多折叠一个值，无递归、无语义改写）。

不变式（全部有契约测试钉住）：
1. 声明字段（schema 正名）绝不被折叠 —— LLM 按 schema 命名的参数永远优先
   （master 回归：webgis_map_product 的 title 曾被改名成不存在的 map_title）。
2. 保护性 ref 游标字段（geojson_ref 等）一旦被工具声明，同样不被折叠。
3. 无语义猜测：只做名称/形态归一，不改数值含义（单位/坐标系永不动）。
4. 确定性：规则按声明序展开，同输入必同输出。
5. 可追溯：每次归一化产出 ``ArgRepair`` 列表（kind/source/target），
   dispatch 经 ``normalization_report_var`` ContextVar 暴露给 trace。
"""
from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type, Union

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 修复证据
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArgRepair:
    """单次参数修复记录（trace/replay/评测共用词汇）。"""

    kind: str          # key_style | field_alias | value_coercion | json_string_list
    source: str        # 原始键/来源
    target: str        # 归一化目标键
    detail: str = ""   # 人类可读补充（bounded）

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "source": self.source,
                "target": self.target, "detail": self.detail}


#: 当前 dispatch 的归一化报告（registry 写入，trace 读取；未在 dispatch 中则空）。
normalization_report_var: contextvars.ContextVar[Tuple[ArgRepair, ...]] = contextvars.ContextVar(
    "tool_normalization_report", default=()
)


# ---------------------------------------------------------------------------
# 规则模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldAliasRule:
    """字段折叠：``target`` 缺席且某别名在场（且别名非声明字段）时折叠过去。

    list_wrap=True 时标量包成单元素列表（overlay_refs / multi_ring_buffer
    的 distances 语义）。requires_target_declared=True 时仅当 args model
    声明了 target 字段才适用（search_poi 的 subtype/district/adcode 家族
    语义 —— 泛 keyword 工具只在真的有该字段时才吃别名）。
    """

    target: str
    aliases: Tuple[str, ...]
    list_wrap: bool = False
    requires_target_declared: bool = False
    kind: str = "field_alias"


@dataclass(frozen=True)
class ValueCoercionRule:
    """取值矫正：别名键的值满足谓词时取值（而非折叠键名）后删除别名键。

    现存唯一实例是 n_classes 的数值形态（num_classes/bins/k/...）。
    """

    target: str
    aliases: Tuple[str, ...]
    accept: Any                     # callable(value) -> bool
    transform: Any                  # callable(value) -> value
    kind: str = "value_coercion"


Rule = object  # FieldAliasRule | ValueCoercionRule（仅文档用途）


# ---------------------------------------------------------------------------
# 工具名别名（原 registry._TOOL_NAME_ALIASES，逐字迁移）
# ---------------------------------------------------------------------------

TOOL_NAME_ALIASES: Dict[str, str] = {
    # 行政边界与政区查询别名
    "admin_boundary_query": "get_local_admin_boundary",
    "get_admin_boundary": "get_local_admin_boundary",
    "query_admin_boundary": "get_local_admin_boundary",
    "admin_boundary": "get_local_admin_boundary",
    "admin_query": "get_admin_division",
    "query_admin_division": "get_admin_division",
    "get_boundary": "get_local_admin_boundary",
    "get_child_district": "get_child_districts",
    "get_local_districts": "get_local_child_districts",
    "get_districts": "get_child_districts",

    # POI 查询别名
    "poi_query": "search_poi",
    "query_poi": "search_poi",
    "poi_search": "search_poi",
    "search_pois": "search_poi",
    "query_osm_pois": "query_osm_poi",

    # 密度/表面分析别名
    "density_surface": "kde_surface",
    "density_analysis": "kde_surface",
    "kernel_density": "kde_surface",
    "kernel_density_estimation": "kde_surface",
    "heatmap_analysis": "heatmap_data",
    "kde_analysis": "kde_surface",

    # 空间聚合别名
    "admin_aggregation": "spatial_aggregate",
    "spatial_aggregation": "spatial_aggregate",
    "admin_aggregate": "spatial_aggregate",
    "point_aggregation": "spatial_aggregate",
    "aggregate_points": "spatial_aggregate",

    # 缓冲区别名
    "buffer": "buffer_analysis",
    "buffer_layer": "buffer_analysis",

    # 路径/网络分析别名
    "shortest_path": "network_shortest_path",
    "route_planning": "plan_route",
    "isochrone": "isochrone_analysis",

    # 空间叠加/属性
    "overlay": "overlay_analysis",
    "spatial_join_layers": "spatial_join",
    "zonal_statistics": "zonal_stats",
}


def resolve_tool_name(name: str) -> str:
    """入向工具名 → canonical 名（无别名时原样返回）。"""
    return TOOL_NAME_ALIASES.get(name, name)


# ---------------------------------------------------------------------------
# geojson 全局别名家族（原 GEOJSON_ALIASES + 硬编码工具名单，逐字迁移）
# ---------------------------------------------------------------------------

GEOJSON_ALIASES: Tuple[str, ...] = (
    "geojson_ref",
    "data_ref",
    "source_ref",
    "input_geojson",
    "points_geojson",
    "target_geojson",
    "source_geojson",
    "layer_data",
    "points_data",
    "data",
    "input_data",
    "feature_collection",
    "features",
    "points",
    "ref",
    "ref_id",
    "layer_ref",
)

#: args model 未声明 geojson 字段时，仍适用 geojson 家族的工具名单
#（原 registry 内联硬编码名单，逐字迁移）。
GEOJSON_FAMILY_TOOLS: frozenset = frozenset({
    "heatmap_data", "buffer_analysis", "spatial_stats", "nearest_neighbor",
    "kde_surface", "kde_contours", "voronoi_polygons", "convex_hull",
    "multi_ring_buffer", "attribute_filter", "h3_binning", "spatial_cluster",
    "hotspot_analysis", "moran_i", "create_thematic_map", "isochrone_analysis",
    "service_area_simple", "point_profile",
})


# ---------------------------------------------------------------------------
# 每工具字段规则（原 _normalize_tool_arguments 分支 3-9，逐条迁移）
# 声明序即应用序 —— 与旧 if 链的折叠顺序逐字一致。
# ---------------------------------------------------------------------------

FIELD_RULES_BY_TOOL: Dict[str, Tuple[Any, ...]] = {
    # 3. 空间聚合
    "spatial_aggregate": (
        FieldAliasRule("points", (
            "points_data", "points_ref", "points_geojson", "point_data",
            "data", "geojson", "geojson_ref", "ref")),
        FieldAliasRule("polygons", (
            "polygons_data", "polygons_ref", "polygons_geojson", "polygon_data",
            "admin_data", "admin_boundary", "boundary_ref", "boundary",
            "admin_boundary_ref", "data", "geojson", "geojson_ref", "ref")),
    ),
    # 4. POI 搜索
    "search_poi": (
        FieldAliasRule("keyword", ("keywords", "query", "text", "search_text", "name")),
        # 注意：search_poi 真实签名声明了 ``city`` 自身字段 —— city 不是
        # district 的别名（声明字段永不折叠，一致性门 test 锁定）。
        FieldAliasRule("subtype", ("poi_type", "type", "category", "sub_type",
                                   "class_name", "type_name"),
                       requires_target_declared=True),
        FieldAliasRule("district", ("district_name", "city_name", "region",
                                    "admin_name", "address"),
                       requires_target_declared=True),
        FieldAliasRule("adcode", ("ad_code", "city_code", "district_code", "code"),
                       requires_target_declared=True),
    ),
    "query_local_poi": (
        FieldAliasRule("keyword", ("keywords", "query", "text", "search_text", "name")),
        # query_local_poi 真实签名声明了 ``category`` —— category 不进 subtype 别名。
        FieldAliasRule("subtype", ("poi_type", "type", "sub_type",
                                   "class_name", "type_name")),
        FieldAliasRule("district", ("district_name", "city_name", "city", "region",
                                    "admin_name", "address")),
        FieldAliasRule("adcode", ("ad_code", "city_code", "district_code", "code")),
    ),
    # 5. 行政区划查询（get_admin_division 的目标键是 keywords，其余是 name）
    "get_admin_division": (
        FieldAliasRule("keywords", (
            "admin_name", "city", "district", "district_name", "region", "address",
            "location", "query", "name")),
        FieldAliasRule("adcode", ("ad_code", "city_code", "district_code", "code")),
    ),
    "get_local_admin_boundary": (
        FieldAliasRule("name", (
            "admin_name", "city", "district", "district_name", "region", "address",
            "location", "query", "keywords")),
        FieldAliasRule("adcode", ("ad_code", "city_code", "district_code", "code")),
    ),
    # 6. 缓冲区分析
    "buffer_analysis": (
        FieldAliasRule("distance", ("buffer_distance", "dist", "radius", "buffer_radius")),
        FieldAliasRule("unit", ("dist_unit", "buffer_unit")),
    ),
    "multi_ring_buffer": (
        FieldAliasRule("distances", ("ring_distances", "radii", "distance_list",
                                     "distance"), list_wrap=True),
    ),
    # 7. 专题图与模板。create_thematic_map 真实签名声明了 ``k``（类数）——
    #    k 不进 n_classes 别名（声明字段永不折叠）。apply_template 不收
    #    n_classes：旧表对其做数值矫正会凭空造出未知参数 n_classes，把
    #    「LLM 误传 bins」误导成「缺 n_classes」——矫正规则已移除，未知
    #    参数门直接以真实错误（bins 不被接受）自愈引导。
    "create_thematic_map": (
        FieldAliasRule("field", ("classify_field", "field_name", "property",
                                 "property_name", "column", "attribute", "attr")),
        FieldAliasRule("palette", ("color_palette", "color_scheme", "colors",
                                   "colormap", "color")),
        FieldAliasRule("method", ("classify_method", "classification",
                                  "classes_method", "scheme")),
        ValueCoercionRule("n_classes", ("num_classes", "bins", "class_count",
                                        "classes"),
                          accept=lambda v: isinstance(v, (int, float)),
                          transform=lambda v: int(v)),
    ),
    "apply_template": (
        FieldAliasRule("field", ("classify_field", "field_name", "property",
                                 "property_name", "column", "attribute", "attr")),
        FieldAliasRule("palette", ("color_palette", "color_scheme", "colors",
                                   "colormap", "color")),
        FieldAliasRule("method", ("classify_method", "classification",
                                  "classes_method", "scheme")),
    ),
    # 8. 地图产品装配（目标必须是真实签名参数；map_title 是入向别名）
    "webgis_map_product": (
        FieldAliasRule("title", ("map_title", "name", "project_title")),
        FieldAliasRule("primary_ref", ("primary_layer", "primary_source", "base_ref",
                                       "base_layer", "main_ref", "ref", "geojson_ref")),
        FieldAliasRule("overlay_refs", ("overlays", "overlay_layers", "layers",
                                        "other_refs", "sub_refs"), list_wrap=True),
    ),
    # 9. 图层增删改
    "webgis_layer_upsert": (
        FieldAliasRule("source_data", ("data", "source_ref", "geojson_ref", "geojson",
                                       "ref", "layer_data")),
    ),
}

#: 旧实现的兼容入口名（POI 四工具共享 keyword 折叠 —— query_poi 是 search_poi
# 的工具名别名，归一化在名字折叠后调用，但旧代码对四个名字都注册了规则；
# admin_boundary_query 同理是 get_local_admin_boundary 的别名。保留显式条目
# 以兼容直接以旧名调用归一化的测试路径）。
FIELD_RULES_BY_TOOL["query_poi"] = FIELD_RULES_BY_TOOL["search_poi"]
FIELD_RULES_BY_TOOL["admin_boundary_query"] = FIELD_RULES_BY_TOOL["get_local_admin_boundary"]


# ---------------------------------------------------------------------------
# 归一化执行
# ---------------------------------------------------------------------------

def _is_list_annotation(annotation: Any) -> bool:
    """annotation 是否为 list 族（list / list[T] / List[T] / Optional[List[T]]）。"""
    import typing as _typing

    ann = annotation
    if _typing.get_origin(ann) is Union:
        return any(_is_list_annotation(a) for a in _typing.get_args(ann))
    if ann is list or ann is List:
        return True
    return _typing.get_origin(ann) is list


def coerce_json_string_lists(
    arguments: dict, model: Type[BaseModel]
) -> Tuple[dict, Tuple[ArgRepair, ...]]:
    """列表参数的 JSON 字符串宽容解码（原 registry._coerce_json_string_lists）。

    LLM 常把本该是数组的参数编码成 JSON 字符串（``"[\"a\",\"b\"]"``）——
    pydantic 拒以 ``Input should be a valid list``，且模型自愈重试不改形。
    凡模型字段注解为 list 族、实参是 str 且 json.loads 结果确为 list 的，
    就地解码替换；其余形态不动，留给 pydantic 出原本的校验错误。
    """
    repairs: List[ArgRepair] = []
    coerced = False
    out = arguments
    for fname, finfo in model.model_fields.items():
        if fname not in arguments or not _is_list_annotation(finfo.annotation):
            continue
        val = arguments[fname]
        if not isinstance(val, str) or not val.strip():
            continue
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list):
            if not coerced:
                out = dict(arguments)
                coerced = True
            out[fname] = parsed
            repairs.append(ArgRepair(
                kind="json_string_list", source=fname, target=fname,
                detail=f"decoded JSON string to list ({len(parsed)} items)",
            ))
            logger.debug(
                "normalization: coerced JSON-string list arg %r (%d items)",
                fname, len(parsed),
            )
    return out, tuple(repairs)


def normalize_tool_arguments(
    name: str,
    arguments: dict,
    model: Optional[Type[BaseModel]] = None,
) -> Tuple[dict, Tuple[ArgRepair, ...]]:
    """声明式归一化入口。返回 (归一化后的 args, 修复证据列表)。

    行为与旧 registry._normalize_tool_arguments 逐字一致（含折叠顺序与
    「声明字段永不折叠」不变式），差异只有两点：返回修复证据；geojson
    家族与每工具规则全部来自本模块的声明表。
    """
    if not isinstance(arguments, dict):
        return arguments, ()

    repairs: List[ArgRepair] = []

    # 1. 浅拷贝并归一化 key 风格（kebab-case -> snake_case: radius-px -> radius_px）
    args: Dict[str, Any] = {}
    for k, v in arguments.items():
        if isinstance(k, str) and "-" in k and not k.startswith("-"):
            fixed = k.replace("-", "_")
            args[fixed] = v
            if fixed != k:
                repairs.append(ArgRepair(kind="key_style", source=k, target=fixed))
        else:
            args[k] = v

    model_fields = set(model.model_fields.keys()) if model is not None else set()

    # 2. geojson 全局家族：适用条件 = model 声明了 geojson 字段，或工具在
    #    显式名单内；geojson 已在场则绝不折叠。
    if ("geojson" in model_fields or name in GEOJSON_FAMILY_TOOLS) and "geojson" not in args:
        for alias in GEOJSON_ALIASES:
            if alias in args:
                # 声明字段（含保护性 ref 游标名）一律保留 —— LLM 按 schema
                # 命名的参数永远优先于别名猜测。
                if alias in model_fields:
                    continue
                args["geojson"] = args.pop(alias)
                repairs.append(ArgRepair(
                    kind="field_alias", source=alias, target="geojson",
                    detail="global geojson family",
                ))
                break

    # 3+ 每工具声明规则
    for rule in FIELD_RULES_BY_TOOL.get(name, ()):
        target = rule.target
        if isinstance(rule, ValueCoercionRule):
            if target in args:
                continue
            for alias in rule.aliases:
                if alias in args and alias not in model_fields and rule.accept(args[alias]):
                    args[target] = rule.transform(args.pop(alias))
                    repairs.append(ArgRepair(
                        kind=rule.kind, source=alias, target=target,
                        detail="value coercion",
                    ))
                    break
            continue
        # FieldAliasRule：target 缺席时把首个在场别名折叠过去
        if target in args:
            continue
        if rule.requires_target_declared and target not in model_fields:
            continue
        for alias in rule.aliases:
            if alias in args:
                if alias in model_fields:
                    continue
                val = args.pop(alias)
                if rule.list_wrap and not isinstance(val, list):
                    val = [val]
                args[target] = val
                repairs.append(ArgRepair(
                    kind=rule.kind, source=alias, target=target,
                    detail="list_wrapped" if rule.list_wrap else "",
                ))
                break

    return args, tuple(repairs)


# ---------------------------------------------------------------------------
# 声明表自检（导入即验证的静态不变式 —— 违例直接暴露给启动/测试）
# ---------------------------------------------------------------------------

def validate_normalization_tables() -> List[str]:
    """规则表静态一致性检查；返回致命错误列表（空 = 通过）。

    - 规则目标不得出现在任何别名中（链式折叠是未定义行为）；
    - 同一规则内别名不得重复；
    - 同一别名出现在同工具的多条规则是**合法**的顺序消费语义
      （spatial_aggregate 的 points/polygons 共享 data/geojson 兜底链，
      先到先得）—— 不告警。
    """
    errors: List[str] = []
    for tool, rules in FIELD_RULES_BY_TOOL.items():
        targets: Dict[str, int] = {}
        all_aliases: Dict[str, int] = {}
        for idx, rule in enumerate(rules):
            if rule.target in targets:
                errors.append(f"{tool}: 规则目标 {rule.target} 重复声明")
            targets[rule.target] = idx
            rule_aliases = list(dict.fromkeys(rule.aliases))
            if len(rule_aliases) != len(rule.aliases):
                errors.append(f"{tool}: 规则 {rule.target} 内部别名重复")
            for alias in rule_aliases:
                if alias in targets:
                    errors.append(
                        f"{tool}: 别名 {alias} 同时是规则目标 {targets[alias]}"
                        "（链式折叠未定义）"
                    )
                all_aliases[alias] = idx
        if errors:
            break
    return errors
