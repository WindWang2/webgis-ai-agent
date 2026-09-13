"""ReplayTrace v1 —— 18 阶段证据链的打包层（B1，ADR-0183 决策一）。

**不是第二套 trace**：字段全部来自既有生产缝的当轮事实 —

- 骨架：``GisTraceChain.as_dict()``（Stage 1-18，bound_meta 消毒后）；
- turn 包络：``TurnEvidence.to_summary()``（outcome/timing/work/llm_usage）；
- verdict：settle 时刻的 finalization SSE 载荷（``finalization_sse_payload``，
  本身有界）+ 链内 FINAL_VERDICT 阶段记录；
- 计划/图摘要：链内 CANDIDATE_WORKFLOWS/SELECTED_WORKFLOW 阶段的行为摘要。

versioning：``schema_version=1``，**additive-only** 演进（未知字段在
from_dict 中原样保留）；为并行线预留 versioned optional 字段
（situation_revision / governor / skill_id / plan steps 引用）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.harness.replay.determinism import behavior_digest, canonical_json, sha256_of
from app.lib.harness.replay.sanitize import (
    bounded_str,
    sanitize_arguments,
    sanitize_tool_result_ref,
    sanitize_value,
)

REPLAY_TRACE_SCHEMA_VERSION = 1

_TEXT_MAX = 2000
_FINAL_TEXT_MAX = 2000


@dataclass
class ReplayTrace:
    """一次 turn 的可离线重放打包单元（自包含、有界、消毒后）。"""

    schema_version: int = REPLAY_TRACE_SCHEMA_VERSION
    session_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    run_id: str = ""
    created_at_epoch: Optional[float] = None

    # ── B1 契约字段 ────────────────────────────────────────────────────────
    user_input: str = ""                 # 有界原文（nondeterministic_text 类）
    normalized_goal: str = ""            # PARSED_INTENT / plan 的意图摘要
    situation_revision: Optional[Dict[str, Any]] = None   # 预留（#1275）
    plan_digest: str = ""                # SELECTED_WORKFLOW 行为摘要
    selected_workflow: str = ""          # 选定工作流名（providers 面入口）
    skill_id: Optional[str] = None       # 预留（#1278）
    governor: Optional[Dict[str, Any]] = None  # 预留（#1279）
    chain: Dict[str, Any] = field(default_factory=dict)   # Stage 1-18 全量
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # 消毒后
    mutations: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    verdict: Dict[str, Any] = field(default_factory=dict)
    outcome: Dict[str, Any] = field(default_factory=dict)   # TurnEvidence.outcome
    timing_ms: Dict[str, Any] = field(default_factory=dict)
    work: Dict[str, Any] = field(default_factory=dict)
    llm_usage: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    final_text: str = ""                 # 有界 USER_OUTPUT（nondeterministic_text 类）
    env: Dict[str, Any] = field(default_factory=dict)
    recording: Dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    behavior_digest: str = ""

    # ── 序列化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key in (
            "schema_version", "session_id", "turn_id", "request_id", "run_id",
            "created_at_epoch", "user_input", "normalized_goal",
            "situation_revision", "plan_digest", "selected_workflow", "skill_id",
            "governor", "chain", "tool_calls", "mutations", "artifacts",
            "verdict", "outcome", "timing_ms", "work", "llm_usage", "warnings",
            "final_text", "env", "recording", "truncated",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        out["behavior_digest"] = self.behavior_digest or behavior_digest(out)
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplayTrace":
        known = {f for f in cls.__dataclass_fields__ if f != "behavior_digest"}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        trace = cls(**kwargs)
        trace.behavior_digest = str(data.get("behavior_digest") or "")
        # additive-only：未知字段原样保留（版本演进纪律，测试钉死）。
        unknown = {k: v for k, v in data.items() if k not in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        if unknown:
            object.__setattr__(trace, "_unknown_fields", unknown)
        return trace

    def canonical(self) -> str:
        return canonical_json(self.to_dict())


# ── 构建（recorder / replayer / tests 共用的唯一入口）────────────────────────


def _stage_records(chain_dict: Dict[str, Any], stage_name: str) -> List[Dict[str, Any]]:
    return [
        rec for rec in (chain_dict.get("stages") or [])
        if isinstance(rec, dict) and rec.get("stage") == stage_name
    ]


def _first_payload(chain_dict: Dict[str, Any], stage_name: str) -> Dict[str, Any]:
    for rec in _stage_records(chain_dict, stage_name):
        payload = {k: v for k, v in rec.items()
                   if k not in ("stage", "stage_id", "ts")}
        if payload:
            return payload
    return {}


def _coerce_arguments(raw: Any) -> Any:
    """链内参数还原：bound_meta 把嵌套 dict repr 化为字符串 —— 先安全还原
    （``ast.literal_eval``，绝不 eval）再消毒；不可还原 → digest-only。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                import ast

                parsed = ast.literal_eval(text[:20000])
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                pass
    return None  # 不可还原 → 由调用方降级 digest-only


