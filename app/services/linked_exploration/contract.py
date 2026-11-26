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
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.feature_pages import (
    FILTER_MAX_FIELD_LEN,
    FILTER_MAX_VALUES,
    _validate_ast,
    where_digest,
)
from app.services.linked_exploration.errors import LinkedExplorationError

#: 计划合同版本（视图重开时按此判定合同漂移）。
#: v2 = 单源（anchor 单数）；v3 = 多源（sources + links；anchor 兼容保留）。
PLAN_SCHEMA_VERSION = 3
#: 仍可读取/保存的历史版本（读取兼容：v2 计划零迁移重放）
_PLAN_SCHEMA_VERSIONS = (2, 3)

#: 计划有界性（任务书 WP11：图数/条件数/桶数硬帽，运行时同样检查）
MAX_CHARTS = 8
MAX_CONDITIONS = 16
MAX_BINS = 32
DEFAULT_BINS = 10
#: 类别图保留桶数（溢出进 other 披露）
MAX_CATEGORY_GROUPS = 64
#: other 桶可筛选的溢出值上限（> FILTER_MAX_VALUES 时禁用 other 筛选并说明）
MAX_OTHER_DISCLOSED = FILTER_MAX_VALUES
#: 多源有界性：本域聚焦少源联动（primary + 少量 context），链路爆炸不是
#: 本域目标（大规模联邦走 data_fabric federation）
MAX_SOURCES = 4
MAX_LINKS = 6
#: 每计划指标数上限（联动范围下的口径数；超过应拆视图）
MAX_METRICS = 8
#: 传播键集上限（下游 in 约束可编译/可披露的有界规模；超限 typed 拒绝并
#: 给出收窄指引，绝不静默截断——截断会让下游范围无声变小）
MAX_PROPAGATED_KEYS = 256
#: 图表实例/条件 ID：1-64 字符，控制字符拒绝（组件 id 兼容）
_MAX_ID_LEN = 64

_VALID_KINDS = ("category", "range")
_VALID_AGGS = ("count", "sum", "mean", "min", "max")
#: 指标算子词表（WP2 指标解释合同；median/distinct_count 有界帽内精确，
#: 超帽 typed 拒绝并给精确路径指引，不声明近似）
_VALID_METRIC_OPS = (
    "count", "distinct_count", "sum", "mean", "weighted_mean", "rate",
    "median",
)
#: 跨源关系：attribute = G38 JoinPlan 同款键连接；spatial = 空间谓词
#: （词表与 G38 SpatialOptions.predicate 对齐）
_VALID_LINK_KINDS = ("attribute", "spatial_within")
_VALID_LINK_CARDINALITY = ("one_to_one", "many_to_one", "one_to_many")
#: many_to_many 一律不收：行数膨胀口径不可解释（要求改用聚合桥/收窄关系）
_VALID_LINK_PROPAGATION = ("none", "left_to_right", "right_to_left")
#: 键变换词表（与 G38 contracts.KEY_TRANSFORMS 对齐）
_VALID_KEY_TRANSFORMS = ("trim", "casefold", "zfill")


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
    #: 绑定源（plan.sources 内的 source_id；None = primary 源。计划级校验
    #: 负责解析与存在性 —— 单模型不知道计划里有哪些源）
    source_id: Optional[str] = None

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
    #: 绑定源（None = 所属图表的源；计划级校验强制与 chart 同源 —— 条件
    #: 跨源会造成 facet 口径歧义）
    source_id: Optional[str] = None

    @field_validator("condition_id", "chart_id")
    @classmethod
    def _v_ids(cls, v: str, info: Any) -> str:
        return _check_id(v, info.field_name)

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("source_id")
    @classmethod
    def _v_source_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _check_id(v, "source_id")

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
    #: 绑定源（None = 所属图表的源；计划级校验强制与 chart 同源）
    source_id: Optional[str] = None

    @field_validator("condition_id", "chart_id")
    @classmethod
    def _v_ids(cls, v: str, info: Any) -> str:
        return _check_id(v, info.field_name)

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("source_id")
    @classmethod
    def _v_source_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _check_id(v, "source_id")

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


