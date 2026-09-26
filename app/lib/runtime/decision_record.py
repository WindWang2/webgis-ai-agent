"""决策溯源记录（方向 8 / ADR-0212 决策一）。

统一 DecisionRecord —— 链上载荷的 **additive 形态**：不加新 Stage、不改
链 schema；决策 riding 既有阶段自带的发射点（plan_selection →
CANDIDATE_WORKFLOWS 载荷、capability_resolution → SELECTED_WORKFLOW
附加记录、dispatch bind 拒绝 → TOOL_CALLS 附加记录），payload 经
``emit_chain`` 的既有 ``bound_meta`` 消毒。

本模块只负责三件事：

- **确定性 decision_id**：内容地址（canonical payload 的 sha256 前缀）。
  同输入同 id —— 录制、重放与重推导产生的同一决策可跨 run 对齐，是
  decision delta 的对齐键；任何证据面（含 policy_version）变化即新 id。
- **有界投影**：inputs/alternatives/reason_codes 逐字段钳制（字符串
  96B、每层条目数上限、深度 4）—— 决不把大数据/凭据带进链。
- **结构化 reason codes**：复用 qualification_v8 ``QualificationReason``
  的四元组形状（check/observed/expected/hint），无自由文本堆砌。

纯函数、零 I/O、绝不抛（构造失败退化为最小 dict）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional

DECISION_SCHEMA_VERSION = 1
#: 链记录里的决策标记键（ReplayTrace 提取层据此扫描）。
DECISION_MARKER_KEY = "decision"

#: 决策种类封闭词表（新增 = additive 演进）。
DECISION_KIND_PLAN_SELECTION = "plan_selection"
DECISION_KIND_CAPABILITY_RESOLUTION = "capability_resolution"
DECISION_KIND_CAPABILITY_DISPATCH_DENIAL = "capability_dispatch_denial"
#: F12（ADR-0214）：MapPlanIR → mutations 编译决策（compile receipt 溯源面）。
DECISION_KIND_PLAN_COMPILE = "plan_compile"

_DECISION_KINDS = frozenset((
    DECISION_KIND_PLAN_SELECTION,
    DECISION_KIND_CAPABILITY_RESOLUTION,
    DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
    DECISION_KIND_PLAN_COMPILE,
))

_MAX_ALTERNATIVES = 8
_MAX_REASON_CODES = 6
_MAX_INPUT_KEYS = 32
_MAX_EVIDENCE_REFS = 8
_STR_MAX = 96
_DEPTH_MAX = 4
#: inputs 投影字节预算（超限整体 digest-only 降级 —— 对账键 inputs_digest
#: 本就按完整投影计算，不丢）。
_INPUTS_BYTES_MAX = 8192


def _scrub(value: str) -> str:
    """值级秘密剥离（单点复用 replay.sanitize 的 scrub_secret_strings；
    replay 包不可用时退化为原串 —— 链层 bound_meta 的键级防线仍在）。"""
    try:
        from app.lib.harness.replay.sanitize import scrub_secret_strings

        return scrub_secret_strings(value)
    except Exception:  # noqa: BLE001 — 防线降级不阻断记录面
        return value


def _bound_str(value: Any, limit: int = _STR_MAX) -> str:
    return _scrub(str(value)[:limit]) if value is not None else ""


def _bound(value: Any, depth: int = 0) -> Any:
    """有界投影（决策面纪律：宁缺毋滥，超限截断不抛）。"""
    if depth >= _DEPTH_MAX:
        return _bound_str(value, 32)
    if isinstance(value, dict):
        return {
            # 键名不过秘密 scrub：凭据**名称**（如 ``api_key:upstream``）
            # 是 rederive 的资格事实，形似 key=value 会被值级正则误杀；
            # 真秘密键由链层精确名单 + 值级 scrub 兜底。
            str(k)[:48]: _bound(v, depth + 1)
            for k, v in list(value.items())[:_MAX_INPUT_KEYS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bound(v, depth + 1) for v in list(value)[:_MAX_ALTERNATIVES]]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _bound_str(value)


def canonical_decision_json(record: Dict[str, Any]) -> str:
    """决策载荷规范化（digest / id 派生统一入口；排序键 + 有限精度）。"""
    return json.dumps(_round_floats(record), sort_keys=True,
                      ensure_ascii=False, default=str)


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _round_floats(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(v) for v in value]
    return value


def _digest_of(payload: Any) -> str:
    return hashlib.sha256(
        canonical_decision_json(payload).encode("utf-8")).hexdigest()


def decision_id_for(record: Dict[str, Any]) -> str:
    """内容地址 id（不含 decision_id 本身的 canonical sha256 前缀）。"""
    body = {k: v for k, v in record.items() if k != "decision_id"}
    return "dec_" + _digest_of(body)[:12]


def reason_code(check: str, observed: str, expected: str,
                hint: str = "") -> Dict[str, str]:
    """结构化 reason code（与 QualificationReason 同形状）。"""
    out = {
        "check": _bound_str(check, 48),
        "observed": _bound_str(observed, 96),
        "expected": _bound_str(expected, 96),
    }
    if hint:
        out["hint"] = _bound_str(hint, 96)
    return out


def alternative_entry(
    alt_id: str,
    *,
    score: Optional[float] = None,
    status: str = "",
    rank: Optional[int] = None,
    reasons: Iterable[Any] = (),
    factors: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """alternatives 条目（有界；score 缺席 = 该决策面无量化分，诚实省略）。"""
    entry: Dict[str, Any] = {"id": _bound_str(alt_id, 96)}
    if score is not None:
        try:
            entry["score"] = round(float(score), 4)
        except (TypeError, ValueError):
            pass
    if status:
        entry["status"] = _bound_str(status, 32)
    if rank is not None:
        entry["rank"] = int(rank)
    if factors:
        entry["factors"] = {
            _bound_str(k, 48): round(float(v), 4)
            for k, v in list(factors.items())[:4]
            if isinstance(v, (int, float))
        }
    reason_list = [r for r in reasons if r]
    if reason_list:
        entry["reason_codes"] = [
            r if isinstance(r, dict) else reason_code("rejection", str(r), "")
            for r in reason_list[:2]
        ]
    return entry


def decision_record(
    kind: str,
    *,
    selected: str = "",
    alternatives: Iterable[Any] = (),
    reason_codes: Iterable[Any] = (),
    inputs: Optional[Dict[str, Any]] = None,
    evidence_refs: Iterable[str] = (),
    policy_version: str = "",
) -> Dict[str, Any]:
    """构造一条 DecisionRecord（确定性、有界、自含 id）。"""
    alt_list = [
        _bound(a, 1) if isinstance(a, dict) else alternative_entry(str(a))
        for a in list(alternatives or [])[:_MAX_ALTERNATIVES]
    ]
    rc_list = [
        _bound(r, 1) if isinstance(r, dict) else reason_code("reason", str(r), "")
        for r in list(reason_codes or [])[:_MAX_REASON_CODES]
    ]
    bounded_inputs = _bound(dict(inputs or {}), 0)
    # 对账键先按完整有界投影计算（降级不丢 digest 的可对账性）。
    inputs_digest = _digest_of(bounded_inputs)[:16]
    # 字节预算（review P2-3：逐维有界的乘积仍可巨大）—— 超预算整体降级
    # digest-only（digest 已按完整投影落定）。
    try:
        inputs_bytes = len(canonical_decision_json(bounded_inputs).encode("utf-8"))
    except (TypeError, ValueError):
        inputs_bytes = _INPUTS_BYTES_MAX + 1
    if inputs_bytes > _INPUTS_BYTES_MAX:
        bounded_inputs = {
            "_degraded": "inputs_over_budget",
            "bytes": inputs_bytes,
        }
    record: Dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "kind": kind if kind in _DECISION_KINDS else _bound_str(kind, 48),
        "inputs_digest": inputs_digest,
        "inputs": bounded_inputs,
        "selected": _bound_str(selected, 96),
        "alternatives": alt_list,
        "reason_codes": rc_list,
        "evidence_refs": [
            _bound_str(r, 96) for r in list(evidence_refs or [])[:_MAX_EVIDENCE_REFS]
        ],
    }
    if policy_version:
        record["policy_version"] = _bound_str(policy_version, 48)
    record["decision_id"] = decision_id_for(record)
    return record


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "DECISION_MARKER_KEY",
    "DECISION_KIND_PLAN_SELECTION",
    "DECISION_KIND_CAPABILITY_RESOLUTION",
    "DECISION_KIND_CAPABILITY_DISPATCH_DENIAL",
    "decision_record",
    "decision_id_for",
    "reason_code",
    "alternative_entry",
    "canonical_decision_json",
]