def _tool_calls_from_chain(chain_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """链内 TOOL_CALLS/ARGUMENTS/TOOL_RESULTS → 消毒后的调用行为块。

    链记录以 tool_call_id 关联；无结果的调用（超时/取消）保留 status=issued。
    """
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in _stage_records(chain_dict, "TOOL_CALLS"):
        call_id = bounded_str(rec.get("tool_call_id") or "", 128)
        name = bounded_str(rec.get("tool_name") or rec.get("name") or "", 128)
        if not call_id:
            continue
        order.append(call_id)
        coerced = _coerce_arguments(rec.get("arguments"))
        if coerced is not None:
            args, arg_bytes, truncated = sanitize_arguments(coerced)
        else:
            args, arg_bytes, truncated = (
                {"_digest_only": sanitize_tool_result_ref(rec.get("arguments"))},
                -1,
                True,
            )
        calls[call_id] = {
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": args,
            "arg_bytes": arg_bytes,
            "args_truncated": truncated,
            "status": "issued",
        }
    for rec in _stage_records(chain_dict, "ARGUMENTS"):
        call_id = bounded_str(rec.get("tool_call_id") or "", 128)
        if call_id and call_id in calls:
            coerced = _coerce_arguments(rec.get("arguments"))
            if coerced is not None:
                args, arg_bytes, truncated = sanitize_arguments(coerced)
                calls[call_id]["arguments"] = args
                calls[call_id]["arg_bytes"] = arg_bytes
                calls[call_id]["args_truncated"] = (
                    calls[call_id].get("args_truncated") or truncated
                )
    for rec in _stage_records(chain_dict, "TOOL_RESULTS"):
        call_id = bounded_str(rec.get("tool_call_id") or "", 128)
        if not call_id or call_id not in calls:
            continue
        calls[call_id]["status"] = bounded_str(rec.get("status") or "ok", 32)
        result_raw = rec.get("result")
        result_coerced = _coerce_arguments(result_raw)
        calls[call_id]["result_ref"] = sanitize_tool_result_ref(
            result_coerced if result_coerced is not None else result_raw
        )
        if rec.get("duration_ms") is not None:
            try:
                calls[call_id]["duration_ms"] = round(float(rec["duration_ms"]), 1)
            except (TypeError, ValueError):
                pass
        if rec.get("is_error") is not None:
            calls[call_id]["is_error"] = bool(rec.get("is_error"))
        if rec.get("error_msg"):
            calls[call_id]["error_msg"] = bounded_str(rec.get("error_msg"), 512)
    return [calls[cid] for cid in order if cid in calls]


def build_trace(
    *,
    session_id: str,
    turn_id: str,
    chain_dict: Dict[str, Any],
    turn_summary: Optional[Dict[str, Any]] = None,
    map_product: Optional[Dict[str, Any]] = None,
    final_text: str = "",
    recording: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, Any]] = None,
    situation_revision: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    run_id: str = "",
) -> ReplayTrace:
    """从当轮生产事实构建 ReplayTrace（纯函数；输入必须是已消毒/有界面）。"""
    summary = turn_summary if isinstance(turn_summary, dict) else {}
    correlation = summary.get("correlation") if isinstance(summary.get("correlation"), dict) else {}
    chain_dict = dict(chain_dict) if isinstance(chain_dict, dict) else {}
    # 存入 trace 的链统一剥 ts（墙钟秒）：规范化时间戳纪律（D5）——相对
    # 计时在 timing_ms，digest 与存储都不要墙钟。
    chain_dict["stages"] = [
        {k: v for k, v in rec.items() if k != "ts"} if isinstance(rec, dict) else rec
        for rec in (chain_dict.get("stages") or [])
    ]

    # 计划/图摘要：候选 ∪ 选定工作流载荷的行为摘要。
    plan_payload = {
        "candidates": sanitize_value(_first_payload(chain_dict, "CANDIDATE_WORKFLOWS"), str_limit=256),
        "selected": sanitize_value(_first_payload(chain_dict, "SELECTED_WORKFLOW"), str_limit=256),
        "parsed_intent": sanitize_value(_first_payload(chain_dict, "PARSED_INTENT"), str_limit=256),
    }
    selected_payload = _first_payload(chain_dict, "SELECTED_WORKFLOW")
    selected_name = ""
    for key in ("workflow", "workflow_id", "name", "selected"):
        value = selected_payload.get(key)
        if isinstance(value, str) and value:
            selected_name = bounded_str(value, 128)
            break

    # 变异块：MAP_MUTATIONS 阶段 + finalization 载荷里的 revision。
    # 记录先剥 ts（墙钟秒，digest 禁入），再消毒。
    mutation_records = [
        {k: v for k, v in rec.items() if k != "ts"}
        for rec in _stage_records(chain_dict, "MAP_MUTATIONS")
    ]
    mutations = {
        "count": len(mutation_records),
        "records": sanitize_value(mutation_records, str_limit=256),
        "mutation_revision": (
            map_product.get("mutation_revision")
            if isinstance(map_product, dict) else None
        ),
    }
    artifacts = sanitize_value(_first_payload(chain_dict, "ARTIFACT_CREATION"), str_limit=256)
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    artifacts["count"] = int((summary.get("work") or {}).get("artifacts") or 0)

    verdict: Dict[str, Any] = {}
    if isinstance(map_product, dict):
        verdict["map_product"] = sanitize_value(map_product, str_limit=300)
    final_verdict_records = _stage_records(chain_dict, "FINAL_VERDICT")
    if final_verdict_records:
        verdict["final_verdict"] = sanitize_value(
            final_verdict_records[-1], str_limit=300)

    trace = ReplayTrace(
        session_id=bounded_str(session_id, 255),
        turn_id=bounded_str(turn_id, 128),
        request_id=bounded_str(request_id or str(correlation.get("request_id") or ""), 128),
        run_id=bounded_str(run_id or str(correlation.get("run_id") or ""), 128),
        created_at_epoch=time.time(),
        user_input=bounded_str(_user_input_from_chain(chain_dict), _TEXT_MAX),
        normalized_goal=bounded_str(str(_goal_from(summary, chain_dict)), 512),
        situation_revision=situation_revision,
        plan_digest=sha256_of(plan_payload),
        selected_workflow=selected_name,
        chain=sanitize_value(chain_dict, str_limit=400),
        tool_calls=_tool_calls_from_chain(chain_dict),
        mutations=mutations,
        artifacts=artifacts,
        verdict=verdict,
        outcome=dict(summary.get("outcome") or {}),
        timing_ms=dict(summary.get("timing_ms") or {}),
        work=dict(summary.get("work") or {}),
        llm_usage=dict(summary.get("llm_usage") or {}),
        warnings=list(summary.get("warnings") or [])[:32],
        final_text=bounded_str(final_text or "", _FINAL_TEXT_MAX),
        env=env or {},
        recording=recording or {"source": "settle", "schema": REPLAY_TRACE_SCHEMA_VERSION},
    )
    trace.behavior_digest = behavior_digest(trace.to_dict())
    return trace


def _user_input_from_chain(chain_dict: Dict[str, Any]) -> str:
    payload = _first_payload(chain_dict, "USER_INTENT")
    for key in ("prompt", "text", "user_input", "message", "intent"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _goal_from(summary: Dict[str, Any], chain_dict: Dict[str, Any]) -> str:
    for payload in (_first_payload(chain_dict, "PARSED_INTENT"),
                    _first_payload(chain_dict, "TASK_ONTOLOGY")):
        for key in ("goal", "normalized_goal", "task_type", "intent"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""
