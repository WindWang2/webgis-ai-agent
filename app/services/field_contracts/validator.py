"""G44 纯验证内核 —— 预览 / 提交 / 完整评估 / undo-redo 共用的同一实现。

纪律（WP03/WP04）：
- **严格类型**：bool≠number；字符串 "1"≠number 1；NaN/Inf 拒绝；integer 接受整值
  float（JSON 无 int 字面量区分）；任意精度整数无损（Python 原生 bigint）。
- **三态显式**：missing（键缺）/ null / empty-string 各自独立判定，无隐藏转换；
  无 trim / casefold / Unicode 规范化 / 字符串数字互转。
- **date**：严格 ``YYYY-MM-DD`` 正则 + 日历校验（闰年拒绝 2023-02-29）。
- **datetime**：强制显式 offset；小数秒 ≤6 位（超出拒绝不截断）；比较与唯一性
  使用规范化 UTC 瞬时（不同 offset 表示同一瞬时即冲突），原值不改写。
- **唯一性**：受控哈希分组 O(N)（绝不两两比较）；null/missing 政策显式。
- **确定性**：违规码封闭（contracts.VIOLATION_CODES）、顺序稳定（声明序 × 行序）。
- **有界**：单值投影 ≤64 字符；findings 物化上限 + 溢出计数。

本模块零 I/O；调用方负责载荷冻结、授权与预算。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timezone
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.field_contracts.contracts import (
    COMPARE_OPERATORS,
    CONDITION_OPERATORS,
    FINDING_VALUE_PROJECTION,
    MAX_ASSESSABLE_FEATURES,
    MAX_CROSS_RULES,
    MAX_ENUM_TOTAL_BYTES,
    MAX_ENUM_VALUES,
    MAX_FIELDS_PER_CONTRACT,
    MAX_MATERIALIZED_FINDINGS,
    MAX_RULES_JSON_BYTES,
    ORDERED_COMPARABLE,
    UNIQUE_NULL_POLICIES,
    EMPTY_STRING_POLICIES,
    FIELD_TYPE_NAMES,
    MAX_DATETIME_FRACTION_DIGITS,
    MAX_FIELD_NAME_LEN,
)

__all__ = [
    "NormalizedContract",
    "normalize_document",
    "contract_digest",
    "declared_fields",
    "check_feature",
    "assess_feature_collection",
    "validate_candidate_state",
    "ContractAssessmentOutcome",
]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?"
    r"(Z|[+-]\d{2}:\d{2})$"
)


class ContractDocumentError(ValueError):
    """合同文档自身非法（作者期错误；不进入运行时违规词表）。"""

    def __init__(self, errors: List[str]) -> None:
        super().__init__("; ".join(errors[:8]))
        self.errors = errors


# ─── 值类型判定（严格；无任何转换）───────────────────────────────────────


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True


def _parse_date_strict(value: str) -> Optional[date]:
    """严格 ISO 日历日期；非法/闰年错误 → None。"""
    if not _DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime_strict(value: str) -> Tuple[Optional[datetime], Optional[str]]:
    """严格 ISO datetime（强制 offset，小数秒 ≤6 位）。

    返回 (utc_norm, err_code)：err_code ∈ {datetime_offset_required,
    datetime_precision, invalid_datetime}；成功时 utc_norm 为带 UTC tz 的瞬时。
    """
    m = _DT_RE.match(value)
    if not m:
        # 无 offset 的形状单独定位（更可解释的错误）
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?$", value):
            return None, "datetime_offset_required"
        return None, "invalid_datetime"
    frac = m.group(3)
    if frac is not None and len(frac) > MAX_DATETIME_FRACTION_DIGITS:
        return None, "datetime_precision"
    base = f"{m.group(1)}T{m.group(2)}" + (f".{frac}" if frac else "")
    offset = m.group(4)
    try:
        parsed = datetime.fromisoformat(base + ("+00:00" if offset == "Z" else offset))
    except ValueError:
        return None, "invalid_datetime"
    return parsed.astimezone(timezone.utc), None


def _normalize_ordered_key(type_name: str, value: Any) -> Tuple[str, Any]:
    """同类型有序比较/唯一键的规范化值（不改写数据，仅判定用）。

    datetime → UTC 瞬时（微秒）；date → date 对象；number/integer → float 兼容
    比较（bigint 无损路径单独处理：仅当 |v| ≤ 2^53 才转 float，否则按整数值）。
    """
    if type_name == "datetime":
        utc, _err = _parse_datetime_strict(value)
        return ("dt", utc.timestamp() if utc is not None else None)
    if type_name == "date":
        return ("d", _parse_date_strict(value))
    if type_name in ("number", "integer"):
        if isinstance(value, int):
            # bigint：可精确转 float 的转 float（比较稳定），否则保留 int（Python
            # 任意精度无损，与 float 比较也精确）
            if -(2**53) <= value <= 2**53:
                return ("n", float(value))
            return ("n", value)
        return ("n", float(value))
    if type_name == "boolean":
        return ("b", bool(value))
    return ("s", value)  # string：原字符精确比较（无规范化）


def _unique_key_component(type_name: str, value: Any) -> Tuple[Any, ...]:
    """唯一键分量（类型标签 + 规范值）—— 类型不同即不同键（F10）。"""
    tag, norm = _normalize_ordered_key(type_name, value)
    return (tag, norm)


# ─── 归一化合同文档 ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldRule:
    field: str
    type: str
    required: bool
    nullable: bool
    empty_string: str
    enum: Optional[Tuple[str, ...]]
    min: Optional[float | int]   # F08：int 边界不强转 float（bigint 无损）
    max: Optional[float | int]
    exclusive_min: bool
    exclusive_max: bool
    min_length: Optional[int]
    max_length: Optional[int]
    label: Optional[str]
    description: Optional[str]
    unit: Optional[str]
    default: Any
    form_control: Optional[str]


@dataclass(frozen=True)
class CrossRule:
    rule_id: str
    kind: str
    when: Optional[Tuple[str, str, Any, Tuple[Any, ...]]]  # (field, op, value, values)
    require: Tuple[str, ...]
    left: Optional[str]
    op: Optional[str]
    right: Optional[str]
    fields: Tuple[str, ...]
    null_policy: str


@dataclass(frozen=True)
class NormalizedContract:
    document: Dict[str, Any]           # canonical JSON dict（digest 输入）
    fields: Tuple[FieldRule, ...]
    cross_rules: Tuple[CrossRule, ...]
    field_map: Dict[str, FieldRule]

    def declared_field_names(self) -> List[str]:
        return [f.field for f in self.fields]


def normalize_document(raw: Dict[str, Any]) -> NormalizedContract:
    """合同文档 → 归一化合同（作者期错误 → ContractDocumentError，全量列出）。"""
    errors: List[str] = []
    if not isinstance(raw, dict):
        raise ContractDocumentError(["document must be an object"])
    version = raw.get("version", 1)
    if version != 1:
        errors.append(f"unsupported document version: {version!r}")
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ContractDocumentError(["fields must be a non-empty list"])
    if len(raw_fields) > MAX_FIELDS_PER_CONTRACT:
        errors.append(f"too many field rules ({len(raw_fields)} > {MAX_FIELDS_PER_CONTRACT})")

    fields: List[FieldRule] = []
    seen: Dict[str, int] = {}
    enum_bytes = 0
    for idx, rf in enumerate(raw_fields):
        if not isinstance(rf, dict):
            errors.append(f"fields[{idx}] must be an object")
            continue
        name = rf.get("field")
        if not isinstance(name, str) or not name or len(name) > MAX_FIELD_NAME_LEN:
            errors.append(f"fields[{idx}].field invalid")
            continue
        if name in seen:
            errors.append(f"duplicate field rule: {name}")
            continue
        seen[name] = idx
        ftype = rf.get("type")
        if ftype not in FIELD_TYPE_NAMES:
            errors.append(f"field {name}: unknown type {ftype!r}")
            continue
        enum = rf.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not enum:
                errors.append(f"field {name}: enum must be a non-empty list")
                enum = None
            elif len(enum) > MAX_ENUM_VALUES:
                errors.append(f"field {name}: enum exceeds {MAX_ENUM_VALUES} values")
                enum = None
            elif not all(isinstance(v, str) for v in enum):
                errors.append(f"field {name}: enum values must be strings")
                enum = None
            else:
                if len(set(enum)) != len(enum):
                    errors.append(f"field {name}: enum has duplicate values")
                    enum = sorted(set(enum))
                if rf.get("empty_string") == "allow" and "" not in enum:
                    errors.append(f"field {name}: empty_string=allow requires \"\" in enum")
                enum_bytes += sum(len(v.encode("utf-8")) for v in enum)
        if ftype != "string" and enum is not None:
            errors.append(f"field {name}: enum only applies to string type")
        mn, mx = rf.get("min"), rf.get("max")
        for bound_name, bound in (("min", mn), ("max", mx)):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, (int, float))
                or (isinstance(bound, float) and not math.isfinite(bound))
            ):
                errors.append(f"field {name}: {bound_name} must be a finite number")
        if ftype not in ("number", "integer") and (mn is not None or mx is not None):
            errors.append(f"field {name}: min/max only apply to number/integer")
        if ftype != "string" and (rf.get("min_length") is not None or rf.get("max_length") is not None):
            errors.append(f"field {name}: min_length/max_length only apply to string")
        es = rf.get("empty_string", "reject")
        if es not in EMPTY_STRING_POLICIES:
            errors.append(f"field {name}: empty_string must be one of {sorted(EMPTY_STRING_POLICIES)}")
        default = rf.get("default")
        if default is not None and _json_type(default) in ("unsupported",):
            errors.append(f"field {name}: default must be a scalar or null")
        fields.append(
            FieldRule(
                field=name,
                type=ftype,
                required=bool(rf.get("required", False)),
                nullable=bool(rf.get("nullable", False)),
                empty_string=es,
                enum=tuple(enum) if enum is not None else None,
                # F08：边界保持原值（int 不强转 float —— 2^53+1 转 float 会舍入）
                min=mn if isinstance(mn, (int, float)) and not isinstance(mn, bool) else None,
                max=mx if isinstance(mx, (int, float)) and not isinstance(mx, bool) else None,
                exclusive_min=bool(rf.get("exclusive_min", False)),
                exclusive_max=bool(rf.get("exclusive_max", False)),
                min_length=rf.get("min_length"),
                max_length=rf.get("max_length"),
                label=rf.get("label"),
                description=rf.get("description"),
                unit=rf.get("unit"),
                default=default,
                form_control=rf.get("form_control"),
            )
        )
    if enum_bytes > MAX_ENUM_TOTAL_BYTES:
        errors.append(f"total enum bytes {enum_bytes} exceed {MAX_ENUM_TOTAL_BYTES}")

    raw_cross = raw.get("cross_rules") or []
    if not isinstance(raw_cross, list):
        errors.append("cross_rules must be a list")
        raw_cross = []
    if len(raw_cross) > MAX_CROSS_RULES:
        errors.append(f"too many cross rules ({len(raw_cross)} > {MAX_CROSS_RULES})")
    cross: List[CrossRule] = []
    rule_ids: set = set()
    field_map = {f.field: f for f in fields}
    for idx, cr in enumerate(raw_cross):
        if not isinstance(cr, dict):
            errors.append(f"cross_rules[{idx}] must be an object")
            continue
        rid = cr.get("rule_id")
        if not isinstance(rid, str) or not rid:
            errors.append(f"cross_rules[{idx}].rule_id invalid")
            continue
        if rid in rule_ids:
            errors.append(f"duplicate rule_id: {rid}")
            continue
        rule_ids.add(rid)
        kind = cr.get("kind")
        if kind == "conditional_required":
            when_raw = cr.get("when")
            if not isinstance(when_raw, dict):
                errors.append(f"rule {rid}: conditional_required requires when")
                continue
            wfield = when_raw.get("field")
            wop = when_raw.get("op")
            if wfield not in field_map:
                errors.append(f"rule {rid}: when.field {wfield!r} not declared")
                continue
            if wop not in CONDITION_OPERATORS:
                errors.append(f"rule {rid}: when.op {wop!r} unknown")
                continue
            values = when_raw.get("values")
            value = when_raw.get("value")
            if wop in ("in", "not_in"):
                if not isinstance(values, list) or not values or len(values) > MAX_ENUM_VALUES:
                    errors.append(f"rule {rid}: op {wop} requires values list")
                    continue
            elif value is None and wop not in ("ne",):
                # eq/gt/... 需要比值；ne null 允许（值非 null 时满足）
                errors.append(f"rule {rid}: op {wop} requires value")
                continue
            require = cr.get("require")
            if not isinstance(require, list) or not require:
                errors.append(f"rule {rid}: conditional_required requires require list")
                continue
            for rf_ in require:
                if rf_ not in field_map:
                    errors.append(f"rule {rid}: require field {rf_!r} not declared")
            wtype = field_map[wfield].type
            if wop in ("gt", "gte", "lt", "lte") and wtype not in ORDERED_COMPARABLE:
                errors.append(f"rule {rid}: ordered op on non-comparable type {wtype}")
            cross.append(
                CrossRule(rid, kind, (wfield, wop, value, tuple(values or ())), tuple(require),
                          None, None, None, (), "ignore")
            )
        elif kind == "compare":
            left, op, right = cr.get("left"), cr.get("op"), cr.get("right")
            if left not in field_map or right not in field_map:
                errors.append(f"rule {rid}: compare left/right must be declared fields")
                continue
            if op not in COMPARE_OPERATORS:
                errors.append(f"rule {rid}: compare op {op!r} unknown")
                continue
            lt_, rt_ = field_map[left].type, field_map[right].type
            if lt_ != rt_ or lt_ not in ORDERED_COMPARABLE:
                errors.append(
                    f"rule {rid}: compare requires same ordered type ({lt_} vs {rt_})"
                )
                continue
            cross.append(CrossRule(rid, kind, None, (), left, op, right, (), "ignore"))
        elif kind == "unique":
            ufields = cr.get("fields")
            if not isinstance(ufields, list) or not 1 <= len(ufields) <= 4:
                errors.append(f"rule {rid}: unique.fields must be 1-{4} fields")
                continue
            for uf in ufields:
                if uf not in field_map:
                    errors.append(f"rule {rid}: unique field {uf!r} not declared")
            if any(uf not in field_map for uf in ufields):
                continue
            null_policy = cr.get("null_policy", "ignore")
            if null_policy not in UNIQUE_NULL_POLICIES:
                errors.append(f"rule {rid}: null_policy must be one of {sorted(UNIQUE_NULL_POLICIES)}")
                continue
            cross.append(CrossRule(rid, kind, None, (), None, None, None, tuple(ufields), null_policy))
        else:
            errors.append(f"cross_rules[{idx}].kind {kind!r} unknown")

    if errors:
        raise ContractDocumentError(errors)

    canonical = {
        "version": 1,
        "title": str(raw.get("title") or "")[:200],
        "notes": raw.get("notes"),
        "fields": [
            {
                "field": f.field,
                "type": f.type,
                "required": f.required,
                "nullable": f.nullable,
                "empty_string": f.empty_string,
                **({"enum": list(f.enum)} if f.enum is not None else {}),
                **({"min": f.min} if f.min is not None else {}),
                **({"max": f.max} if f.max is not None else {}),
                **({"exclusive_min": True} if f.exclusive_min else {}),
                **({"exclusive_max": True} if f.exclusive_max else {}),
                **({"min_length": f.min_length} if f.min_length is not None else {}),
                **({"max_length": f.max_length} if f.max_length is not None else {}),
                "label": f.label,
                "description": f.description,
                "unit": f.unit,
                "default": f.default,
                **({"form_control": f.form_control} if f.form_control else {}),
            }
            for f in fields
        ],
        "cross_rules": [
            {
                "rule_id": c.rule_id,
                "kind": c.kind,
                **({"when": {"field": c.when[0], "op": c.when[1],
                             "value": c.when[2],
                             **({"values": list(c.when[3])} if c.when[3] else {})}}
                   if c.when else {}),
                **({"require": list(c.require)} if c.require else {}),
                **({"left": c.left, "op": c.op, "right": c.right} if c.left else {}),
                **({"fields": list(c.fields), "null_policy": c.null_policy} if c.fields else {}),
            }
            for c in cross
        ],
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(blob.encode("utf-8")) > MAX_RULES_JSON_BYTES:
        raise ContractDocumentError(
            [f"rules JSON exceeds {MAX_RULES_JSON_BYTES} bytes"]
        )
    return NormalizedContract(
        document=canonical, fields=tuple(fields), cross_rules=tuple(cross), field_map=field_map
    )


def contract_digest(nc: NormalizedContract) -> str:
    import hashlib

    blob = json.dumps(nc.document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def declared_fields(nc: Optional[NormalizedContract]) -> "set[str]":
    return set(nc.field_map) if nc else set()


# ─── 单要素检查 ──────────────────────────────────────────────────────────


def _violation(
    code: str, *, rule_id: Optional[str] = None, field_name: Optional[str] = None,
    wfid: Optional[str] = None, row: Optional[int] = None, value: Any = None,
    **detail: Any,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"code": code}
    if rule_id:
        out["rule_id"] = rule_id
    if field_name:
        out["field"] = field_name
    if wfid is not None:
        out["wfid"] = wfid
    if row is not None:
        out["row"] = row
    if value is not None and not isinstance(value, (dict, list)):
        if isinstance(value, str):
            out["value"] = value[:FINDING_VALUE_PROJECTION]
        elif isinstance(value, bool):
            out["value"] = value
        elif isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                out["value"] = repr(value)[:FINDING_VALUE_PROJECTION]
            else:
                # R1-P3：bigint 文本化截断（与字符串同 64 字符投影预算）
                text = str(value)
                out["value"] = value if len(text) <= FINDING_VALUE_PROJECTION else text[:FINDING_VALUE_PROJECTION]
    if detail:
        out["detail"] = detail
    return out


def _check_value_against_rule(rule: FieldRule, value: Any, *, wfid: Optional[str], row: Optional[int]) -> List[Dict[str, Any]]:
    """单字段单值检查（value 已非 missing 且非 None）。"""
    out: List[Dict[str, Any]] = []
    t = rule.type
    jt = _json_type(value)
    # 严格类型面
    if t == "string":
        if jt != "string":
            return [_violation("type_mismatch", field_name=rule.field, wfid=wfid, row=row,
                               value=value, expected="string", got=jt)]
    elif t == "boolean":
        if jt != "boolean":
            return [_violation("type_mismatch", field_name=rule.field, wfid=wfid, row=row,
                               value=value, expected="boolean", got=jt)]
    elif t in ("number", "integer"):
        if jt != "number":
            return [_violation("type_mismatch", field_name=rule.field, wfid=wfid, row=row,
                               value=value, expected=t, got=jt)]
        if isinstance(value, float) and not math.isfinite(value):
            return [_violation("non_finite_number", field_name=rule.field, wfid=wfid, row=row)]
        if t == "integer":
            if isinstance(value, float) and not value.is_integer():
                return [_violation("not_integer", field_name=rule.field, wfid=wfid, row=row,
                                   value=value)]
    elif t in ("date", "datetime"):
        if jt != "string":
            return [_violation("type_mismatch", field_name=rule.field, wfid=wfid, row=row,
                               value=value, expected=t, got=jt)]
        if t == "date":
            if _parse_date_strict(value) is None:
                return [_violation("invalid_date", field_name=rule.field, wfid=wfid, row=row,
                                   value=value)]
        else:
            _utc, err = _parse_datetime_strict(value)
            if err:
                return [_violation(err, field_name=rule.field, wfid=wfid, row=row, value=value)]

    # 类型通过 → 取值域（空串优先且唯一：reject 政策下不再叠加枚举/长度，确定性）
    if t == "string" and value == "":
        if rule.empty_string == "reject":
            return [_violation("empty_string_rejected", field_name=rule.field, wfid=wfid, row=row,
                               value=value)]
        # allow：空串仍须落在枚举与长度域内（作者期已保证枚举含 ""，此处运行时兜底）
    if rule.enum is not None and value not in rule.enum:
        out.append(_violation("enum_violation", field_name=rule.field, wfid=wfid, row=row,
                              value=value, allowed=rule.enum[:24],
                              allowed_total=len(rule.enum)))
    if t in ("number", "integer"):
        # Python int/float 比较精确（bigint 无损；float 边界为有限数）
        v: Any = value
        violated = False
        if rule.min is not None and (v < rule.min or (rule.exclusive_min and v == rule.min)):
            violated = True
        if rule.max is not None and (v > rule.max or (rule.exclusive_max and v == rule.max)):
            violated = True
        if violated:
            out.append(_violation("number_range", field_name=rule.field, wfid=wfid, row=row,
                                  value=value, min=rule.min, max=rule.max,
                                  exclusive_min=rule.exclusive_min,
                                  exclusive_max=rule.exclusive_max))
    if t == "string":
        ln = len(value)  # 码点
        if (rule.min_length is not None and ln < rule.min_length) or (
            rule.max_length is not None and ln > rule.max_length
        ):
            out.append(_violation("string_length", field_name=rule.field, wfid=wfid, row=row,
                                  length=ln, min_length=rule.min_length,
                                  max_length=rule.max_length))
    return out


def check_feature(
    props: Optional[Dict[str, Any]],
    nc: NormalizedContract,
    *,
    wfid: Optional[str] = None,
    row: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """单要素全字段 + 跨字段（非唯一）检查。"""
    out: List[Dict[str, Any]] = []
    props = props if isinstance(props, dict) else {}
    for rule in nc.fields:
        if rule.field not in props:
            if rule.required:
                out.append(_violation("required_missing", field_name=rule.field, wfid=wfid, row=row))
            continue
        value = props[rule.field]
        if value is None:
            if not rule.nullable:
                out.append(_violation("null_not_allowed", field_name=rule.field, wfid=wfid, row=row))
            continue
        out.extend(_check_value_against_rule(rule, value, wfid=wfid, row=row))
    # 跨字段（非唯一）
    for cr in nc.cross_rules:
        if cr.kind == "conditional_required":
            assert cr.when is not None
            wfield, wop, wvalue, wvalues = cr.when
            trig = props.get(wfield, None)
            present = wfield in props
            satisfied = False
            if present and trig is not None:
                trig_type = nc.field_map[wfield].type
                if wop in ("eq", "ne", "gt", "gte", "lt", "lte"):
                    if _json_type(trig) == _json_type(wvalue) or (
                        _json_type(trig) == "number" and _json_type(wvalue) == "number"
                    ):
                        if wop in ("eq", "ne"):
                            if _json_type(trig) == "string" and trig_type == "datetime":
                                # R1-P3：datetime 的 eq/ne 按规范 UTC 瞬时比较
                                #（与 compare/unique 同语义；不同 offset 的同一
                                # 瞬时视为相等）。
                                t_utc, t_err = _parse_datetime_strict(trig)
                                v_utc, v_err = _parse_datetime_strict(wvalue)
                                if t_err or v_err:
                                    satisfied = False
                                else:
                                    same = t_utc == v_utc
                                    satisfied = same if wop == "eq" else not same
                            else:
                                satisfied = (trig == wvalue) if wop == "eq" else (trig != wvalue)
                        else:
                            try:
                                lk = _normalize_ordered_key(trig_type, trig)
                                rk = _normalize_ordered_key(trig_type, wvalue)
                                if lk[1] is not None and rk[1] is not None:
                                    satisfied = {
                                        "gt": lk[1] > rk[1], "gte": lk[1] >= rk[1],
                                        "lt": lk[1] < rk[1], "lte": lk[1] <= rk[1],
                                    }[wop]
                            except Exception:  # noqa: BLE001 — 不可比 → 不满足
                                satisfied = False
                    else:
                        satisfied = wop == "ne"  # eq 跨类型恒假；ne 跨类型恒真
                elif wop in ("in", "not_in"):
                    member = any(
                        trig == v and _json_type(trig) == _json_type(v) for v in wvalues
                    )
                    satisfied = member if wop == "in" else not member
            if satisfied:
                for rf_ in cr.require:
                    if rf_ not in props:
                        out.append(_violation(
                            "conditional_required", rule_id=cr.rule_id, field_name=rf_,
                            wfid=wfid, row=row, when_field=wfield,
                        ))
        elif cr.kind == "compare":
            left_v = props.get(cr.left, None) if cr.left in props else None
            right_v = props.get(cr.right, None) if cr.right in props else None
            if cr.left not in props or cr.right not in props or left_v is None or right_v is None:
                continue  # 缺/null 由字段规则管辖；compare 不适用（确定性跳过）
            lt_ = nc.field_map[cr.left].type
            # R1-P1：两侧 JSON 类型都必须与声明类型面一致才可比 —— 只查左值
            # 会让字符串声明 + 数字右值进到 < 比较抛 TypeError（500 而非
            # typed finding）。类型不匹配 = 字段规则已报错，compare 跳过。
            def _type_ok(field_name: str, value: Any) -> bool:
                declared = nc.field_map[field_name].type
                jt = _json_type(value)
                if declared in ("number", "integer"):
                    return jt == "number"
                if declared == "boolean":
                    return jt == "boolean"
                return jt == "string"  # string/date/datetime

            if not (_type_ok(cr.left, left_v) and _type_ok(cr.right, right_v)):
                continue
            try:
                lk = _normalize_ordered_key(lt_, left_v)
                rk = _normalize_ordered_key(lt_, right_v)
                if lk[1] is None or rk[1] is None:
                    continue
                ok = {
                    "lt": lk[1] < rk[1], "lte": lk[1] <= rk[1],
                    "gt": lk[1] > rk[1], "gte": lk[1] >= rk[1],
                }[str(cr.op)]
            except Exception:  # noqa: BLE001 — 不可比（含日历解析失败）确定性跳过
                continue
            if not ok:
                out.append(_violation(
                    "compare_violation", rule_id=cr.rule_id, wfid=wfid, row=row,
                    left=cr.left, right=cr.right, op=cr.op,
                ))
    return out


# ─── 完整评估（冻结快照）────────────────────────────────────────────────


@dataclass
class ContractAssessmentOutcome:
    total_features: int
    checked_features: int
    violations_materialized: List[Dict[str, Any]]
    violations_total: int
    overflow_count: int                      # 物化上限之外的违规数（只计数）
    counts_by_code: Dict[str, int]
    counts_by_rule: Dict[str, int]
    unique_conflict_groups: Dict[str, int]
    complete: bool                           # False = 预算耗尽/中止（incomplete，不可激活）
    stop_reason: Optional[str]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_features": self.total_features,
            "checked_features": self.checked_features,
            "violations_total": self.violations_total,
            "violations_materialized": len(self.violations_materialized),
            "overflow_count": self.overflow_count,
            "counts_by_code": dict(self.counts_by_code),
            "counts_by_rule": dict(self.counts_by_rule),
            "unique_conflict_groups": dict(self.unique_conflict_groups),
            "complete": self.complete,
            "stop_reason": self.stop_reason,
        }


def assess_feature_collection(
    fc: Dict[str, Any],
    nc: NormalizedContract,
    *,
    materialize_limit: int = MAX_MATERIALIZED_FINDINGS,
    should_continue: Optional[Iterable[None]] = None,
) -> ContractAssessmentOutcome:
    """完整数据版本评估：逐行 O(N×R) + 唯一性受控哈希分组。

    locator：优先 _wfid（物化/可编辑数据集），否则 source row index（只读评估）。
    """
    features = fc.get("features") or []
    outcome = ContractAssessmentOutcome(
        total_features=len(features), checked_features=0,
        violations_materialized=[], violations_total=0, overflow_count=0,
        counts_by_code={}, counts_by_rule={}, unique_conflict_groups={},
        complete=True, stop_reason=None,
    )
    if len(features) > MAX_ASSESSABLE_FEATURES:
        outcome.complete = False
        outcome.stop_reason = "dataset_exceeds_assessable_size"
        return outcome
    # 唯一性分组：rule_id -> {key_tuple: [行定位...]}
    unique_groups: Dict[str, Dict[Tuple[Any, ...], List[Dict[str, Any]]]] = {
        cr.rule_id: {} for cr in nc.cross_rules if cr.kind == "unique"
    }
    continue_iter = iter(should_continue) if should_continue is not None else None

    def _record(v: Dict[str, Any]) -> None:
        outcome.violations_total += 1
        code = str(v.get("code"))
        outcome.counts_by_code[code] = outcome.counts_by_code.get(code, 0) + 1
        rid = v.get("rule_id")
        if rid:
            outcome.counts_by_rule[str(rid)] = outcome.counts_by_rule.get(str(rid), 0) + 1
        if len(outcome.violations_materialized) < materialize_limit:
            outcome.violations_materialized.append(v)
        else:
            outcome.overflow_count += 1

    for row_index, feature in enumerate(features):
        if continue_iter is not None:
            try:
                next(continue_iter)
            except StopIteration:
                outcome.complete = False
                outcome.stop_reason = "cancelled"
                return outcome
        if not isinstance(feature, dict):
            continue
        outcome.checked_features += 1
        props = feature.get("properties")
        props = props if isinstance(props, dict) else {}
        wfid = props.get("_wfid") if isinstance(props.get("_wfid"), str) else None
        for v in check_feature(props, nc, wfid=wfid, row=row_index):
            _record(v)
        for cr in nc.cross_rules:
            if cr.kind != "unique":
                continue
            key: List[Any] = []
            skip = False
            for uf in cr.fields:
                if uf not in props:
                    if cr.null_policy == "ignore":
                        skip = True
                        break
                    key.append(("absent", None))
                    continue
                val = props[uf]
                if val is None:
                    if cr.null_policy == "ignore":
                        skip = True
                        break
                    key.append(("absent", None))  # participate: null≡missing
                    continue
                ftype = nc.field_map[uf].type
                jt = _json_type(val)
                if (
                    (ftype in ("date", "datetime") and jt != "string")
                    or (ftype == "string" and jt != "string")
                    or (ftype == "boolean" and jt != "boolean")
                    or (ftype in ("number", "integer") and jt != "number")
                ):
                    key.append(("badtype", jt, val if isinstance(val, (str, bool, int, float)) else None))
                    continue
                if ftype == "date" and _parse_date_strict(val) is None:
                    key.append(("baddate", val[:FINDING_VALUE_PROJECTION]))
                    continue
                if ftype == "datetime":
                    _utc, err = _parse_datetime_strict(val)
                    if err:
                        key.append(("baddt", err, val[:FINDING_VALUE_PROJECTION]))
                        continue
                key.append(_unique_key_component(ftype, val))
            if skip:
                continue
            bucket = unique_groups[cr.rule_id]
            kt = tuple(key)
            locator = {"wfid": wfid, "row": row_index}
            bucket.setdefault(kt, []).append(locator)

    # 唯一冲突物化（组 ≥2）—— 独立可核对：组数 + 每组行数
    for cr in nc.cross_rules:
        if cr.kind != "unique":
            continue
        for kt, locators in unique_groups[cr.rule_id].items():
            if len(locators) < 2:
                continue
            outcome.unique_conflict_groups[cr.rule_id] = (
                outcome.unique_conflict_groups.get(cr.rule_id, 0) + 1
            )
            key_repr: Dict[str, Any] = {}
            for uf, comp in zip(cr.fields, kt):
                key_repr[uf] = (
                    comp if isinstance(comp, (str, bool, int, float)) or comp is None
                    else (str(comp[1])[:FINDING_VALUE_PROJECTION] if len(comp) == 2 else str(comp[-1])[:FINDING_VALUE_PROJECTION])
                )
            for loc in locators:
                _record(_violation(
                    "unique_conflict", rule_id=cr.rule_id, wfid=loc.get("wfid"),
                    row=loc.get("row"), fields=list(cr.fields),
                    group_size=len(locators), key=key_repr,
                ))
    return outcome


# ─── 候选态门禁（编辑链：preview / commit / undo / redo / G32 merge）────


def validate_candidate_state(
    candidate_fc: Dict[str, Any],
    nc: NormalizedContract,
    *,
    changed_wfids: Optional[set] = None,
    schema_changed: bool = False,
) -> List[Dict[str, Any]]:
    """候选全量属性状态校验（编辑链唯一入口；与完整评估同一检查实现）。

    - 字段/跨字段（非唯一）规则：schema_changed=True 或 changed_wfids=None 时全量
      扫描；否则只扫变更行（激活后不变式：未变更行保持合规）。
    - 唯一性：恒全量候选分组（变更行可能与未选行冲突 —— F10/F11/F19）。
    - 物化 ≤ MAX_MATERIALIZED_FINDINGS；调用方负责有界投影进 EditFinding。
    """
    features = candidate_fc.get("features") or []
    if len(features) > MAX_ASSESSABLE_FEATURES:
        return [{
            "code": "rule_not_evaluable",
            "detail": {"reason": "candidate exceeds assessable size",
                        "features": len(features)},
        }]
    # 唯一性分组（与评估共用语义）
    unique_rules = [cr for cr in nc.cross_rules if cr.kind == "unique"]
    groups: Dict[str, Dict[Tuple[Any, ...], List[Dict[str, Any]]]] = {
        cr.rule_id: {} for cr in unique_rules
    }
    findings: List[Dict[str, Any]] = []
    full_scan = schema_changed or changed_wfids is None
    for row_index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        props = props if isinstance(props, dict) else {}
        wfid = props.get("_wfid") if isinstance(props.get("_wfid"), str) else None
        # 唯一性恒扫
        for cr in unique_rules:
            key: List[Any] = []
            skip = False
            for uf in cr.fields:
                if uf not in props:
                    if cr.null_policy == "ignore":
                        skip = True
                        break
                    key.append(("absent", None))
                    continue
                val = props[uf]
                if val is None:
                    if cr.null_policy == "ignore":
                        skip = True
                        break
                    key.append(("absent", None))
                    continue
                ftype = nc.field_map[uf].type
                jt = _json_type(val)
                type_ok = (
                    (ftype == "string" and jt == "string")
                    or (ftype == "boolean" and jt == "boolean")
                    or (ftype in ("number", "integer") and jt == "number")
                    or (ftype in ("date", "datetime") and jt == "string")
                )
                if not type_ok:
                    key.append(("badtype", jt))
                    continue
                if ftype == "date" and _parse_date_strict(val) is None:
                    key.append(("baddate",))
                    continue
                if ftype == "datetime":
                    _utc, err = _parse_datetime_strict(val)
                    if err:
                        key.append(("baddt", err))
                        continue
                key.append(_unique_key_component(ftype, val))
            if skip:
                continue
            groups[cr.rule_id].setdefault(tuple(key), []).append(
                {"wfid": wfid, "row": row_index}
            )
        # 字段规则
        if full_scan or (wfid is not None and wfid in (changed_wfids or set())):
            findings.extend(check_feature(props, nc, wfid=wfid, row=row_index))
    for cr in unique_rules:
        for kt, locs in groups[cr.rule_id].items():
            if len(locs) < 2:
                continue
            key_repr = {}
            for uf, comp in zip(cr.fields, kt):
                key_repr[uf] = (
                    comp if isinstance(comp, (str, bool, int, float)) or comp is None
                    else (str(comp[1])[:FINDING_VALUE_PROJECTION] if len(comp) == 2 else str(comp[-1])[:FINDING_VALUE_PROJECTION])
                )
            for loc in locs:
                findings.append(_violation(
                    "unique_conflict", rule_id=cr.rule_id, wfid=loc.get("wfid"),
                    row=loc.get("row"), fields=list(cr.fields),
                    group_size=len(locs), key=key_repr,
                ))
    if len(findings) > MAX_MATERIALIZED_FINDINGS:
        head = findings[:MAX_MATERIALIZED_FINDINGS]
        head.append({
            "code": "rule_not_evaluable",
            "detail": {
                "reason": "candidate violations exceed materialization limit",
                "violations_total": len(findings),
                "materialized": MAX_MATERIALIZED_FINDINGS,
            },
        })
        return head
    return findings