class DrillHierarchy(BaseModel):
    """钻取层级映射：子粒度行内持有父粒度键的字段（显式声明，不猜）。

    层级只来自用户指定或既有字段关系（任务书 WP1：不从名称建立不可靠
    映射）。``parent_source_id`` 缺省 = 同源（父键在本源另一字段，如
    街道点的"所属区县"字段）；指定其它源 = 父粒度表（按键对齐上卷）。
    """

    model_config = ConfigDict(extra="forbid")

    parent_field: str
    parent_source_id: Optional[str] = None
    #: 粒度披露（如 "街道→区县"）；仅说明用
    level_note: str = Field(default="", max_length=200)

    @field_validator("parent_field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field(v)


class SourceBinding(BaseModel):
    """多源计划中的单源绑定：稳定别名 + 源锚 + 角色 + 粒度标注。

    ``source_id`` 是计划内稳定短别名（图表/条件/关系用它引用源，不用
    长 ref id）。``role``：primary 恰好一个（默认匹配计数/主图源），
    context 其余。``granularity_note`` 仅披露（如 "区县面" / "学校点"）。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    anchor: SourceAnchorV2
    role: str = "context"
    granularity_note: str = Field(default="", max_length=200)
    hierarchy: Optional[DrillHierarchy] = None

    @field_validator("source_id")
    @classmethod
    def _v_source_id(cls, v: str) -> str:
        return _check_id(v, "source_id")

    @field_validator("role")
    @classmethod
    def _v_role(cls, v: str) -> str:
        if v not in ("primary", "context"):
            raise ValueError("role 必须是 primary/context")
        return v


class LinkKeyPair(BaseModel):
    """属性关系的一个键对（left_field ↔ right_field；与 G38 KeyField
    词表对齐：trim/casefold/zfill；zfill 需宽度 1-32）。

    键类型政策与 G38 canonical_key_value 一致：null 不参与、类型分族
    （数值 int==float、字符串严格、bool 独立族）、变换只作用字符串。
    """

    model_config = ConfigDict(extra="forbid")

    left_field: str
    right_field: str
    transform: Optional[str] = None
    zfill_width: int = 0

    @field_validator("left_field", "right_field")
    @classmethod
    def _v_fields(cls, v: str) -> str:
        return _check_field(v)

    @field_validator("transform")
    @classmethod
    def _v_transform(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in _VALID_KEY_TRANSFORMS:
            raise ValueError(
                f"transform 必须是 {'/'.join(_VALID_KEY_TRANSFORMS)} 或省略")
        return v

    @model_validator(mode="after")
    def _v_zfill(self) -> "LinkKeyPair":
        if self.transform == "zfill" and not (1 <= self.zfill_width <= 32):
            raise ValueError("zfill 宽度必须在 1..32")
        if self.transform != "zfill":
            self.zfill_width = 0
        return self


class LinkSpec(BaseModel):
    """跨源关系：本线不发明 join 语言 —— 属性关系声明与 G38 JoinPlan
    同款键对词表；``join_plan_id`` 可选引用已批准/已诊断的 G38 计划
    （仅溯源披露，执行仍按本域有界半连接语义）。

    方向语义：left = ``left_source_id`` 源，right = ``right_source_id`` 源。
    ``propagate`` 声明过滤传播方向（缺省 none = 条件各自独立，只在本源
    生效）；传播沿声明方向传递、传递图必须无环（双向 = 立即成环，词表
    不收 bidirectional —— 双向联动用两条单向关系表达并接受环检测）。
    ``cardinality`` 必须显式声明；``many_to_many`` 不收（行数膨胀不可
    解释，要求收窄关系或走聚合桥）。
    """

    model_config = ConfigDict(extra="forbid")

    link_id: str
    kind: str = "attribute"
    left_source_id: str
    right_source_id: str
    #: attribute 关系的键对（1-8 个复合键，同 G38 keys ≤8）
    keys: List[LinkKeyPair] = Field(default_factory=list)
    #: spatial_within 的空间谓词（词表对齐 G38 SpatialOptions.predicate）
    predicate: Optional[str] = None
    cardinality: str
    propagate: str = "none"
    #: 可选 G38 计划溯源（不做执行依赖；诊断以本域 preview 实测为准）
    join_plan_id: Optional[str] = Field(default=None, max_length=64)
    #: 关系说明（如 "学校点 within 区县面"）；披露用
    note: str = Field(default="", max_length=200)

    @field_validator("link_id", "left_source_id", "right_source_id")
    @classmethod
    def _v_ids(cls, v: str, info: Any) -> str:
        return _check_id(v, info.field_name)

    @field_validator("kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        if v not in _VALID_LINK_KINDS:
            raise ValueError(f"kind 必须是 {'/'.join(_VALID_LINK_KINDS)}")
        return v

    @field_validator("cardinality")
    @classmethod
    def _v_cardinality(cls, v: str) -> str:
        if v not in _VALID_LINK_CARDINALITY:
            raise ValueError(
                "cardinality 必须是 "
                f"{'/'.join(_VALID_LINK_CARDINALITY)}（many_to_many 不收："
                "行数膨胀不可解释，请收窄关系或改用聚合桥）")
        return v

    @field_validator("propagate")
    @classmethod
    def _v_propagate(cls, v: str) -> str:
        if v not in _VALID_LINK_PROPAGATION:
            raise ValueError(
                f"propagate 必须是 {'/'.join(_VALID_LINK_PROPAGATION)}"
                "（双向用两条单向关系表达）")
        return v

    @field_validator("predicate")
    @classmethod
    def _v_predicate(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ("within", "within_or_boundary"):
            raise ValueError("predicate 必须是 within/within_or_boundary")
        return v

    @field_validator("keys")
    @classmethod
    def _v_keys(cls, v: List[LinkKeyPair]) -> List[LinkKeyPair]:
        if len(v) > 8:
            raise ValueError("复合键最多 8 对（与 G38 keys ≤8 对齐）")
        return v

    @model_validator(mode="after")
    def _v_semantics(self) -> "LinkSpec":
        if self.left_source_id == self.right_source_id:
            raise ValueError("关系的两端必须是不同源")
        if self.kind == "attribute":
            if not self.keys:
                raise ValueError("attribute 关系必须至少声明一个键对")
            if self.predicate is not None:
                raise ValueError("attribute 关系不使用 predicate")
        else:  # spatial_within
            if self.predicate is None:
                raise ValueError("spatial_within 关系必须声明 predicate")
            if self.keys:
                raise ValueError("spatial_within 关系不使用键对")
        return self


class MetricSpec(BaseModel):
    """指标解释合同（WP2）：算子 + 分母治理 + 粒度/单位披露。

    语义边界：
    - 指标在**联动范围过滤后的行集**上求值（服务层先按 per-source where
      + 传播键集过滤，再交给指标求值器）—— 指标永远回答"当前范围下"。
    - 同源指标：``source_id`` 行集上直接聚合；``group_field`` 可选分
      组（typed 组键，溢出并入 other 披露）。
    - 跨源 rate（如每万人学校数）：``denominator_source_id`` 指向另一
      源且必须给 ``link_id``；分组 = 关系键本身（每键一组），分子/分母
      各自按键集合聚合 —— 只按键聚合、不物化行乘积（M:N 膨胀不可能）。
    - 分母合计为零/空/全非数值 → 该组 ``unavailable`` + 原因码，绝不
      返回 0 冒充率。
    - ``weighted_mean``：``weight_field``（行内数值权重）或
      ``weight_by_area``（几何面积权重，需显式开启并披露坐标系口径）。
    - ``scale``：有限正数（如 10000 = 每万人）；结果乘以 scale。
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    op: str
    #: 取值源（行集来源；None = primary）
    source_id: Optional[str] = None
    #: value 字段（sum/mean/weighted_mean/distinct_count/median/rate 分子
    #: 聚合需要；rate 分子为 count 时不需要）
    value_field: Optional[str] = None
    weight_field: Optional[str] = None
    weight_by_area: bool = False
    #: rate 分母字段（None = 分母侧行计数）
    denominator_field: Optional[str] = None
    #: rate 分母源（跨源 rate 必须给并同时给 link_id；缺省 = source_id）
    denominator_source_id: Optional[str] = None
    #: 跨源 rate 使用的关系（须引用计划内 link）
    link_id: Optional[str] = None
    #: 同源分组字段（跨源 rate 不适用 —— 键即分组）
    group_field: Optional[str] = None
    #: 输出粒度源（缺省 = source_id；跨源 rate 固定为分母侧）
    grain_source_id: Optional[str] = None
    #: 结果缩放（有限正数；None = 1）
    scale: Optional[float] = None
    #: 单位披露（field_contracts 声明优先；此处是指标级显式声明）
    unit: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=200)

    @field_validator("metric_id")
    @classmethod
    def _v_metric_id(cls, v: str) -> str:
        return _check_id(v, "metric_id")

    @field_validator("op")
    @classmethod
    def _v_op(cls, v: str) -> str:
        if v not in _VALID_METRIC_OPS:
            raise ValueError(f"op 必须是 {'/'.join(_VALID_METRIC_OPS)}")
        return v

    @field_validator(
        "source_id", "weight_field",
        "denominator_field", "denominator_source_id", "link_id",
        "group_field", "grain_source_id",
    )
    @classmethod
    def _v_optional_ids(cls, v: Optional[str], info: Any) -> Optional[str]:
        if v is None:
            return v
        if info.field_name in ("weight_field", "denominator_field",
                               "group_field"):
            return _check_field(v)
        return _check_id(v, info.field_name)

    @field_validator("value_field")
    @classmethod
    def _v_value_field(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _check_field(v)

    @field_validator("scale")
    @classmethod
    def _v_scale(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not math.isfinite(v) or v <= 0:
            raise ValueError("scale 必须是有限正数")
        return v

    @model_validator(mode="after")
    def _v_semantics(self) -> "MetricSpec":
        needs_value = self.op in (
            "sum", "mean", "weighted_mean", "distinct_count", "median")
        if needs_value and not self.value_field:
            raise ValueError(f"{self.op} 需要 value_field")
        # rate 的 value_field 可选：缺省 = 分子侧行计数（如"学校数"）
        if self.op == "weighted_mean" and not self.weight_by_area \
                and not self.weight_field:
            raise ValueError(
                "weighted_mean 需要 weight_field 或显式 weight_by_area=true")
        if self.weight_field and self.weight_by_area:
            raise ValueError("weight_field 与 weight_by_area 二选一")
        if self.op != "weighted_mean":
            if self.weight_field or self.weight_by_area:
                raise ValueError("weight 仅 weighted_mean 使用")
        if self.denominator_source_id is not None or self.link_id is not None:
            if self.op != "rate":
                raise ValueError("跨源分母仅 rate 指标使用")
            if self.denominator_source_id is None or self.link_id is None:
                raise ValueError(
                    "跨源 rate 必须同时给 denominator_source_id 与 link_id")
        if self.group_field is not None and self.link_id is not None:
            raise ValueError(
                "跨源 rate 以关系键分组：不支持额外 group_field")
        return self


class ExplorationPlan(BaseModel):
    """版本化探索计划。

    v2（单源）：``anchor`` 必填、``sources`` 空 —— 旧计划零迁移可读。
    v3（多源）：``sources`` 非空（恰好一个 primary），``anchor`` 可省
    （若给出必须等于 primary 锚）；``links`` 声明跨源关系（DAG）。
    图表/条件以 ``source_id``（缺省 primary）绑定源；条件必须与其图表
    同源（facet 排除口径才无歧义）。``metrics`` 是指标解释合同（求值
    仍在联动范围过滤后的行集上进行）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = PLAN_SCHEMA_VERSION
    anchor: Optional[SourceAnchorV2] = None
    sources: List[SourceBinding] = Field(default_factory=list)
    links: List[LinkSpec] = Field(default_factory=list)
    metrics: List[MetricSpec] = Field(default_factory=list)
    charts: List[ChartSpec] = Field(default_factory=list)
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    #: 基准 scope 披露（保存时冻结的既有图层过滤/时间范围摘要；不覆盖）
    base_scope_note: str = Field(default="", max_length=500)

    @field_validator("schema_version")
    @classmethod
    def _v_schema_version(cls, v: int) -> int:
        if v not in _PLAN_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema_version 仅支持 {'/'.join(str(x) for x in _PLAN_SCHEMA_VERSIONS)}")
        return v

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

    @field_validator("links")
    @classmethod
    def _v_links(cls, v: List[LinkSpec]) -> List[LinkSpec]:
        if len(v) > MAX_LINKS:
            raise ValueError(f"关系最多 {MAX_LINKS} 条")
        ids = [lk.link_id for lk in v]
        if len(set(ids)) != len(ids):
            raise ValueError("link_id 重复")
        return v

    @field_validator("metrics")
    @classmethod
    def _v_metrics(cls, v: List[MetricSpec]) -> List[MetricSpec]:
        if len(v) > MAX_METRICS:
            raise ValueError(f"指标最多 {MAX_METRICS} 个")
        ids = [m.metric_id for m in v]
        if len(set(ids)) != len(ids):
            raise ValueError("metric_id 重复")
        return v

    @model_validator(mode="after")
    def _v_multi_source(self) -> "ExplorationPlan":
        source_ids = [s.source_id for s in self.sources]
        if self.sources:
            if len(source_ids) > MAX_SOURCES:
                raise ValueError(f"源最多 {MAX_SOURCES} 个")
            if len(set(source_ids)) != len(source_ids):
                raise ValueError("source_id 重复")
            primaries = [s for s in self.sources if s.role == "primary"]
            if len(primaries) != 1:
                raise ValueError("必须恰好一个 primary 源")
            primary = primaries[0]
            if self.schema_version == 2:
                raise ValueError(
                    "schema_version=2 是单源合同：多源请用 schema_version=3")
            if self.anchor is not None and self.anchor != primary.anchor:
                raise ValueError(
                    "anchor 与 primary 源锚不一致（省略 anchor 或给 primary 锚）")
            known = set(source_ids)
            for link in self.links:
                if link.left_source_id not in known:
                    raise ValueError(
                        f"关系 {link.link_id} 的 left_source_id "
                        f"{link.left_source_id!r} 不在 sources 中")
                if link.right_source_id not in known:
                    raise ValueError(
                        f"关系 {link.link_id} 的 right_source_id "
                        f"{link.right_source_id!r} 不在 sources 中")
            for binding in self.sources:
                h = binding.hierarchy
                if h is not None and h.parent_source_id is not None \
                        and h.parent_source_id not in known:
                    raise ValueError(
                        f"源 {binding.source_id} 层级 parent_source_id "
                        f"{h.parent_source_id!r} 不在 sources 中")
            link_ids = {lk.link_id for lk in self.links}
            primary_id = primary.source_id
            for metric in self.metrics:
                msid = metric.source_id or primary_id
                if msid not in known:
                    raise ValueError(
                        f"指标 {metric.metric_id} 的 source_id "
                        f"{metric.source_id!r} 不在 sources 中")
                grain = metric.grain_source_id or msid
                if grain not in known:
                    raise ValueError(
                        f"指标 {metric.metric_id} 的 grain_source_id "
                        f"{metric.grain_source_id!r} 不在 sources 中")
                if metric.link_id is not None and metric.link_id not in link_ids:
                    raise ValueError(
                        f"指标 {metric.metric_id} 的 link_id "
                        f"{metric.link_id!r} 不在 links 中")
                if metric.denominator_source_id is not None and \
                        metric.denominator_source_id not in known:
                    raise ValueError(
                        f"指标 {metric.metric_id} 的 denominator_source_id "
                        f"{metric.denominator_source_id!r} 不在 sources 中")
            self._check_propagation_dag()
        else:
            if self.anchor is None:
                raise ValueError(
                    "计划必须有源：v2 给 anchor，v3 给 sources")
            if self.schema_version == 3:
                raise ValueError(
                    "schema_version=3 必须声明 sources（单源也用 sources 表达）")
        # 图表/条件的源引用（v2：不允许 source_id —— 单源合同没有命名绑定）
        chart_source = {c.chart_id: c.source_id for c in self.charts}
        for chart in self.charts:
            if self.sources:
                if chart.source_id is not None and chart.source_id not in source_ids:
                    raise ValueError(
                        f"图表 {chart.chart_id} 的 source_id "
                        f"{chart.source_id!r} 不在 sources 中")
            elif chart.source_id is not None:
                raise ValueError(
                    "v2 计划不支持图表 source_id（单源合同没有命名绑定）")
        for raw in self.conditions:
            sid = raw.get("source_id") if isinstance(raw, dict) else None
            if sid is not None and not isinstance(sid, str):
                raise ValueError("条件 source_id 必须是字符串")
            if self.sources:
                if sid is not None and sid not in source_ids:
                    raise ValueError(
                        f"条件 {raw.get('condition_id')!r} 的 source_id "
                        f"{sid!r} 不在 sources 中")
                # 条件必须与所属图表同源（facet 排除口径无歧义）
                cid = raw.get("chart_id")
                csid = chart_source.get(str(cid)) if cid is not None else None
                eff = sid  # None → 继承图表源（primary 由编译期解析）
                if csid is not None and eff is not None and eff != csid:
                    raise ValueError(
                        f"条件 {raw.get('condition_id')!r} 的 source_id "
                        f"{eff!r} 与其图表 {cid!r} 的源 {csid!r} 不一致")
            elif sid is not None:
                raise ValueError(
                    "v2 计划不支持条件 source_id（单源合同没有命名绑定）")
        return self

    def _check_propagation_dag(self) -> None:
        """传播方向图必须无环（任务书 WP1：禁止循环传播）。"""
        edges: Dict[str, List[str]] = {s.source_id: [] for s in self.sources}
        for link in self.links:
            if link.propagate == "left_to_right":
                edges[link.left_source_id].append(link.right_source_id)
            elif link.propagate == "right_to_left":
                edges[link.right_source_id].append(link.left_source_id)
        # DFS 三色标记
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {k: WHITE for k in edges}

        def _visit(node: str) -> None:
            color[node] = GRAY
            for nxt in edges.get(node, ()):
                if color[nxt] == GRAY:
                    raise ValueError(
                        f"传播关系成环（{node} → {nxt}）：禁止循环传播")
                if color[nxt] == WHITE:
                    _visit(nxt)
            color[node] = BLACK

        for node in edges:
            if color[node] == WHITE:
                _visit(node)


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


# ─── 多源计划助手（v3；v2 以派生 primary 绑定表达，访问面统一） ──────────

def plan_source_bindings(plan: ExplorationPlan) -> List[SourceBinding]:
    """计划的源绑定列表（规范化访问面）。

    v2 计划派生 ``SourceBinding(source_id="primary", role="primary",
    anchor=plan.anchor)``；v3 返回声明顺序（primary 在前不保证 —— 角色
    由 role 字段权威）。
    """
    if plan.sources:
        return list(plan.sources)
    if plan.anchor is None:  # 已被计划校验排除；防御
        raise LinkedExplorationError(
            "INVALID_PLAN", "计划没有源锚", status_code=422)
    return [SourceBinding(source_id="primary", anchor=plan.anchor, role="primary")]


def plan_primary_binding(plan: ExplorationPlan) -> SourceBinding:
    """primary 源绑定（匹配计数/默认图表源）。"""
    for binding in plan_source_bindings(plan):
        if binding.role == "primary":
            return binding
    raise LinkedExplorationError(
        "INVALID_PLAN", "计划缺少 primary 源", status_code=422)


def resolve_chart_source_id(plan: ExplorationPlan, chart: ChartSpec) -> str:
    """图表生效源 id（缺省 primary）。"""
    if chart.source_id is None:
        return plan_primary_binding(plan).source_id
    return chart.source_id


def resolve_condition_source_id(
    plan: ExplorationPlan,
    condition: BaseModel,
    chart_source_ids: Dict[str, str],
) -> str:
    """条件生效源 id：显式 source_id 优先，否则继承所属图表的源，
    再否则 primary。计划级校验已保证显式值与图表源一致。"""
    sid = getattr(condition, "source_id", None)
    if sid is not None:
        return sid
    chart_id = getattr(condition, "chart_id", "")
    mapped = chart_source_ids.get(chart_id)
    if mapped is not None:
        return mapped
    return plan_primary_binding(plan).source_id


def compile_plan_wheres(
    plan: ExplorationPlan,
    conditions: Sequence[BaseModel],
    *,
    exclude_chart_id: Optional[str] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """按源分组编译组合 where（组内沿用 AND-跨控件/OR-同控件规则）。

    返回 ``{source_id: where|None}``，每个源一键（无条件 = None，不是
    永假）。``exclude_chart_id`` 是 facet 口径：跨全部源排除该 chart 的
    条件（图表的条件与图表同源，计划校验已保证）。
    """
    bindings = plan_source_bindings(plan)
    groups: Dict[str, List[BaseModel]] = {b.source_id: [] for b in bindings}
    chart_source_ids = {
        c.chart_id: resolve_chart_source_id(plan, c) for c in plan.charts
    }
    for cond in conditions:
        if exclude_chart_id is not None and \
                getattr(cond, "chart_id", "") == exclude_chart_id:
            continue
        sid = resolve_condition_source_id(plan, cond, chart_source_ids)
        if sid not in groups:
            raise LinkedExplorationError(
                "INVALID_PLAN",
                f"条件解析到未知源 {sid!r}", status_code=422)
        groups[sid].append(cond)
    return {
        sid: (compile_combined_where(conds) if conds else None)
        for sid, conds in groups.items()
    }


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def plan_digest_of_plan(plan: ExplorationPlan) -> str:
    """计划规范化 digest（保存/预览/应用幂等锚点）——计划级入口。

    v2 计划与旧 ``plan_digest`` 公式逐字节一致（兼容历史 normalized_digest
    语义）；v3 加入 sources/links 规范化形状。
    """
    conditions = parse_conditions(plan.conditions)
    if not plan.sources:
        return plan_digest(plan.anchor, plan.charts, conditions)
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "sources": [s.model_dump() for s in plan.sources],
        "links": [lk.model_dump() for lk in plan.links],
        "metrics": [m.model_dump() for m in plan.metrics],
        "charts": [c.model_dump() for c in plan.charts],
        "conditions": [c.model_dump() for c in conditions],
    }
    return "g46-" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def plan_conditions_digest(
    plan: ExplorationPlan,
    conditions: Sequence[BaseModel],
) -> str:
    """多源条件状态 digest（前端 generation 拦截旧响应；含 per-source
    where 与关系传播声明 —— 传播声明改变下游口径，必须参与 digest）。"""
    wheres = compile_plan_wheres(plan, conditions)
    payload = {
        "wheres": {k: v for k, v in sorted(wheres.items())},
        "links": [lk.model_dump() for lk in plan.links],
    }
    return "g46m-" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def chart_kind_label(kind: str) -> str:
    return {"category": "分类条形图", "range": "数值区间图"}.get(kind, kind)


__all__ = [
    "PLAN_SCHEMA_VERSION", "MAX_CHARTS", "MAX_CONDITIONS", "MAX_BINS",
    "DEFAULT_BINS", "MAX_CATEGORY_GROUPS", "MAX_OTHER_DISCLOSED",
    "MAX_SOURCES", "MAX_LINKS", "MAX_PROPAGATED_KEYS", "MAX_METRICS",
    "ChartSpec", "CategoryCondition", "RangeCondition", "ExplorationCondition",
    "ExplorationPlan", "SourceAnchorV2", "SourceBinding", "DrillHierarchy",
    "LinkKeyPair", "LinkSpec", "MetricSpec",
    "condition_to_where", "compile_combined_where", "where_to_maplibre",
    "parse_condition", "parse_conditions", "plan_digest", "conditions_digest",
    "chart_kind_label",
    "plan_source_bindings", "plan_primary_binding", "resolve_chart_source_id",
    "resolve_condition_source_id", "compile_plan_wheres",
    "plan_digest_of_plan", "plan_conditions_digest",
]
