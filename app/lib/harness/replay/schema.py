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
from typing import Any, Dict, List, Optional, Tuple

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
#: D5 整体预算（超限降级 digest-only 形态并置 truncated，绝不无界）。
_TRACE_BUDGET_BYTES = 512 * 1024


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
        unknown = getattr(self, "_unknown_fields", None)
        if unknown:
            out.update(unknown)
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


#: 生产发射键名（agent_pi_bridge / tool_dispatch_service / planner）与本线
#: 测试键名的双兼容映射 —— 真实录制与语料 fixtures 必须走同一提取层。
_CALL_ID_KEYS = ("call_id", "tool_call_id")
_TOOL_NAME_KEYS = ("tool", "tool_name", "name")


def _call_id_of(rec: Dict[str, Any]) -> str:
    for key in _CALL_ID_KEYS:
        value = rec.get(key)
        if isinstance(value, str) and value:
            return bounded_str(value, 128)
    return ""


def _tool_name_of(rec: Dict[str, Any]) -> str:
    for key in _TOOL_NAME_KEYS:
        value = rec.get(key)
        if isinstance(value, str) and value:
            return bounded_str(value, 128)
    return ""


def _tool_calls_from_chain(chain_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """链内 TOOL_CALLS/ARGUMENTS/TOOL_RESULTS → 消毒后的调用行为块。

    生产发射形态（bridge）：TOOL_CALLS 带 ``call_id``/``tool``；ARGUMENTS
    的 ``args`` 是有界字符串化参数（dispatch 面是 ``arg_keys`` 键名清单）；
    TOOL_RESULTS 只有 ``tool``/``status``/``latency_ms``（无 id）——
    无 id 的结果按 (tool 名, 出现序) 回填最早未终态调用。
    """
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    arg_payloads: List[Tuple[str, Dict[str, Any]]] = []
    for rec in _stage_records(chain_dict, "TOOL_CALLS"):
        call_id = _call_id_of(rec)
        name = _tool_name_of(rec)
        if not call_id and not name:
            continue
        # dispatch 面的 TOOL_CALLS 无 id → 稳定序号 id；幂等重放防重复。
        call_id = call_id or f"call-{len(order) + 1}"
        if call_id in calls:
            continue
        order.append(call_id)
        coerced = _coerce_arguments(rec.get("arguments"))
        if coerced is not None:
            args, arg_bytes, truncated = sanitize_arguments(coerced)
        elif rec.get("arguments") is not None:
            # 不可还原（bound_meta 折叠形态）→ digest-only 诚实降级。
            args, arg_bytes, truncated = (
                {"_digest_only": sanitize_tool_result_ref(rec.get("arguments"))},
                -1,
                True,
            )
        else:
            args, arg_bytes, truncated = {}, -1, False
        calls[call_id] = {
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": args,
            "arg_bytes": arg_bytes,
            "args_truncated": truncated,
            "status": "issued",
        }
    for rec in _stage_records(chain_dict, "ARGUMENTS"):
        name = _tool_name_of(rec)
        coerced = _coerce_arguments(rec.get("arguments") or rec.get("args"))
        arg_keys = rec.get("arg_keys")
        if isinstance(arg_keys, str):
            # bound_meta 把键名清单 repr 化 → literal_eval 安全还原。
            arg_keys = _coerce_arguments(arg_keys)
        if coerced is None and isinstance(arg_keys, list):
            # dispatch 形态：参数键名清单（形状证据，无值）。
            arg_payloads.append((name, {"_arg_keys": arg_keys}))
        elif coerced is not None:
            arg_payloads.append((name, coerced))
        elif isinstance(rec.get("args"), str):
            arg_payloads.append((name, {"_digest_only":
                                        sanitize_tool_result_ref(rec["args"])}))
    for name, coerced in arg_payloads:
        for call_id in order:
            if calls[call_id]["tool_name"] == name and \
                    not calls[call_id]["arguments"]:
                args, arg_bytes, truncated = sanitize_arguments(coerced)
                calls[call_id]["arguments"] = args
                calls[call_id]["arg_bytes"] = arg_bytes
                calls[call_id]["args_truncated"] = truncated
                break
    result_cursor: Dict[str, int] = {}
    for rec in _stage_records(chain_dict, "TOOL_RESULTS"):
        name = _tool_name_of(rec)
        candidates = [cid for cid in order
                      if calls[cid]["tool_name"] == name
                      and calls[cid]["status"] == "issued"]
        offset = result_cursor.get(name, 0)
        if offset >= len(candidates):
            continue
        call_id = candidates[offset]
        result_cursor[name] = offset + 1
        calls[call_id]["status"] = bounded_str(rec.get("status") or "ok", 32)
        result_raw = rec.get("result")
        result_coerced = _coerce_arguments(result_raw)
        calls[call_id]["result_ref"] = sanitize_tool_result_ref(
            result_coerced if result_coerced is not None else result_raw
        )
        latency = rec.get("latency_ms", rec.get("duration_ms"))
        if latency is not None:
            try:
                calls[call_id]["duration_ms"] = round(float(latency), 1)
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
    selected_name = ""
    for payload in (_first_payload(chain_dict, "SELECTED_WORKFLOW"),
                    _first_payload(chain_dict, "CANDIDATE_WORKFLOWS")):
        # "recipe_id"/"selected" 是生产规划面的键名。
        for key in ("recipe_id", "selected", "workflow", "workflow_id",
                    "name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                selected_name = bounded_str(value, 128)
                break
        if selected_name:
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
    # D5 整体预算：超 512KB → 丢链载荷（保留覆盖度/阶段名），truncated=True。
    payload = trace.to_dict()
    if len(canonical_json(payload).encode("utf-8")) > _TRACE_BUDGET_BYTES:
        trace.chain = {
            "total_records": chain_dict.get("total_records"),
            "completeness": chain_dict.get("completeness"),
            "covered_stages": chain_dict.get("covered_stages"),
        }
        trace.truncated = True
        trace.recording = {**(trace.recording or {}), "budget_degraded": True}
        trace.behavior_digest = behavior_digest(trace.to_dict())
    return trace


def _user_input_from_chain(chain_dict: Dict[str, Any]) -> str:
    payload = _first_payload(chain_dict, "USER_INTENT")
    # "query" 是生产规划面（planner emit_chain）的键名。
    for key in ("prompt", "query", "text", "user_input", "message", "intent"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _goal_from(summary: Dict[str, Any], chain_dict: Dict[str, Any]) -> str:
    for payload in (_first_payload(chain_dict, "PARSED_INTENT"),
                    _first_payload(chain_dict, "TASK_ONTOLOGY")):
        # "task" 是生产规划面的键名（PARSED_INTENT/TASK_ONTOLOGY）。
        for key in ("goal", "normalized_goal", "task", "task_type",
                    "intent"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""
