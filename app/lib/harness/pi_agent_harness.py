"""PiAgentHarness V2 — evidence-driven evaluation for the Pi Agent Bridge.

V2 闭环契约（HARNESS-V2）：
- MapSpecValidity 来自分层真实证据（mutation accepted → semantic valid →
  compile valid → runtime valid），而非"工具没报错"。缺失证据 = NOT_EVALUATED，
  绝不是 success。
- CursorResolutionRate 来自真实 SessionStore 解析（ref 存在 + 归属正确 session
  + 类型匹配），而非 ref 字符串前缀检查。跨 session / 不存在的 ref 不计为 resolved。
- 每条证据独立携带 run/session/turn/tool_call correlation，并发 session 不互染。
- 兼容：旧浮点 metric 接口（compute_*、evaluate_all、get_telemetry_summary）保留，
  但语义改为"真实证据"；无证据时返回 0.0（诚实），而非 100.0（假成功）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.lib.harness.evidence import (
    EvaluationRun,
    MapSpecValidityEvidence,
    MapSpecValidityTier,
    RefResolution,
    RefResolutionStatus,
    ToolCallEvidence,
)
from app.lib.harness.tool_call_event import ToolCallEvent

logger = logging.getLogger(__name__)

REF_CURSOR_PATTERN = re.compile(r"ref:(?:geojson|raster|table):[a-zA-Z0-9_-]+")

MAPSPEC_MUTATION_TOOLS = {
    "webgis_project_init",
    "webgis_view_set",
    "webgis_source_profile",
    "webgis_layer_upsert",
    "webgis_layer_remove",
    "webgis_layout_set",
}

# Type injected by callers that can do real (async) SessionStore-backed resolution.
RefResolver = Callable[[str, str], Awaitable[RefResolution]]
# Sync pure-Python mapspec structural validator (app.services.mapspec.coordinator.validate).
MapSpecValidator = Callable[[Dict[str, Any]], Dict[str, Any]]


class PiAgentHarness:
    """Evidence-driven evaluation harness for PiAgentBridge execution sessions.

    The legacy float-metric surface (compute_*, evaluate_all, get_telemetry_summary)
    is preserved for existing consumers, but every metric is now derived from real
    evidence. Where evidence is missing the metric degrades to 0.0, never 100.0.

    Call ``evaluate_with_evidence`` to perform real (async) ref resolution and
    build the structured ToolCallEvidence trail that powers the closed loop.
    """

    MAX_EVENTS: int = 1000

    def __init__(
        self,
        session_id: str = "",
        *,
        ref_resolver: Optional[RefResolver] = None,
        mapspec_validator: Optional[MapSpecValidator] = None,
    ):
        self.session_id = session_id
        self.ref_resolver = ref_resolver
        self.mapspec_validator = mapspec_validator

        # Raw event buffers (legacy-compatible shape), FIFO-capped.
        self.tool_calls: List[Dict[str, Any]] = []
        self.tool_results: List[Dict[str, Any]] = []
        self.sse_events: List[Dict[str, Any]] = []
        self.mapspec_mutations: List[Dict[str, Any]] = []
        self.ref_cursors: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        self.recovered_exceptions_count: int = 0
        self._in_error_state: bool = False

        # V2 correlation: every recorded event is stamped with the active
        # run/turn so concurrent sessions cannot pool into one accumulator.
        self._active_run_id: str = session_id or "default-run"
        self._active_turn_id: str = ""
        # Cached real resolutions from the last evaluate_with_evidence() pass;
        # lets the sync compute_* surface reflect real data instead of faking.
        self._resolved_refs: Dict[str, RefResolution] = {}
        self._validity_cache: Dict[str, MapSpecValidityEvidence] = {}

    # ── FIFO cap + correlation stamping ──────────────────────────────────

    def _append_capped(self, lst: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
        lst.append(item)
        if len(lst) > self.MAX_EVENTS:
            del lst[: len(lst) - self.MAX_EVENTS]

    def set_correlation(self, run_id: str = "", turn_id: str = "") -> None:
        """Stamp subsequent records with run/turn correlation ids."""
        if run_id:
            self._active_run_id = run_id
        if turn_id:
            self._active_turn_id = turn_id

    def reset(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._active_run_id = session_id or "default-run"
        self._active_turn_id = ""
        self.tool_calls.clear()
        self.tool_results.clear()
        self.sse_events.clear()
        self.mapspec_mutations.clear()
        self.ref_cursors.clear()
        self.exceptions.clear()
        self._resolved_refs.clear()
        self._validity_cache.clear()
        self.recovered_exceptions_count = 0
        self._in_error_state = False

    # ── Raw event recording (sync, legacy-compatible) ────────────────────

    def record_tool_call(
        self, tool_call_id: str, name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        call_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments or {},
            "run_id": self._active_run_id,
            "turn_id": self._active_turn_id,
            "session_id": self.session_id,
        }
        self._append_capped(self.tool_calls, call_entry)
        self._scan_and_record_ref_cursors(tool_call_id, arguments)

        if name in MAPSPEC_MUTATION_TOOLS:
            self._append_capped(self.mapspec_mutations, {
                "tool_call_id": tool_call_id,
                "tool_name": name,
                "arguments": arguments,
                "is_valid": False,
                "run_id": self._active_run_id,
                "turn_id": self._active_turn_id,
                "session_id": self.session_id,
            })
        return call_entry

    def record_tool_result(
        self,
        tool_call_id: str,
        name: str,
        result: Dict[str, Any],
        is_error: bool = False,
        error_msg: Optional[str] = None,
    ) -> Dict[str, Any]:
        result_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "result": result or {},
            "is_error": is_error,
            "error_msg": error_msg or "",
        }
        self._append_capped(self.tool_results, result_entry)

        if is_error:
            self._append_capped(self.exceptions, {
                "tool_call_id": tool_call_id,
                "name": name,
                "error_msg": error_msg or "",
                "run_id": self._active_run_id,
            })
            self._in_error_state = True
        else:
            if self._in_error_state:
                self.recovered_exceptions_count += 1
                self._in_error_state = False

        if name in MAPSPEC_MUTATION_TOOLS:
            # V2: MapSpec validity derives from REAL evidence — the tool result
            # of a mutation carries ``is_compiled`` (pure-Python validate() outcome
            # from MapSpecLifecycleEngine) and ``success``. "Didn't error" alone
            # is mutation_accepted, NOT semantic validity.
            for mutation in reversed(self.mapspec_mutations):
                if mutation["tool_call_id"] == tool_call_id:
                    mutation_accepted = (
                        not is_error and result.get("success", True) is not False
                    )
                    semantic_valid = (
                        mutation_accepted
                        and result.get("is_compiled") is True
                    )
                    mutation["is_valid"] = semantic_valid  # SEMANTIC_VALID tier
                    mutation["mutation_accepted"] = mutation_accepted
                    mutation["semantic_errors"] = result.get("warnings", [])
                    break
        return result_entry

    def record_sse_event(self, event: Dict[str, Any]) -> None:
        self._append_capped(self.sse_events, event)

    def record_event(self, event: ToolCallEvent) -> None:
        # Honour any session_id carried on the event for correlation.
        if getattr(event, "session_id", None):
            self.session_id = event.session_id  # type: ignore[assignment]
        self.record_tool_call(
            tool_call_id=event.tool_call_id,
            name=event.tool_name,
            arguments=event.arguments,
        )
        self.record_tool_result(
            tool_call_id=event.tool_call_id,
            name=event.tool_name,
            result=event.result,
            is_error=event.is_error,
            error_msg=event.error_msg,
        )

    # ── Ref cursor scanning ──────────────────────────────────────────────

    def _scan_and_record_ref_cursors(self, tool_call_id: str, args: Dict[str, Any]) -> None:
        args_str = str(args)
        found_refs = set(REF_CURSOR_PATTERN.findall(args_str))
        for ref in found_refs:
            self._append_capped(self.ref_cursors, {
                "tool_call_id": tool_call_id,
                "ref_cursor": ref,
                # Pre-resolution: syntactically valid ONLY. Real resolution happens
                # in evaluate_with_evidence against the SessionStore. We never claim
                # "resolved" from a string prefix check alone.
                "is_resolved": False,
                "status": RefResolutionStatus.SYNTACTICALLY_VALID.value,
                "run_id": self._active_run_id,
                "session_id": self.session_id,
            })

    # ── V2: real async evaluation against the SessionStore / validator ──

    async def evaluate_with_evidence(
        self,
        expected_tools: Optional[List[str]] = None,
        ideal_step_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform REAL ref resolution + structured evidence collection.

        Returns a structured evaluation with the validity ladder + ref statuses.
        Also caches resolutions so the legacy sync compute_* reflect real data.
        """
        expected_tools = expected_tools or []
        ideal_step_count = 0 if ideal_step_count is None else ideal_step_count

        run = EvaluationRun(
            run_id=self._active_run_id,
            session_id=self.session_id,
            expected_tools=list(expected_tools),
            ideal_step_count=ideal_step_count,
        )

        # 1. Resolve every recorded ref cursor against the real SessionStore.
        if self.ref_resolver is not None:
            for rc in self.ref_cursors:
                ref = rc["ref_cursor"]
                try:
                    resolution = await self.ref_resolver(self.session_id, ref)
                except Exception as e:
                    resolution = RefResolution(
                        ref=ref,
                        session_id=self.session_id,
                        status=RefResolutionStatus.NOT_FOUND,
                        detail=f"resolver error: {e}",
                    )
                self._resolved_refs[ref] = resolution
                rc["is_resolved"] = resolution.is_resolved
                rc["status"] = resolution.status.value
        # If no resolver wired: refs remain SYNTACTICALLY_VALID (not resolved) —
        # the metrics below will honestly reflect "not verified".

        # 2. Build per-tool-call evidence with correlation + validity ladder.
        results_by_id = {r["tool_call_id"]: r for r in self.tool_results}
        mutation_results = {
            m["tool_call_id"]: m for m in self.mapspec_mutations
        }

        for tc in self.tool_calls:
            tcid = tc["tool_call_id"]
            res = results_by_id.get(tcid, {})
            refs_for_call = [
                self._resolved_refs.get(rc["ref_cursor"])
                or RefResolution(
                    ref=rc["ref_cursor"],
                    session_id=self.session_id,
                    status=RefResolutionStatus.SYNTACTICALLY_VALID,
                )
                for rc in self.ref_cursors
                if rc["tool_call_id"] == tcid
            ]

            validity: Optional[MapSpecValidityEvidence] = None
            if tc["name"] in MAPSPEC_MUTATION_TOOLS:
                mut = mutation_results.get(tcid, {})
                validity = self._validity_for_mutation(tcid, mut, res)
                self._validity_cache[tcid] = validity

            ev = ToolCallEvidence(
                run_id=tc.get("run_id", self._active_run_id),
                session_id=tc.get("session_id", self.session_id),
                turn_id=tc.get("turn_id", ""),
                tool_call_id=tcid,
                tool_name=tc["name"],
                duration_ms=int(res.get("duration_ms", 0)) if res else 0,
                is_error=res.get("is_error", False) if res else False,
                error_msg=res.get("error_msg", "") if res else "",
                ref_resolutions=refs_for_call,
                mapspec_validity=validity,
            )
            run.add(ev)

        # 3. Structured + float metrics (both honest).
        float_metrics = self.evaluate_all(expected_tools, ideal_step_count)
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "evidence": [self._evidence_to_dict(e) for e in run.evidence],
            "metrics": float_metrics,
            "ref_resolutions": {
                ref: {"status": r.status.value, "resolved": r.is_resolved}
                for ref, r in self._resolved_refs.items()
            },
        }

    def _validity_for_mutation(
        self, tcid: str, mut: Dict[str, Any], res: Dict[str, Any]
    ) -> MapSpecValidityEvidence:
        """Build the validity ladder from recorded mutation evidence."""
        accepted = bool(mut.get("mutation_accepted", False))
        semantic_valid_flag = bool(mut.get("is_valid", False))
        if res.get("is_error"):
            tier = MapSpecValidityTier.MUTATION_REJECTED
        elif semantic_valid_flag:
            tier = MapSpecValidityTier.SEMANTIC_VALID
        elif accepted:
            tier = MapSpecValidityTier.MUTATION_ACCEPTED
        else:
            tier = MapSpecValidityTier.NOT_EVALUATED
        return MapSpecValidityEvidence(
            tier=tier,
            mutation_accepted=accepted if accepted else None,
            semantic_errors=mut.get("semantic_errors", []),
            mapspec_revision=res.get("mapspec_revision"),
            checkpoint_id=res.get("checkpoint_id"),
        )

    @staticmethod
    def _evidence_to_dict(ev: ToolCallEvidence) -> Dict[str, Any]:
        v = ev.mapspec_validity
        return {
            "run_id": ev.run_id,
            "session_id": ev.session_id,
            "turn_id": ev.turn_id,
            "tool_call_id": ev.tool_call_id,
            "tool_name": ev.tool_name,
            "duration_ms": ev.duration_ms,
            "is_error": ev.is_error,
            "error_msg": ev.error_msg,
            "refs": [
                {"ref": r.ref, "status": r.status.value, "resolved": r.is_resolved}
                for r in ev.ref_resolutions
            ],
            "mapspec_validity": (
                {
                    "tier": v.tier.name,
                    "is_valid": v.is_valid,
                    "evaluated": v.evaluated,
                    "semantic_errors": v.semantic_errors,
                    "checkpoint_id": v.checkpoint_id,
                }
                if v else None
            ),
            "runtime_evidence_path": ev.runtime_evidence_path,
        }

    # ── Legacy float-metric surface (now evidence-honest) ────────────────

    def compute_tool_choice_accuracy(self, expected_tools: List[str]) -> float:
        if not expected_tools:
            return 100.0
        invoked_tools = [tc["name"] for tc in self.tool_calls]
        expected_remaining = list(expected_tools)
        correct = 0
        for tool in invoked_tools:
            if tool in expected_remaining:
                correct += 1
                expected_remaining.remove(tool)
        accuracy = (correct / len(expected_tools)) * 100.0
        return min(100.0, max(0.0, accuracy))

    def compute_mapspec_validity(self) -> float:
        """% of mutations with REAL semantic-validity evidence.

        V2: a mutation that merely "didn't error" (MUTATION_ACCEPTED) does NOT
        count as valid — only SEMANTIC_VALID+ counts. No mutations recorded →
        0.0 (no evidence), not 100.0.
        """
        if not self.mapspec_mutations:
            return 0.0
        valid_count = sum(1 for m in self.mapspec_mutations if m.get("is_valid", False))
        validity = (valid_count / len(self.mapspec_mutations)) * 100.0
        return min(100.0, max(0.0, validity))

    def compute_cursor_resolution_rate(self) -> float:
        """% of refs that genuinely RESOLVED against the SessionStore.

        V2: syntactic-only refs (no resolver run, or resolver returned not-found /
        wrong-session) do NOT count. No refs recorded → 0.0 (no evidence), not 100.0.
        """
        if not self.ref_cursors:
            return 0.0
        resolved_count = sum(1 for c in self.ref_cursors if c.get("is_resolved", False))
        rate = (resolved_count / len(self.ref_cursors)) * 100.0
        return min(100.0, max(0.0, rate))

    def compute_step_efficiency(self, ideal_step_count: int) -> float:
        actual_step_count = len(self.tool_calls)
        if actual_step_count == 0:
            return 100.0 if ideal_step_count == 0 else 0.0
        efficiency = (ideal_step_count / actual_step_count) * 100.0
        return min(100.0, max(0.0, efficiency))

    def compute_error_recovery_rate(self) -> float:
        total_exceptions = len(self.exceptions)
        if total_exceptions == 0:
            return 100.0
        rate = (self.recovered_exceptions_count / total_exceptions) * 100.0
        return min(100.0, max(0.0, rate))

    def get_telemetry_summary(self) -> Dict[str, Dict[str, float]]:
        return {
            "rates": {
                "MapSpecValidity": round(self.compute_mapspec_validity(), 2),
                "CursorResolutionRate": round(self.compute_cursor_resolution_rate(), 2),
                "ErrorRecoveryRate": round(self.compute_error_recovery_rate(), 2),
            },
            "counts": {
                "ToolCallsCount": float(len(self.tool_calls)),
                "ExceptionsCount": float(len(self.exceptions)),
            },
        }

    def evaluate_all(
        self, expected_tools: List[str], ideal_step_count: int
    ) -> Dict[str, float]:
        return {
            "ToolChoiceAccuracy": round(self.compute_tool_choice_accuracy(expected_tools), 2),
            "MapSpecValidity": round(self.compute_mapspec_validity(), 2),
            "CursorResolutionRate": round(self.compute_cursor_resolution_rate(), 2),
            "StepEfficiency": round(self.compute_step_efficiency(ideal_step_count), 2),
            "ErrorRecoveryRate": round(self.compute_error_recovery_rate(), 2),
        }
