"""Typed predicate AST for Data Fabric V2 (ADR-0094).

LLM/UI 永不直接生成 SQL。所有过滤谓词都以该 AST 表达，由 adapter-specific
compiler（``compilers.py``）编译为参数化查询：PostGIS SQL、CQL2-text、
ArcGIS where、FES XML、本地求值器。字段名必须通过 ``^[A-Za-z_][A-Za-z0-9_]*$``
且（编译时）来自 DatasetDescriptor schema 白名单；值一律参数化或转义。

AST 节点是 frozen pydantic 模型，支持 dict 往返（``predicate_to_dict`` /
``predicate_from_dict``）、确定性 canonical 序列化（AND/OR 子节点排序，
使 fingerprint 与书写顺序无关）与本地求值（``evaluate_predicate``）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 允许的标量值类型（bool 必须先于 int，避免 smart-coercion 把布尔吞成整数）。
ScalarValue = Union[bool, int, float, str, None]


class PredicateError(ValueError):
    """无效谓词结构 / 字段名 / 值。"""


def _check_field_name(v: str) -> str:
    if not isinstance(v, str) or not FIELD_NAME_RE.match(v):
        raise PredicateError(f"invalid field name: {v!r}")
    return v


def _check_bbox(v: Sequence[float]) -> List[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise PredicateError("bbox must be [minx, miny, maxx, maxy]")
    minx, miny, maxx, maxy = v
    for c in (minx, miny, maxx, maxy):
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            raise PredicateError("bbox coordinates must be numeric")
    if miny > maxy:
        raise PredicateError(f"bbox miny({miny}) > maxy({maxy})")
    if minx > maxx:
        # 跨反子午线：合法 bbox，由 planner/编译器显式 split（禁止静默错误）。
        if not (-180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0):
            raise PredicateError(f"bbox minx({minx}) > maxx({maxx}) outside antimeridian range")
    return [float(minx), float(miny), float(maxx), float(maxy)]


def bbox_crosses_antimeridian(bbox: Sequence[float]) -> bool:
    """4326 bbox 是否跨反子午线（minx > maxx）。"""
    return len(bbox) == 4 and bbox[0] > bbox[2]


def split_antimeridian_bbox(bbox: Sequence[float]) -> List[List[float]]:
    """把跨反子午线 bbox 拆分为两个西侧/东侧框（供 OR 编译）。"""
    minx, miny, maxx, maxy = bbox
    return [
        [minx, miny, 180.0, maxy],
        [-180.0, miny, maxx, maxy],
    ]


# ── 属性谓词 ────────────────────────────────────────────────────────────────


class _PredicateBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _AttrPredicate(_PredicateBase):
    field: str

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field_name(v)


class Eq(_AttrPredicate):
    op: Literal["eq"] = "eq"
    value: ScalarValue = None


class Ne(_AttrPredicate):
    op: Literal["ne"] = "ne"
    value: ScalarValue = None


class Gt(_AttrPredicate):
    op: Literal["gt"] = "gt"
    value: ScalarValue = None


class Ge(_AttrPredicate):
    op: Literal["ge"] = "ge"
    value: ScalarValue = None


class Lt(_AttrPredicate):
    op: Literal["lt"] = "lt"
    value: ScalarValue = None


class Le(_AttrPredicate):
    op: Literal["le"] = "le"
    value: ScalarValue = None


class In(_AttrPredicate):
    op: Literal["in"] = "in"
    values: List[ScalarValue] = Field(min_length=1, max_length=1000)


class NotIn(_AttrPredicate):
    op: Literal["not_in"] = "not_in"
    values: List[ScalarValue] = Field(min_length=1, max_length=1000)


class Between(_AttrPredicate):
    op: Literal["between"] = "between"
    low: Union[int, float, str]
    high: Union[int, float, str]


class Like(_AttrPredicate):
    op: Literal["like"] = "like"
    pattern: str = Field(min_length=1, max_length=256)


class IsNull(_AttrPredicate):
    op: Literal["is_null"] = "is_null"
    negated: bool = False  # True → IS NOT NULL


class _LogicPredicate(_PredicateBase):
    args: List["Predicate"]


class And(_LogicPredicate):
    op: Literal["and"] = "and"

    @field_validator("args")
    @classmethod
    def _v_args(cls, v: List["Predicate"]) -> List["Predicate"]:
        if not v:
            raise PredicateError("and() requires at least one argument")
        return v


class Or(_LogicPredicate):
    op: Literal["or"] = "or"

    @field_validator("args")
    @classmethod
    def _v_args(cls, v: List["Predicate"]) -> List["Predicate"]:
        if not v:
            raise PredicateError("or() requires at least one argument")
        return v


class Not(_PredicateBase):
    op: Literal["not"] = "not"
    arg: "Predicate"


Predicate = Annotated[
    Union[
        Eq, Ne, Gt, Ge, Lt, Le, In, NotIn, Between, Like, IsNull,
        And, Or, Not,
    ],
    Field(discriminator="op"),
]

# 前向引用解析（pydantic v2 需要在类型别名定义后 rebuild）。
And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()

_PREDICATE_ADAPTER: TypeAdapter = TypeAdapter(Predicate)


# ── 空间谓词 ────────────────────────────────────────────────────────────────


class _SpatialBase(_PredicateBase):
    """几何列缺省取数据集主几何列（DatasetDescriptor 决定）。"""

    field: Optional[str] = None

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _check_field_name(v)
        return v


class BBox(_SpatialBase):
    """axis-order 固定 [minx, miny, maxx, maxy]（lon/lat，当 crs=4326 时）。"""

    op: Literal["bbox"] = "bbox"
    bbox: List[float]
    crs: str = "EPSG:4326"

    @field_validator("bbox")
    @classmethod
    def _v_bbox(cls, v: List[float]) -> List[float]:
        return _check_bbox(v)


class Intersects(_SpatialBase):
    op: Literal["intersects"] = "intersects"
    geometry: Dict[str, Any]  # GeoJSON geometry
    crs: str = "EPSG:4326"


class Within(_SpatialBase):
    op: Literal["within"] = "within"
    geometry: Dict[str, Any]
    crs: str = "EPSG:4326"


class Contains(_SpatialBase):
    op: Literal["contains"] = "contains"
    geometry: Dict[str, Any]
    crs: str = "EPSG:4326"


class Touches(_SpatialBase):
    op: Literal["touches"] = "touches"
    geometry: Dict[str, Any]
    crs: str = "EPSG:4326"


class Overlaps(_SpatialBase):
    op: Literal["overlaps"] = "overlaps"
    geometry: Dict[str, Any]
    crs: str = "EPSG:4326"


class DWithin(_SpatialBase):
    """距离谓词。``units`` 固定 meters（geodesic 语义由编译器实现：
    PostGIS 走 ``geography`` cast；本地求值走 haversine）。"""

    op: Literal["dwithin"] = "dwithin"
    geometry: Dict[str, Any]  # 通常为 Point
    crs: str = "EPSG:4326"
    distance: float = Field(gt=0, le=2_000_000)
    units: Literal["meters"] = "meters"


SpatialPredicate = Annotated[
    Union[BBox, Intersects, Within, Contains, Touches, Overlaps, DWithin],
    Field(discriminator="op"),
]
_SPATIAL_ADAPTER: TypeAdapter = TypeAdapter(SpatialPredicate)


# ── 时间谓词 ────────────────────────────────────────────────────────────────

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def _check_iso(v: str) -> str:
    if not isinstance(v, str) or not _ISO_RE.match(v):
        raise PredicateError(f"invalid ISO-8601 datetime: {v!r}")
    return v


class _TemporalBase(_PredicateBase):
    field: str

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field_name(v)


class Before(_TemporalBase):
    op: Literal["before"] = "before"
    value: str

    @field_validator("value")
    @classmethod
    def _v_value(cls, v: str) -> str:
        return _check_iso(v)


class After(_TemporalBase):
    op: Literal["after"] = "after"
    value: str

    @field_validator("value")
    @classmethod
    def _v_value(cls, v: str) -> str:
        return _check_iso(v)


class During(_TemporalBase):
    op: Literal["during"] = "during"
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _v_bounds(cls, v: str) -> str:
        return _check_iso(v)


TemporalPredicate = Annotated[Union[Before, After, During], Field(discriminator="op")]
_TEMPORAL_ADAPTER: TypeAdapter = TypeAdapter(TemporalPredicate)


# ── 公共 API ────────────────────────────────────────────────────────────────


def predicate_from_dict(data: Any) -> Predicate:
    """dict → Predicate AST（严格校验；失败抛 ``PredicateError``）。"""
    try:
        return _PREDICATE_ADAPTER.validate_python(data)
    except PredicateError:
        raise
    except Exception as e:  # pydantic ValidationError 等
        raise PredicateError(f"invalid predicate: {e}") from e


def spatial_from_dict(data: Any) -> SpatialPredicate:
    try:
        return _SPATIAL_ADAPTER.validate_python(data)
    except PredicateError:
        raise
    except Exception as e:
        raise PredicateError(f"invalid spatial predicate: {e}") from e


def temporal_from_dict(data: Any) -> TemporalPredicate:
    try:
        return _TEMPORAL_ADAPTER.validate_python(data)
    except PredicateError:
        raise
    except Exception as e:
        raise PredicateError(f"invalid temporal predicate: {e}") from e


def _canonical_value(v: Any) -> Any:
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        # 1 与 1.0 canonical 化为同一形式，保证 fingerprint 稳定。
        return int(v)
    return v


def _canonical_node(node: Any) -> Any:
    if isinstance(node, (And, Or)):
        children = sorted(
            (json.dumps(_canonical_node(a), sort_keys=True, ensure_ascii=False) for a in node.args)
        )
        # 排序后反序列化回结构（通过 dict 往返保持类型）。
        parsed = [json.loads(c) for c in children]
        return {"op": node.op, "args": parsed}
    d = node.model_dump()
    for key, val in list(d.items()):
        if isinstance(val, list) and val and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val):
            d[key] = [_canonical_value(x) for x in val]
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            d[key] = _canonical_value(val)
    # 定长 dict key 顺序由 sort_keys 处理（json.dumps 阶段）。
    return d


def predicate_to_canonical_dict(node: Any) -> Dict[str, Any]:
    """canonical dict（AND/OR 子节点排序、数值归一）→ 用于 fingerprint。"""
    return _canonical_node(node)


def canonical_payload(node: Any) -> str:
    return json.dumps(predicate_to_canonical_dict(node), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def predicate_fingerprint(node: Any) -> str:
    return hashlib.sha256(canonical_payload(node).encode("utf-8")).hexdigest()[:16]


def iter_fields(node: Any) -> List[str]:
    """收集谓词树引用的全部字段名（去重、按出现序）。"""
    seen: List[str] = []
    def _walk(n: Any) -> None:
        f = getattr(n, "field", None)
        if isinstance(f, str) and f not in seen:
            seen.append(f)
        for attr in ("args", "arg"):
            children = getattr(n, attr, None)
            if isinstance(children, list):
                for c in children:
                    _walk(c)
            elif children is not None:
                _walk(children)
    _walk(node)
    return seen


def validate_predicate_fields(node: Any, allowed: Sequence[str]) -> None:
    """字段白名单校验：所有引用字段必须属于 DatasetDescriptor schema。"""
    allowed_set = set(allowed)
    for f in iter_fields(node):
        if f not in allowed_set:
            raise PredicateError(
                f"field '{f}' not in dataset schema (allowed: {sorted(allowed_set)[:20]})"
            )


def predicate_summary(node: Any) -> str:
    """单行谓词摘要（explain/evidence 用，不含原始值以防泄漏）。"""
    op = getattr(node, "op", "?")
    f = getattr(node, "field", None)
    if op in ("and", "or"):
        inner = " AND " if op == "and" else " OR "
        return "(" + inner.join(predicate_summary(a) for a in node.args) + ")"
    if op == "not":
        return f"NOT {predicate_summary(node.arg)}"
    if op == "is_null":
        return f"{node.field} IS{' NOT' if node.negated else ''} NULL"
    if f is not None:
        return f"{f} {op}"
    return op


# ── 本地求值器（fallback / 联邦本地过滤）────────────────────────────────────


def _to_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _like_to_regex(pattern: str) -> "re.Pattern[str]":
    out = ["^"]
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    out.append("$")
    return re.compile("".join(out), re.DOTALL)


def evaluate_predicate(node: Any, properties: Dict[str, Any]) -> bool:
    """属性谓词本地求值（SQL 三值逻辑对齐，R2-M6）。

    NULL 语义与 SQL 一致：比较/IN/LIKE 遇 NULL → unknown（最终按 False 排除）；
    NOT unknown → unknown（同样排除）——此前 NOT/ne/not_in 对 NULL 返回 True，
    与服务器端行集不一致。
    """
    result = _eval_three_valued(node, properties)
    return result is True  # unknown → False（行被排除，与 SQL WHERE 一致）


def _eval_three_valued(node: Any, properties: Dict[str, Any]) -> Optional[bool]:
    """True/False/None（unknown）三值求值。"""
    op = node.op
    if op == "and":
        results = [_eval_three_valued(a, properties) for a in node.args]
        if any(r is False for r in results):
            return False
        if any(r is None for r in results):
            return None
        return True
    if op == "or":
        results = [_eval_three_valued(a, properties) for a in node.args]
        if any(r is True for r in results):
            return True
        if any(r is None for r in results):
            return None
        return False
    if op == "not":
        r = _eval_three_valued(node.arg, properties)
        if r is None:
            return None
        return not r

    val = properties.get(node.field)
    if op == "is_null":
        return (val is None) != bool(node.negated)

    # 以下操作遇 NULL → unknown
    if val is None:
        return None

    if op in ("in", "not_in"):
        members = node.values
        hit = any(val == m or (isinstance(val, (int, float)) and _to_number(m) == _to_number(val)) for m in members)
        if op == "in":
            return hit
        # F5/SQL 三值逻辑：x NOT IN (a, NULL) 永不为 TRUE —— 命中 → FALSE；
        # 未命中但成员含 NULL → unknown（行被 WHERE 排除），与服务器端一致。
        if hit:
            return False
        if any(m is None for m in members):
            return None
        return True
    if op == "between":
        lo, hi = _to_number(node.low), _to_number(node.high)
        nv = _to_number(val)
        if nv is not None and lo is not None and hi is not None:
            return lo <= nv <= hi
        return str(node.low) <= str(val) <= str(node.high)
    if op == "like":
        return bool(_like_to_regex(node.pattern).match(str(val)))

    target = node.value
    nv, nt = _to_number(val), _to_number(target)
    if nv is not None and nt is not None:
        a, b = nv, nt
    else:
        a, b = str(val), str(target)
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "gt":
        return a > b
    if op == "ge":
        return a >= b
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    raise PredicateError(f"cannot evaluate op {op!r} locally")


def evaluate_temporal(node: Any, properties: Dict[str, Any]) -> bool:
    raw = properties.get(node.field)
    if raw is None:
        return False
    if isinstance(raw, (datetime, date)):
        tv = raw
    else:
        s = str(raw)
        try:
            tv = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return False
    def _pt(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    if node.op == "before":
        return tv < _pt(node.value)
    if node.op == "after":
        return tv > _pt(node.value)
    return _pt(node.start) <= tv <= _pt(node.end)


__all__ = [
    "PredicateError",
    "Predicate",
    "SpatialPredicate",
    "TemporalPredicate",
    "ScalarValue",
    "Eq", "Ne", "Gt", "Ge", "Lt", "Le", "In", "NotIn", "Between", "Like", "IsNull",
    "And", "Or", "Not",
    "BBox", "Intersects", "Within", "Contains", "Touches", "Overlaps", "DWithin",
    "Before", "After", "During",
    "predicate_from_dict",
    "spatial_from_dict",
    "temporal_from_dict",
    "predicate_to_canonical_dict",
    "canonical_payload",
    "predicate_fingerprint",
    "iter_fields",
    "validate_predicate_fields",
    "predicate_summary",
    "evaluate_predicate",
    "evaluate_temporal",
    "bbox_crosses_antimeridian",
    "split_antimeridian_bbox",
]
