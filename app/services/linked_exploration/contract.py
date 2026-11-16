"""G46 探索计划合同：图表/条件身份 → 组合 typed where → MapLibre 表达式。

三条编译链全部闭合（不引用未定义算子）：

1. ``ExplorationCondition``（控件提交的条件）→ typed where AST 叶
   （feature_pages 的闭合算子集）；
2. 条件集合 → 组合 where：**条件（控件）之间一律 AND**；同一分类条件内
   多个 typed 值 OR（in 叶），``include_null`` 单独 OR 一个 ``eq null`` 叶
   （E03：OR 只作用该维度，不把所有维度变 OR）；
3. 组合 where → MapLibre 表达式（mapspec_filter 有界子集，前端 live 地图
   与 SVG/PNG/PDF 导出共享同一求值器）——**typed identity 精确**：
   ``["in", ["get", F], ["literal", values]]`` 保留 1/"1"/true 互不混同
   （MapLibre 严格相等；与 evaluate_where._num_eq 的 bool 纪律一致）。

分类/区间条件的值域：scalar（str/number/bool/null）；区间端点必须是有限
数（数值区间图只绑数值字段）。显示名（label）与机器 identity（typed value）
分开：label 仅供 chips/图例渲染，永不回填求值。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.feature_pages import (
    FILTER_MAX_FIELD_LEN,
    FILTER_MAX_VALUES,
    _validate_ast,
    where_digest,
)
from app.services.linked_exploration.errors import LinkedExplorationError

#: 计划合同版本（视图重开时按此判定合同漂移）
PLAN_SCHEMA_VERSION = 2

#: 计划有界性（任务书 WP11：图数/条件数/桶数硬帽，运行时同样检查）
MAX_CHARTS = 8
MAX_CONDITIONS = 16
MAX_BINS = 32
DEFAULT_BINS = 10
#: 类别图保留桶数（溢出进 other 披露）
MAX_CATEGORY_GROUPS = 64
#: other 桶可筛选的溢出值上限（> FILTER_MAX_VALUES 时禁用 other 筛选并说明）
MAX_OTHER_DISCLOSED = FILTER_MAX_VALUES
#: 图表实例/条件 ID：1-64 字符，控制字符拒绝（组件 id 兼容）
_MAX_ID_LEN = 64

_VALID_KINDS = ("category", "range")
_VALID_AGGS = ("count", "sum", "mean", "min", "max")


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID_LEN:
        raise LinkedExplorationError(
            "INVALID_ID", f"{what} 必须是 1-{_MAX_ID_LEN} 字符字符串",
            status_code=422)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise LinkedExplorationError(
            "INVALID_ID", f"{what} 含控制字符", status_code=422)
    return value


def _check_field(value: str) -> str:
    if (
        not isinstance(value, str) or not value
        or len(value) > FILTER_MAX_FIELD_LEN
    ):
        raise LinkedExplorationError(
            "INVALID_FIELD", f"字段名必须是 1-{FILTER_MAX_FIELD_LEN} 字符字符串",
            status_code=422)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise LinkedExplorationError(
            "INVALID_FIELD", "字段名含控制字符", status_code=422)
    return value


def _check_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise LinkedExplorationError(
        "INVALID_VALUE", "条件值必须是 scalar（str/number/bool/null）",
        status_code=422)


class ChartSpec(BaseModel):
    """一张探索图表（控件）的声明。实例身份 = ``chart_id``。"""

    model_config = ConfigDict(extra="forbid")

    chart_id: str
    #: category（分类条形）| range（数值区间直方图）
    kind: str
    field: str
    #: 数值聚合口径（count 无需 value_field）
    aggregation: str = "count"
    value_field: Optional[str] = None
    title: str = ""
    #: range 图桶数（DEFAULT_BINS..MAX_BINS）
    bins: int = DEFAULT_BINS
    #: 绑定图层（MapSpec layer id，仅用于地图投影披露；不强制存在）
    layer_id: Optional[str] = None

    @field_validator("chart_id")
    @classmethod
    def _v_chart_id(cls, v: str) -> str:
        return _check_id(v, "chart_id")

    @field_validator("kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        if v not in _VALID_KINDS:
            raise ValueError(f"kind 必须是 {'/'.join(_VALID_KINDS)}")
        return v

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("aggregation")
    @classmethod
    def _v_agg(cls, v: str) -> str:
        if v not in _VALID_AGGS:
            raise ValueError(f"aggregation 必须是 {'/'.join(_VALID_AGGS)}")
        return v

    @field_validator("value_field")
    @classmethod
    def _v_value_field(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _check_field(v)

    @model_validator(mode="after")
    def _v_semantics(self) -> "ChartSpec":
        if self.kind == "range" and self.aggregation not in ("count",):
            # 区间图桶内聚合仅支持 count（sum/mean 跨桶求值分母口径复杂，
            # 明确不支持而非静默支持错口径）
            raise ValueError("range 图 aggregation 仅支持 count")
        if self.aggregation in ("sum", "mean", "min", "max") and not self.value_field:
            raise ValueError(f"{self.aggregation} 需要 value_field")
        if self.kind == "range" and not (2 <= self.bins <= MAX_BINS):
            raise ValueError(f"bins 必须在 2-{MAX_BINS}")
        return self


class CategoryCondition(BaseModel):
    """分类条件：同控件内多值 OR；``include_null`` 追加 eq-null 叶。

    typed identity：``values`` 保留原始类型（1/"1"/true 互不混同）；
    ``labels`` 仅展示用，不参与求值。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "category"
    condition_id: str
    chart_id: str
    field: str
    values: List[Any] = Field(default_factory=list)
    include_null: bool = False
    labels: Dict[str, str] = Field(default_factory=dict)

    @field_validator("condition_id", "chart_id")
    @classmethod
    def _v_ids(cls, v: str, info: Any) -> str:
        return _check_id(v, info.field_name)

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("values")
    @classmethod
    def _v_values(cls, v: List[Any]) -> List[Any]:
        if len(v) > FILTER_MAX_VALUES:
            raise ValueError(f"values 最多 {FILTER_MAX_VALUES} 个")
        return [_check_scalar(x) for x in v]

    @model_validator(mode="after")
    def _v_nonempty(self) -> "CategoryCondition":
        if not self.values and not self.include_null:
            raise ValueError("分类条件必须至少含一个值或 include_null")
        return self


