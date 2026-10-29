"""属性编辑算子 —— set_fields / rename_field 的校验与 delta 构造（纯函数）。

校验证据来自数据集自身（有界扫描）：
- 字段存在性：目标字段的 key 必须已在 properties 出现（不借编辑新增列；
  新列属 schema 演进，超出本词表）。
- 类型：目标字段在数据集中的既有值类型（string/number/boolean/null 混合
  取「多数面」并披露）；新值类型不符 → type_mismatch。
- 枚举：字段 distinct 值 ≤ 阈值且全为短字符串 → 视为 categorial，词表外
  新值 → enum_violation（finding 带 distinct 词表，供模型改口而非瞎猜）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.feature_edit.contracts import (
    MAX_RENAME_LENGTH,
    RESERVED_FIELD_NAMES,
    WFID_PROP,
    finding,
    json_field_type,
)

__all__ = [
    "FieldProfile",
    "profile_fields",
    "validate_set_fields",
    "validate_rename_field",
    "build_attr_deltas",
]

# distinct 阈值：≤ 该值且全短字符串的字段按 categorial 处理（枚举校验）。
ENUM_DISTINCT_LIMIT = 24
ENUM_VALUE_MAX_LEN = 64
# 有界扫描：字段面统计只看前 N 条要素（类型/枚举面足够稳定，O(features)
# 全扫对 20k 级数据集也可接受，但统计面必须显式有界）。
PROFILE_SCAN_LIMIT = 20_000


class FieldProfile:
    """单字段的有界统计（可 JSON 化为 plan 校验证据）。"""

    __slots__ = ("name", "types", "distinct", "values_scanned")

    def __init__(self, name: str) -> None:
        self.name = name
        self.types: Dict[str, int] = {}
        self.distinct: Dict[str, int] = {}
        self.values_scanned = 0

    def observe(self, value: Any) -> None:
        self.values_scanned += 1
        self.types[json_field_type(value)] = self.types.get(json_field_type(value), 0) + 1
        if isinstance(value, str) and len(value) <= ENUM_VALUE_MAX_LEN:
            self.distinct[value] = self.distinct.get(value, 0) + 1
            if len(self.distinct) > ENUM_DISTINCT_LIMIT:
                # 超限即放弃 distinct 追踪（高基数字段不是 categorial）
                self.distinct = {}

    @property
    def is_categorical(self) -> bool:
        return 0 < len(self.distinct) <= ENUM_DISTINCT_LIMIT

    @property
    def dominant_type(self) -> Optional[str]:
        """多数类型面（null 不参与多数裁决；无证据 → None）。"""
        candidates = {t: c for t, c in self.types.items() if t not in ("null", "unsupported")}
        if not candidates:
            return None
        return max(candidates.items(), key=lambda kv: kv[1])[0]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "types": dict(self.types)}
        if self.is_categorical:
            out["enum"] = sorted(self.distinct.keys())
        return out


def profile_fields(fc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """数据集字段面（有界扫描；返回 {field: profile_dict}）。"""
    profiles: Dict[str, FieldProfile] = {}
    scanned = 0
    for feature in fc.get("features") or []:
        if scanned >= PROFILE_SCAN_LIMIT:
            break
        if not isinstance(feature, dict):
            continue
        scanned += 1
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        for key, value in props.items():
            if key == WFID_PROP:
                continue
            profile = profiles.get(key)
            if profile is None:
                profile = profiles[key] = FieldProfile(str(key))
            profile.observe(value)
    return {name: p.to_dict() for name, p in profiles.items()}


def validate_set_fields(
    fields: Dict[str, Any], field_profiles: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """set_fields 的词表/类型/枚举校验（返回 findings；空 = 通过）。"""
    findings: List[Dict[str, Any]] = []
    if not isinstance(fields, dict) or not fields:
        return [finding("invalid_op", "set_fields requires a non-empty fields map")]
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            findings.append(finding("invalid_field_name", "field name must be a non-empty string"))
            continue
        if name in RESERVED_FIELD_NAMES:
            findings.append(finding("reserved_field", "reserved system field", field=name))
            continue
        if len(name) > MAX_RENAME_LENGTH:
            findings.append(finding("invalid_field_name", "field name too long", field=name))
            continue
        profile = field_profiles.get(name)
        if profile is None:
            findings.append(
                finding(
                    "unknown_field",
                    "field does not exist in dataset (creating new columns is out of scope)",
                    field=name,
                    known_fields=sorted(field_profiles.keys())[:24],
                )
            )
            continue
        value_type = json_field_type(value)
        if value_type == "unsupported":
            findings.append(
                finding("type_mismatch", "only scalar values are editable", field=name)
            )
            continue
        dominant = profile.get("types") or {}
        if value_type not in dominant and dominant:
            # 全 null 字段（无类型证据）放行首个标量赋值；否则必须落在既有类型面
            if not (set(dominant.keys()) == {"null"}):
                findings.append(
                    finding(
                        "type_mismatch",
                        "value type does not match field's existing types",
                        field=name,
                        value_type=value_type,
                        field_types=sorted(dominant.keys()),
                    )
                )
                continue
        enum_values = profile.get("enum")
        if isinstance(enum_values, list) and isinstance(value, str) and value not in enum_values:
            findings.append(
                finding(
                    "enum_violation",
                    "value outside the field's observed vocabulary",
                    field=name,
                    value=value[:64],
                    allowed=enum_values[:ENUM_DISTINCT_LIMIT],
                )
            )
    return findings


def validate_rename_field(
    src: Any, dst: Any, field_profiles: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """rename_field 校验（存在性/保留字/冲突）。"""
    findings: List[Dict[str, Any]] = []
    if not isinstance(src, str) or not src:
        return [finding("invalid_field_name", "rename from must be a non-empty string")]
    if not isinstance(dst, str) or not dst:
        return [finding("invalid_field_name", "rename to must be a non-empty string")]
    if len(dst) > MAX_RENAME_LENGTH:
        return [finding("invalid_field_name", "new field name too long", field=dst)]
    if dst in RESERVED_FIELD_NAMES:
        return [finding("reserved_field", "cannot rename onto reserved system field", field=dst)]
    if src not in field_profiles:
        return [
            finding(
                "unknown_field",
                "source field does not exist",
                field=src,
                known_fields=sorted(field_profiles.keys())[:24],
            )
        ]
    if dst in field_profiles:
        return [finding("invalid_field_name", "target field already exists", field=dst)]
    return findings


def build_attr_deltas(
    fc: Dict[str, Any],
    wfids: List[str],
    fields: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """构造 per-feature set_fields deltas（before/after 全留痕）。"""
    deltas: List[Dict[str, Any]] = []
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if not isinstance(wfid, str) or wfid not in wfids:
            continue
        field_changes: Dict[str, Dict[str, Any]] = {}
        for name, value in fields.items():
            before = props.get(name) if isinstance(props, dict) else None
            field_changes[name] = {"before": before, "after": value}
        deltas.append({"wfid": wfid, "fields": field_changes})
    return deltas
