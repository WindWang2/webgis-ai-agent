"""Trace Completeness Contract（ADR-0104 Wave 5）——trace 完整性认证。

复用既有 trace 词表，不建第二 tracing backend：
- ``GisTraceChain`` 的 18 规范阶段（app/lib/runtime/gis_trace.py，ADR-0103 §十）
  → task class 级"必备阶段"契约；
- geocompute 事件环（app/services/geocompute/tracing.py，ADR-0096 D7）
  → 事件序列契约（起点/终点/失败码/重试关系/取消后无完成）；
- 脱敏契约：敏感键 + 载荷字节上界（denylist 键 + 有界体积）。

产物：
- ``TraceCertificate``：一次认证（gaps 列表）；
- ``render_certification_md()``：派生认证表（任务类 × 必备阶段 × 运行时
  填充现状）。运行时填充现状 = 静态发现哪些 app 代码真的 record 这些阶段
  ——契约已定义但无人填充的阶段**如实标注 contract-only**，不伪造认证。

红线：tests/quality/test_trace_completeness.py（行为认证 + 表字节一致）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List

from app.lib.quality.discovery import repo_root

# ── GisTraceChain 阶段（按名字镜像，避免 import 期强耦合；值经测试对齐）──
_STAGE_NAMES = (
    "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY", "DATA_PROFILE",
    "CANDIDATE_WORKFLOWS", "SELECTED_WORKFLOW", "TOOL_SURFACE",
    "MODEL_ROUTING", "TOOL_CALLS", "ARGUMENTS", "TOOL_RESULTS",
    "ARTIFACT_CREATION", "MAP_MUTATIONS", "MAP_OBSERVATION",
    "VERIFICATION", "REPAIR", "FINAL_VERDICT", "USER_OUTPUT",
)

# ── task class → 必备阶段契约 ────────────────────────────────────────────
#: plan_only：只走规划（离线场景/评测回放的最小证据面）
PLAN_ONLY: FrozenSet[str] = frozenset({
    "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY", "SELECTED_WORKFLOW",
    "USER_OUTPUT",
})
#: tool_execution：工具执行类任务
TOOL_EXECUTION: FrozenSet[str] = frozenset({
    "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY", "TOOL_SURFACE",
    "TOOL_CALLS", "TOOL_RESULTS", "ARTIFACT_CREATION", "USER_OUTPUT",
})
#: map_product：制图产品闭环（到终裁）
MAP_PRODUCT: FrozenSet[str] = frozenset({
    "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY", "SELECTED_WORKFLOW",
    "TOOL_CALLS", "TOOL_RESULTS", "ARTIFACT_CREATION", "MAP_MUTATIONS",
    "MAP_OBSERVATION", "VERIFICATION", "FINAL_VERDICT", "USER_OUTPUT",
})
#: failure_path：失败路径（最小证据：意图 + 尝试 + 终裁 + 失败码）
FAILURE_PATH: FrozenSet[str] = frozenset({
    "USER_INTENT", "PARSED_INTENT", "TOOL_CALLS", "FINAL_VERDICT",
})

TRACE_CLASS_REQUIREMENTS: Dict[str, FrozenSet[str]] = {
    "plan_only": PLAN_ONLY,
    "tool_execution": TOOL_EXECUTION,
    "map_product": MAP_PRODUCT,
    "failure_path": FAILURE_PATH,
}

#: 失败路径必须携带的字段（FINAL_VERDICT 记录 payload 内）
FAILURE_REQUIRED_PAYLOAD_KEYS: FrozenSet[str] = frozenset({"failure_code"})

# ── geocompute 事件序列契约（emit() 事件名，ADR-0096 D7 实测词表）────────
GEOCOMPUTE_TERMINAL_EVENTS = frozenset({"run_finished"})
GEOCOMPUTE_FAILURE_EVENTS = frozenset({"node_failed", "node_attempt_failed"})
GEOCOMPUTE_CANCEL_EVENTS = frozenset({"node_cancelled"})
GEOCOMPUTE_SUCCESS_NODE_EVENTS = frozenset({"node_completed", "node_reused", "node_skipped"})

# ── 脱敏契约 ─────────────────────────────────────────────────────────────
_SENSITIVE_RE_CACHE = None


def _sensitive_key_re():
    """派生自 jobs/redaction 的 SENSITIVE_KEY_PARTS（R2 review MINOR-5：
    与 events.py 同一口径，不再自绘弱子集词表）。"""
    global _SENSITIVE_RE_CACHE
    if _SENSITIVE_RE_CACHE is None:
        try:
            from app.services.jobs.redaction import SENSITIVE_KEY_PARTS
            parts = sorted(SENSITIVE_KEY_PARTS)
        except Exception:  # noqa: BLE001 —— 循环导入兜底
            parts = ["secret", "token", "password", "api_key", "apikey",
                     "authorization", "cookie", "credential"]
        _SENSITIVE_RE_CACHE = re.compile(
            "(?i)(" + "|".join(re.escape(p) for p in parts) + ")")
    return _SENSITIVE_RE_CACHE
#: 单条 trace 记录序列化字节上界（有界元数据，不是载荷）
MAX_RECORD_BYTES = 8 * 1024
#: 单个字符串值上界
MAX_STRING_VALUE = 2048


@dataclass(frozen=True)
class TraceGap:
    """一条 trace 缺口。"""

    code: str
    severity: str  # BLOCKER | MAJOR | MINOR
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass
class TraceCertificate:
    task_class: str
    passed: bool
    gaps: List[TraceGap] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_class": self.task_class,
            "passed": self.passed,
            "gaps": [g.to_dict() for g in self.gaps],
        }


# ── GisTraceChain 认证 ───────────────────────────────────────────────────


def validate_chain(
    chain_dict: Dict[str, Any],
    task_class: str,
) -> List[TraceGap]:
    """认证一条 GisTraceChain.as_dict() 投影。

    chain_dict 形如 {"turn_id":..., "session_id":..., "stages":
    [{"stage": "TOOL_CALLS", "payload": {...}}, ...]}（as_dict 实际形态，
    测试里对齐真实投影）。
    """
    gaps: List[TraceGap] = []
    required = TRACE_CLASS_REQUIREMENTS.get(task_class)
    if required is None:
        return [TraceGap("UNKNOWN_TASK_CLASS", "BLOCKER",
                         f"unknown task class: {task_class}")]

    turn_id = chain_dict.get("turn_id")
    if not turn_id:
        gaps.append(TraceGap("MISSING_CORRELATION", "BLOCKER",
                             "trace chain has no turn_id (correlation)"))
    covered = {rec.get("stage") for rec in chain_dict.get("stages", [])}
    missing = sorted(required - covered)
    if missing:
        gaps.append(TraceGap("MISSING_STAGES", "BLOCKER",
                             f"required stages not recorded: {missing}"))

    # 失败路径：终裁必须带 failure_code；重试关系需要可追溯
    # （ChainRecord.as_dict 把 payload 拍平进记录本体，兼容嵌套形态。）
    if task_class == "failure_path" or "FINAL_VERDICT" in covered:
        verdict = next(
            (r for r in chain_dict.get("stages", [])
             if r.get("stage") == "FINAL_VERDICT"), None)
        if verdict is not None:
            payload = verdict.get("payload")
            if not isinstance(payload, dict):
                payload = {k: v for k, v in verdict.items()
                           if k not in ("stage", "stage_id", "ts")}
            missing_keys = sorted(FAILURE_REQUIRED_PAYLOAD_KEYS - set(payload))
            if missing_keys:
                gaps.append(TraceGap(
                    "FAILURE_CODE_MISSING", "BLOCKER",
                    f"FINAL_VERDICT record missing {missing_keys}"))
    # 阶段记录体量上界（有界元数据契约）
    for rec in chain_dict.get("stages", []):
        size = len(json.dumps(rec, ensure_ascii=False, default=str))
        if size > MAX_RECORD_BYTES:
            gaps.append(TraceGap(
                "RECORD_OVERSIZE", "MAJOR",
                f"stage record {rec.get('stage')} serialized {size}B "
                f"> {MAX_RECORD_BYTES}B（疑似携带载荷）"))
    gaps.extend(_redaction_gaps(chain_dict.get("stages", [])))
    return gaps


def certify_chain(chain_dict: Dict[str, Any], task_class: str) -> TraceCertificate:
    gaps = validate_chain(chain_dict, task_class)
    return TraceCertificate(
        task_class=task_class, passed=not gaps, gaps=gaps)


# ── geocompute 事件序列认证 ──────────────────────────────────────────────


def validate_geocompute_events(events: List[Dict[str, Any]]) -> List[TraceGap]:
    """认证一个 run 的事件序列（按 ts 升序语义给定时的时间序）。"""
    gaps: List[TraceGap] = []
    if not events:
        return [TraceGap("EMPTY_TRACE", "BLOCKER", "no events for run")]
    run_ids = {e.get("run_id") for e in events}
    if None in run_ids or "" in run_ids or len(run_ids) != 1:
        gaps.append(TraceGap(
            "RUN_CORRELATION_BROKEN", "BLOCKER",
            f"events carry inconsistent run_id: {sorted(map(str, run_ids))}"))
    names = [e.get("event") for e in events]
    if names[0] != "run_started":
        gaps.append(TraceGap(
            "NO_RUN_START", "MAJOR", f"first event is {names[0]!r}, not run_started"))
    terminals = [n for n in names if n in GEOCOMPUTE_TERMINAL_EVENTS]
    if len(terminals) != 1 or names[-1] != "run_finished":
        gaps.append(TraceGap(
            "TERMINAL_EVENT_MISSING", "BLOCKER",
            f"expected exactly one terminal run_finished, got {terminals}"))
    for e in events:
        if e.get("event") in GEOCOMPUTE_FAILURE_EVENTS and not e.get("error_code"):
            gaps.append(TraceGap(
                "FAILURE_CODE_MISSING", "BLOCKER",
                f"{e.get('event')} on node {e.get('node_id')} without error_code"))
    # 取消后同一节点不得再有成功事件（取消必须生效）
    cancelled: set = set()
    for e in events:
        node = e.get("node_id")
        if e.get("event") in GEOCOMPUTE_CANCEL_EVENTS and node:
            cancelled.add(node)
        elif e.get("event") in GEOCOMPUTE_SUCCESS_NODE_EVENTS and node in cancelled:
            gaps.append(TraceGap(
                "COMPLETED_AFTER_CANCEL", "BLOCKER",
                f"node {node} completed after cancellation"))
    # 重试关系：attempt 失败 ≥2 次说明有重试，attempts 字段应如实计数
    for e in events:
        if e.get("event") == "node_attempt_failed":
            attempts = e.get("attempts")
            if attempts is not None and int(attempts) < 1:
                gaps.append(TraceGap(
                    "RETRY_RELATION_INCONSISTENT", "MAJOR",
                    f"attempt_failed with attempts={attempts}"))
    gaps.extend(_redaction_gaps(events))
    return gaps


def certify_geocompute_run(events: List[Dict[str, Any]]) -> TraceCertificate:
    gaps = validate_geocompute_events(events)
    return TraceCertificate(
        task_class="geocompute_run", passed=not gaps, gaps=gaps)


# ── 脱敏 ─────────────────────────────────────────────────────────────────


def _redaction_gaps(records: List[Dict[str, Any]]) -> List[TraceGap]:
    """对拍平或嵌套 payload 的记录做敏感键/超长值扫描。"""
    gaps: List[TraceGap] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        payload = rec.get("payload")
        items = (payload if isinstance(payload, dict) else rec).items()
        for key, value in items:
            if _sensitive_key_re().search(str(key)):
                gaps.append(TraceGap(
                    "SENSITIVE_KEY", "BLOCKER",
                    f"sensitive-looking key {key!r} must never enter trace"))
            elif isinstance(value, str) and len(value) > MAX_STRING_VALUE:
                gaps.append(TraceGap(
                    "VALUE_OVERSIZE", "MAJOR",
                    f"string value under {key!r} is {len(value)} chars "
                    f"> {MAX_STRING_VALUE}"))
    return gaps


# ── 认证表（派生 Markdown）───────────────────────────────────────────────


def _runtime_population_sites() -> Dict[str, List[str]]:
    """静态发现：app/ 里哪些文件真实 record 各阶段 / emit geocompute 事件。"""
    root = repo_root()
    sites: Dict[str, List[str]] = {}
    app_dir = root / "app"
    for py in sorted(app_dir.rglob("*.py")):
        rel = py.relative_to(root).as_posix()
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for stage in _STAGE_NAMES:
            if f"Stage.{stage}" in text:
                sites.setdefault(stage, []).append(rel)
    return {k: sorted(v) for k, v in sites.items()}


def render_certification_md() -> str:
    sites = _runtime_population_sites()
    lines: List[str] = []
    lines.append("# Trace Completeness Certification（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_trace_certification.py` 派生，请勿手改。")
    lines.append("> 契约：`app/lib/quality/trace_contract.py`；行为红线：")
    lines.append("> `tests/quality/test_trace_completeness.py`。")
    lines.append("")
    lines.append("## 任务类 → 必备阶段")
    lines.append("")
    lines.append("| task class | 必备阶段 | 阶段数 |")
    lines.append("|---|---|---|")
    for tc in sorted(TRACE_CLASS_REQUIREMENTS):
        stages = sorted(TRACE_CLASS_REQUIREMENTS[tc])
        lines.append(f"| {tc} | {', '.join(stages)} | {len(stages)} |")
    lines.append("")
    lines.append("## 18 规范阶段 × 运行时填充现状")
    lines.append("")
    lines.append("| stage | 运行时填充位置 | 状态 |")
    lines.append("|---|---|---|")
    for stage in _STAGE_NAMES:
        where = sites.get(stage, [])
        status = "runtime-populated" if where else "contract-only（缺口：无生产代码填充）"
        where_text = ", ".join(where) if where else "—"
        lines.append(f"| {stage} | {where_text} | {status} |")
    lines.append("")
    lines.append(
        "> contract-only 阶段是已声明的 trace 缺口：契约先行，填充随各主线"
        "演进；认证不得为未填充阶段伪造 passed。")
    lines.append("")
    payload = json.dumps(
        {"requirements": {k: sorted(v) for k, v in TRACE_CLASS_REQUIREMENTS.items()},
         "sites": sites},
        ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    lines.append(f"- 内容指纹：`{fingerprint[:16]}…`")
    lines.append("")
    return "\n".join(lines)