class RangeCondition(BaseModel):
    """数值区间条件：端点必须有限数；下/上界各最多一端。

    闭开语义与 evaluate_where 的 gte/gt/lte/lt 叶完全一致（E08：live/
    服务端/导出同一闭开规则）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "range"
    condition_id: str
    chart_id: str
    field: str
    gte: Optional[float] = None
    gt: Optional[float] = None
    lte: Optional[float] = None
    lt: Optional[float] = None

    @field_validator("condition_id", "chart_id")
    @classmethod
    def _v_ids(cls, v: str, info: Any) -> str:
        return _check_id(v, info.field_name)

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("gte", "gt", "lte", "lt")
    @classmethod
    def _v_finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not math.isfinite(v):
            raise ValueError("区间端点必须是有限数（NaN/Infinity 拒绝）")
        return v

    @model_validator(mode="after")
    def _v_bounds(self) -> "RangeCondition":
        lowers = [b for b in (self.gte, self.gt) if b is not None]
        uppers = [b for b in (self.lte, self.lt) if b is not None]
        if len(lowers) > 1 or len(uppers) > 1:
            raise ValueError("下界/上界各最多一端")
        if not lowers and not uppers:
            raise ValueError("区间条件必须至少含一端")
        lo = lowers[0] if lowers else None
        hi = uppers[0] if uppers else None
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("区间下界不得大于上界")
        return self


ExplorationCondition = CategoryCondition | RangeCondition


def condition_to_where(cond: BaseModel) -> Dict[str, Any]:
    """单条件 → typed where AST（闭合算子；经 ``_validate_ast`` 复核）。

    分类：``or(in(values), eq null)``（include_null 时）；区间：``and(端点叶)``。
    """
    if isinstance(cond, CategoryCondition):
        leaves: List[Dict[str, Any]] = []
        if cond.values:
            leaves.append({"op": "in", "field": cond.field, "values": cond.values})
        if cond.include_null:
            leaves.append({"op": "eq", "field": cond.field, "value": None})
        if len(leaves) == 1:
            node = leaves[0]
        else:
            node = {"op": "or", "terms": leaves}
    elif isinstance(cond, RangeCondition):
        terms = []
        if cond.gte is not None:
            terms.append({"op": "gte", "field": cond.field, "value": cond.gte})
        if cond.gt is not None:
            terms.append({"op": "gt", "field": cond.field, "value": cond.gt})
        if cond.lte is not None:
            terms.append({"op": "lte", "field": cond.field, "value": cond.lte})
        if cond.lt is not None:
            terms.append({"op": "lt", "field": cond.field, "value": cond.lt})
        node = terms[0] if len(terms) == 1 else {"op": "and", "terms": terms}
    else:
        raise LinkedExplorationError(
            "INVALID_CONDITION", f"未知条件类型 {type(cond).__name__}", status_code=422)
    try:
        return _validate_ast(node)
    except ValueError as e:
        raise LinkedExplorationError(
            "INVALID_CONDITION", f"条件编译失败：{e}", status_code=422) from e


def compile_combined_where(
    conditions: Sequence[BaseModel],
    *,
    exclude_chart_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """条件集合 → 组合 where。**条件之间一律 AND**（同控件内 OR 已在叶层）。

    ``exclude_chart_id``：facet 口径 —— 排除该控件自身的条件，其余保留
    （WP05：facet 只排除自身，不排除别的控件）。
    返回 None 表示"无条件"（不产生过滤，不是永假）。
    """
    terms: List[Dict[str, Any]] = []
    for cond in conditions:
        if exclude_chart_id is not None and getattr(cond, "chart_id", "") == exclude_chart_id:
            continue
        terms.append(condition_to_where(cond))
    if not terms:
        return None
    if len(terms) == 1:
        node = terms[0]
    else:
        node = {"op": "and", "terms": terms}
    try:
        return _validate_ast(node)
    except ValueError as e:
        # 单条件已各自校验；组合超帽（terms>16）时给出有界失败
        raise LinkedExplorationError(
            "CONDITIONS_OVER_LIMIT",
            f"组合条件编译失败：{e}（最多 {MAX_CONDITIONS} 个条件）",
            status_code=422) from e


# ─── MapLibre 表达式编译（typed identity 精确；闭合子集） ─────────────────

def _ml_scalar(v: Any) -> Any:
    """MapLibre literal 值。float 整数值归一为 int（与 MapLibre JSON 编码一致）。"""
    if isinstance(v, float) and v.is_integer() and abs(v) < 2**53:
        return int(v)
    return v


def where_to_maplibre(where: Optional[Dict[str, Any]]) -> Optional[List[Any]]:
    """typed where AST → MapLibre filter 表达式（mapspec_filter 支持的子集）。

    语义对齐表（与 evaluate_where 逐算子核对）：
    - ``in``：``["in",["get",F],["literal",values]]`` —— MapLibre 严格相等，
      bool 与数字互不等、1 与 "1" 互不等（= _num_eq 纪律）；
    - ``eq null``：``["==",["get",F],null]`` —— MapLibre 中缺失与显式 null
      都取 null，null==null 命中（= evaluate_where eq-null 命中缺席或 null；
      mapspec_filter 的 null 相等语义由本任务修正为一致）；
    - ``between`` 数值：``["all",[">="..],[ "<="..]]`` —— 非数值/bool 不命中；
    - contains/startswith（字符串子串）不在本域条件语言内：遇到即 typed
      拒绝（不静默降级，也不假装 MapLibre 能精确表达大小写不敏感子串）。
    """
    if where is None:
        return None
    op = where["op"]
    if op == "and":
        parts = [where_to_maplibre(t) for t in where["terms"]]
        return ["all", *[p for p in parts if p is not None]] or None
    if op == "or":
        parts = [where_to_maplibre(t) for t in where["terms"]]
        if any(p is None for p in parts):
            # or 内出现无条件真值叶不可能（叶必有算子）——防御
            return None
        return ["any", *parts]
    if op == "not":
        inner = where_to_maplibre(where["term"])
        return ["!", inner] if inner is not None else None
    field = where["field"]
    get = ["get", field]
    if op == "in":
        return ["in", get, ["literal", [_ml_scalar(v) for v in where["values"]]]]
    if op == "eq":
        return ["==", get, _ml_scalar(where["value"])]
    if op == "ne":
        return ["!=", get, _ml_scalar(where["value"])]
    if op == "between":
        return [
            "all",
            [">=", get, _ml_scalar(where["gte"])],
            ["<=", get, _ml_scalar(where["lte"])],
        ]
    if op in ("gte", "gt", "lte", "lt"):
        sym = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}[op]
        return [sym, get, _ml_scalar(where["value"])]
    # contains / startswith：MapLibre 子串语义与大小写规则无法与
    # evaluate_where 精确对齐 —— 本域条件语言不产生这些叶。
    raise LinkedExplorationError(
        "FILTER_NOT_COMPILEABLE",
        f"算子 {op!r} 无法精确编译为地图过滤表达式（本域条件语言不使用它）",
        status_code=422)


class SourceAnchorV2(BaseModel):
    """G46 源锚：ref + 强 revision + 辅助指纹（重开时按此核对）。

    ``extra="ignore"``：inspect/freeze 返回的 anchor()（G41 形状）携带
    feature_count/algorithm_version 等辅助字段 —— 权威只取三元组，辅助
    字段忽略（不因上游加披露字段而破坏已保存视图）。
    """

    model_config = ConfigDict(extra="ignore")

    ref_id: str = Field(min_length=1, max_length=128)
    content_revision: int = Field(ge=1)
    content_fingerprint: Optional[str] = Field(default=None, max_length=128)


class ExplorationPlan(BaseModel):
    """版本化探索计划：源锚 + 图表集 + 条件集（持久视图的内容）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = PLAN_SCHEMA_VERSION
    anchor: SourceAnchorV2
    charts: List[ChartSpec] = Field(default_factory=list)
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    #: 基准 scope 披露（保存时冻结的既有图层过滤/时间范围摘要；不覆盖）
    base_scope_note: str = Field(default="", max_length=500)

    @field_validator("charts")
    @classmethod
    def _v_charts(cls, v: List[ChartSpec]) -> List[ChartSpec]:
        if len(v) > MAX_CHARTS:
            raise ValueError(f"图表实例最多 {MAX_CHARTS} 个")
        ids = [c.chart_id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("chart_id 重复")
        return v

    @field_validator("conditions")
    @classmethod
    def _v_conditions(cls, v: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(v) > MAX_CONDITIONS:
            raise ValueError(f"条件最多 {MAX_CONDITIONS} 个")
        ids = [str(c.get("condition_id", "")) for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("condition_id 重复")
        return v


def parse_condition(raw: Dict[str, Any]) -> ExplorationCondition:
    """raw dict → 具体 Condition 模型（typed 校验失败 → 域错误）。"""
    kind = raw.get("kind")
    try:
        if kind == "category":
            return CategoryCondition.model_validate(raw)
        if kind == "range":
            return RangeCondition.model_validate(raw)
    except Exception as e:  # pydantic ValidationError
        raise LinkedExplorationError(
            "INVALID_CONDITION", f"条件校验失败：{e}", status_code=422) from e
    raise LinkedExplorationError(
        "INVALID_CONDITION", f"未知条件 kind：{kind!r}", status_code=422)


def parse_conditions(raw_list: Sequence[Dict[str, Any]]) -> List[ExplorationCondition]:
    out: List[ExplorationCondition] = []
    for i, raw in enumerate(raw_list):
        try:
            out.append(parse_condition(raw))
        except LinkedExplorationError as e:
            e.index = i
            raise
    return out


def plan_digest(anchor: SourceAnchorV2, charts: Sequence[ChartSpec],
                conditions: Sequence[BaseModel]) -> str:
    """计划规范化 digest（保存/预览/应用幂等锚点）。"""
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "anchor": anchor.model_dump(),
        "charts": [c.model_dump() for c in charts],
        "conditions": [c.model_dump() for c in conditions],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return "g46-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def conditions_digest(conditions: Sequence[BaseModel]) -> str:
    """仅条件集的 digest（前端 generation 拦截旧响应用）。"""
    combined = compile_combined_where(conditions)
    if combined is None:
        return "g46-empty"
    return "g46c-" + where_digest(combined)


def chart_kind_label(kind: str) -> str:
    return {"category": "分类条形图", "range": "数值区间图"}.get(kind, kind)


__all__ = [
    "PLAN_SCHEMA_VERSION", "MAX_CHARTS", "MAX_CONDITIONS", "MAX_BINS",
    "DEFAULT_BINS", "MAX_CATEGORY_GROUPS", "MAX_OTHER_DISCLOSED",
    "ChartSpec", "CategoryCondition", "RangeCondition", "ExplorationCondition",
    "ExplorationPlan", "SourceAnchorV2",
    "condition_to_where", "compile_combined_where", "where_to_maplibre",
    "parse_condition", "parse_conditions", "plan_digest", "conditions_digest",
    "chart_kind_label",
]
