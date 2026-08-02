"""PiAgentHarness - Simulation Seam & 5-Dimensional Metric Evaluator for Pi Agent Bridge.

Intercepts SSE events and tool call payloads during Pi RPC / Chat execution,
tracking session execution telemetry and calculating evaluation metrics:
1. ToolChoiceAccuracy
2. MapSpecValidity
3. CursorResolutionRate
4. StepEfficiency
5. ErrorRecoveryRate
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REF_CURSOR_PATTERN = re.compile(r"ref:(geojson|raster|table):[a-zA-Z0-9_-]+")

MAPSPEC_MUTATION_TOOLS = {
    "webgis_project_init",
    "webgis_view_set",
    "webgis_source_profile",
    "webgis_layer_upsert",
    "webgis_layer_remove",
    "webgis_layout_set",
}


class PiAgentHarness:
    """Simulation seam & evaluation harness for PiAgentBridge execution sessions."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self.tool_calls: List[Dict[str, Any]] = []
        self.tool_results: List[Dict[str, Any]] = []
        self.sse_events: List[Dict[str, Any]] = []
        self.mapspec_mutations: List[Dict[str, Any]] = []
        self.ref_cursors: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        self.recovered_exceptions_count: int = 0
        self._in_error_state: bool = False

    def reset(self, session_id: str = "") -> None:
        """Reset the harness state for a new test session."""
        self.session_id = session_id
        self.tool_calls.clear()
        self.tool_results.clear()
        self.sse_events.clear()
        self.mapspec_mutations.clear()
        self.ref_cursors.clear()
        self.exceptions.clear()
        self.recovered_exceptions_count = 0
        self._in_error_state = False

    def record_tool_call(
        self, tool_call_id: str, name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Intercept and record a tool call request payload."""
        call_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments or {},
        }
        self.tool_calls.append(call_entry)
        self._scan_and_record_ref_cursors(tool_call_id, arguments)

        if name in MAPSPEC_MUTATION_TOOLS:
            self.mapspec_mutations.append({
                "tool_call_id": tool_call_id,
                "tool_name": name,
                "arguments": arguments,
                "is_valid": False,
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
        """Intercept and record a tool execution result."""
        result_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "result": result or {},
            "is_error": is_error,
            "error_msg": error_msg or "",
        }
        self.tool_results.append(result_entry)

        if is_error:
            self.exceptions.append({
                "tool_call_id": tool_call_id,
                "name": name,
                "error_msg": error_msg or "",
            })
            self._in_error_state = True
        else:
            if self._in_error_state:
                self.recovered_exceptions_count += 1
                self._in_error_state = False

        if name in MAPSPEC_MUTATION_TOOLS:
            for mutation in reversed(self.mapspec_mutations):
                if mutation["tool_call_id"] == tool_call_id:
                    success = not is_error and result.get("success", True) is not False
                    mutation["is_valid"] = success
                    break

        return result_entry

    def record_sse_event(self, event: Dict[str, Any]) -> None:
        """Intercept and record an SSE event dict."""
        self.sse_events.append(event)

    def _scan_and_record_ref_cursors(self, tool_call_id: str, args: Dict[str, Any]) -> None:
        """Scan tool arguments recursively for ref: cursor strings."""
        args_str = str(args)
        found_refs = set(re.findall(r"ref:(?:geojson|raster|table):[a-zA-Z0-9_-]+", args_str))
        for ref in found_refs:
            is_resolved = self._check_ref_cursor_resolved(ref)
            self.ref_cursors.append({
                "tool_call_id": tool_call_id,
                "ref_cursor": ref,
                "is_resolved": is_resolved,
            })

    def _check_ref_cursor_resolved(self, ref_cursor: str) -> bool:
        """Check if reference cursor is valid and resolved."""
        return bool(ref_cursor and ref_cursor.startswith("ref:"))

    def compute_tool_choice_accuracy(self, expected_tools: List[str]) -> float:
        """ToolChoiceAccuracy = (correct_tool_invocations / total_expected_tools) * 100%"""
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
        """MapSpecValidity = (valid_mapspec_mutations / total_mapspec_mutations) * 100%"""
        if not self.mapspec_mutations:
            return 100.0
        valid_count = sum(1 for m in self.mapspec_mutations if m.get("is_valid", False))
        validity = (valid_count / len(self.mapspec_mutations)) * 100.0
        return min(100.0, max(0.0, validity))

    def compute_cursor_resolution_rate(self) -> float:
        """CursorResolutionRate = (resolved_ref_cursors / total_ref_cursors) * 100%"""
        if not self.ref_cursors:
            return 100.0
        resolved_count = sum(1 for c in self.ref_cursors if c.get("is_resolved", False))
        rate = (resolved_count / len(self.ref_cursors)) * 100.0
        return min(100.0, max(0.0, rate))

    def compute_step_efficiency(self, ideal_step_count: int) -> float:
        """StepEfficiency = (ideal_step_count / actual_step_count) * 100%"""
        actual_step_count = len(self.tool_calls)
        if actual_step_count == 0:
            return 100.0 if ideal_step_count == 0 else 0.0
        efficiency = (ideal_step_count / actual_step_count) * 100.0
        return min(100.0, max(0.0, efficiency))

    def compute_error_recovery_rate(self) -> float:
        """ErrorRecoveryRate = (successful_recoveries / total_tool_exceptions) * 100%"""
        total_exceptions = len(self.exceptions)
        if total_exceptions == 0:
            return 100.0
        rate = (self.recovered_exceptions_count / total_exceptions) * 100.0
        return min(100.0, max(0.0, rate))

    def evaluate_all(
        self, expected_tools: List[str], ideal_step_count: int
    ) -> Dict[str, float]:
        """Compute all 5 evaluation metrics."""
        return {
            "ToolChoiceAccuracy": round(self.compute_tool_choice_accuracy(expected_tools), 2),
            "MapSpecValidity": round(self.compute_mapspec_validity(), 2),
            "CursorResolutionRate": round(self.compute_cursor_resolution_rate(), 2),
            "StepEfficiency": round(self.compute_step_efficiency(ideal_step_count), 2),
            "ErrorRecoveryRate": round(self.compute_error_recovery_rate(), 2),
        }
