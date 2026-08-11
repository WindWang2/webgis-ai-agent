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
    MapActionEvidence,
    MapActionStatus,
    MapSpecValidityEvidence,
    MapSpecValidityTier,
    RefResolution,
    RefResolutionStatus,
    ToolCallEvidence,
)
from app.lib.harness.tool_call_event import ToolCallEvent

logger = logging.getLogger(__name__)

# Real ref format produced by SessionStore is ``ref:<prefix>-<id>`` (DASH), e.g.
# ``ref:geojson-abc123``. The legacy colon pattern never matched real refs, so
# CursorResolutionRate was structurally always 100 (no refs ever detected).
# V3: heatmap refs are stored by ToolDispatchService (tool_dispatch_service.py)
# with prefix ``heatmap`` (audit BE-2 finding), so they must be detected too.
REF_CURSOR_PATTERN = re.compile(r"ref:(?:geojson|raster|table|data|heatmap)-[a-zA-Z0-9_-]+")

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
# V3: 读取 session store 中前端回传的地图动作 ACK（session_id -> ack dict 列表），
# 镜像 ref_resolver 的注入 seam。ack dict 的形态见 app/services/session_data.py
# append_map_action_event（action_id/status/actual/error/correlation...）。
MapActionReader = Callable[[str], Awaitable[List[Dict[str, Any]]]]

# ── V3 交互收敛判定（design §5，后端权威重算，绝不信 hint 单独）────────────
# 相机收敛容差：center ≤0.001°，zoom ≤0.05。浮点边界（如 116.001-116.0）会有
# ~1e-12 噪声，判定用 ε=1e-9 吸收，保持"≤ 容差"语义不被 float 噪声翻转。
CAMERA_CENTER_TOL_DEG = 0.001
CAMERA_ZOOM_TOL = 0.05
_FLOAT_EPSILON = 1e-9


def _has_camera_state(d: Any) -> bool:
    """dict 是否携带可判定相机收敛的视口状态（center + zoom）。"""
    return isinstance(d, dict) and "center" in d and "zoom" in d


def _camera_match(requested: Any, actual: Any) -> bool:
    """requested 与 actual 视口是否收敛（center/zoom 均在容差内）。

    后端重算：只要 requested/actual 数据都在，就以此为准，不看前端 hint。
    """
    if not (_has_camera_state(requested) and _has_camera_state(actual)):
        return False
    try:
        r_center = requested.get("center")
        a_center = actual.get("center")
        r_zoom = requested.get("zoom")
        a_zoom = actual.get("zoom")
        if r_zoom is None or a_zoom is None:
            return False
        if abs(float(r_zoom) - float(a_zoom)) > CAMERA_ZOOM_TOL + _FLOAT_EPSILON:
            return False
        if not isinstance(r_center, (list, tuple)) or len(r_center) < 2:
            return False
        if not isinstance(a_center, (list, tuple)) or len(a_center) < 2:
            return False
        if abs(float(r_center[0]) - float(a_center[0])) > CAMERA_CENTER_TOL_DEG + _FLOAT_EPSILON:
            return False
        if abs(float(r_center[1]) - float(a_center[1])) > CAMERA_CENTER_TOL_DEG + _FLOAT_EPSILON:
            return False
        return True
    except (TypeError, ValueError):
        return False


def _is_verifiable_ack(ev: MapActionEvidence) -> bool:
    """ACK 是否可验证收敛：
    - 相机类：requested 与 actual 都带 center+zoom → 后端可重算；
    - 图层增删等：actual.confirmed 存在；
    - 其它：仅当 actual 显式携带 converged 提示。
    """
    actual = ev.actual or {}
    if _has_camera_state(ev.requested) and _has_camera_state(actual):
        return True
    return "confirmed" in actual or "converged" in actual


def _ack_converged(ev: MapActionEvidence) -> bool:
    """单条 ACK 是否收敛（design §5）。

    数据优先：相机数据齐全时后端重算（hint 仅供参考，绝不单独采信）；
    confirmed/converged 提示仅在无数据可重算时兜底。
    """
    actual = ev.actual or {}
    if _has_camera_state(ev.requested) and _has_camera_state(actual):
        return _camera_match(ev.requested, actual)
    if "confirmed" in actual:
        return actual.get("confirmed") is True
    return actual.get("converged") is True


def _ack_is_well_formed(ev: MapActionEvidence) -> bool:
    """非成功终态 ACK 的恢复证据是否结构完整（具名 status + error/reason）。"""
    if ev.status not in (
        MapActionStatus.FAILED,
        MapActionStatus.CANCELLED,
        MapActionStatus.SUPERSEDED,
    ):
        return False
    actual = ev.actual or {}
    return bool(ev.error) or bool(actual.get("reason") or actual.get("error"))


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

        # V3: 已发出的地图动作（issued 侧证据，FIFO 上限与其它累积面一致）。
        # 终态 ACK 由前端经 session store 上报；evaluate_with_evidence 通过
        # map_action_reader 读取并匹配（action_id 精确匹配）。
        self.map_actions_issued: List[Dict[str, Any]] = []
        # 最近一次 evaluate_with_evidence() 构建的 MapActionEvidence（issued ∪ ack）。
        self._map_action_evidence: List[MapActionEvidence] = []

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
        self.map_actions_issued.clear()
        self._map_action_evidence.clear()
        self._resolved_refs.clear()
        self._validity_cache.clear()
        self.recovered_exceptions_count = 0
        self._in_error_state = False

    # ── Raw event recording (sync, legacy-compatible) ────────────────────

    def record_tool_call(
        self,
        tool_call_id: str,
        name: str,
        arguments: Dict[str, Any],
        *,
        run_id: str = "",
        turn_id: str = "",
    ) -> Dict[str, Any]:
        """记录一次工具调用。

        V3 新增可选 per-record run_id/turn_id：显式透传（生产路径绝不调用全局
        set_correlation —— 单例 harness 跨 session 累积，全局 correlation 会互相
        污染）。缺省回退到当前 _active_run_id/_active_turn_id（向后兼容）。
        """
        eff_run_id = run_id or self._active_run_id
        eff_turn_id = turn_id or self._active_turn_id
        call_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments or {},
            "run_id": eff_run_id,
            "turn_id": eff_turn_id,
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
                "run_id": eff_run_id,
                "turn_id": eff_turn_id,
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
        *,
        run_id: str = "",
        turn_id: str = "",
    ) -> Dict[str, Any]:
        """记录一次工具结果。

        V3 新增可选 per-record run_id/turn_id（语义同 record_tool_call）。
        """
        eff_run_id = run_id or self._active_run_id
        eff_turn_id = turn_id or self._active_turn_id
        result_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "result": result or {},
            "is_error": is_error,
            "error_msg": error_msg or "",
            "run_id": eff_run_id,
            "turn_id": eff_turn_id,
        }
        self._append_capped(self.tool_results, result_entry)

        if is_error:
            self._append_capped(self.exceptions, {
                "tool_call_id": tool_call_id,
                "name": name,
                "error_msg": error_msg or "",
                "run_id": eff_run_id,
                "turn_id": eff_turn_id,
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

    def record_map_action_issued(
        self,
        session_id: str,
        tool_call_id: str,
        turn_id: str = "",
        action_id: str = "",
        command: str = "",
        requested: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录一次已发出的地图动作（V3 issued 侧证据），FIFO 受 MAX_EVENTS 约束。

        终态 ACK 由前端经 session store 上报（BE-1 的 append_map_action_event），
        evaluate_with_evidence 通过 map_action_reader 读取并按 action_id 精确匹配。
        没有 ACK 的动作永远停留在 ISSUED —— 缺失证据绝不被当作 success。

        ``requested`` 为请求目标参数快照（ToolDispatchService 铸 action_id 时已
        按 ~2KB 封顶），供后端重算相机收敛。

        session_id 为空时不记录 —— 无法归属到具体会话的 issued 记录在评估时
        无法按 session_id 隔离（map_action_reader 是 session-scoped），记下来
        只会污染其它会话的评估。
        """
        if not session_id:
            return {}
        entry = {
            "action_id": action_id,
            "command": command,
            "session_id": session_id,
            "run_id": self._active_run_id,
            "turn_id": turn_id or self._active_turn_id,
            "tool_call_id": tool_call_id,
            "requested": dict(requested) if isinstance(requested, dict) else {},
        }
        self._append_capped(self.map_actions_issued, entry)
        return entry

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
        *,
        map_action_reader: Optional[MapActionReader] = None,
    ) -> Dict[str, Any]:
        """Perform REAL ref resolution + structured evidence collection.

        Returns a structured evaluation with the validity ladder + ref statuses.
        Also caches resolutions so the legacy sync compute_* reflect real data.

        V3：``map_action_reader`` 镜像 ``ref_resolver`` 的注入 seam —— 读取 session
        store 中前端回传的地图动作 ACK（(session_id) -> ack dict 列表），按
        action_id 精确匹配到 issued 动作，构建 MapActionEvidence；无 ACK 的动作
        status 保持 ISSUED（缺失证据）。结果新增 ``interaction`` 段
        {issued, acked, actions}。
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

        # 1b. V3: 读取 session store ACK，构建地图动作证据（issued ∪ ack）。
        self._map_action_evidence = await self._build_map_action_evidence(
            map_action_reader
        )

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
                # V3: 该工具调用发出的地图动作证据（按 tool_call_id 归属）。
                map_actions=[
                    a for a in self._map_action_evidence
                    if a.tool_call_id == tcid
                ],
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
            # V3: 交互段（issued 侧 vs 终态 acked 侧）。
            "interaction": self._interaction_section(),
        }

    # ── V3: 地图动作证据构建 ─────────────────────────────────────────────

    async def _build_map_action_evidence(
        self, map_action_reader: Optional[MapActionReader]
    ) -> List[MapActionEvidence]:
        """把 issued 记录与 session store ACK 合并成 MapActionEvidence 列表。

        会话隔离：harness 单例会跨 session 累积 issued 记录，而 map_action_reader
        是 session-scoped（只返回当前 session 的 ACK）。这里按 session_id 过滤
        issued 侧，只保留当前评估会话的动作 —— 其它会话的 issued/ack 绝不能算进
        本次 coverage（前端 action_id 是随机 mint，但跨会话重名/重放时首达终态
        幂等只按 action_id，必须先在 issued 侧隔离）。
        """
        issued = [
            rec for rec in self.map_actions_issued
            if rec.get("session_id") == self.session_id
        ]
        if not issued:
            return []
        acks_by_id: Dict[str, Dict[str, Any]] = {}
        if map_action_reader is not None:
            try:
                acks = await map_action_reader(self.session_id)
            except Exception as e:  # noqa: BLE001 - reader 失败必须可观察而非崩评估
                logger.warning("[Harness] map_action_reader failed for %s: %s", self.session_id, e)
                acks = []
            if isinstance(acks, list):
                for ack in acks:
                    if isinstance(ack, dict) and ack.get("action_id"):
                        acks_by_id[str(ack["action_id"])] = ack
        return [
            self._map_action_evidence_from_record(rec, acks_by_id.get(rec.get("action_id")))
            for rec in issued
        ]

    @staticmethod
    def _map_action_evidence_from_record(
        rec: Dict[str, Any], ack: Optional[Dict[str, Any]]
    ) -> MapActionEvidence:
        """从 issued 记录 + 可选 ACK 构建 MapActionEvidence。

        无 ACK（或 ACK 的 status 无法解析）→ 保持 ISSUED = 缺失终态证据。
        ACK 侧 correlation（run/turn/sse_event_id）优先于 issued 记录的缺省值。
        requested 相反：issued 快照带相机状态时优先（防客户端假快照自证收敛），
        ACK 侧 requested 仅兜底。
        """
        status = MapActionStatus.ISSUED
        actual: Dict[str, Any] = {}
        error = ""
        started_at = finished_at = ""
        duration_ms: Optional[float] = None
        run_id = str(rec.get("run_id") or "")
        turn_id = str(rec.get("turn_id") or "")
        sse_event_id = ""
        requested: Dict[str, Any] = rec.get("requested") if isinstance(rec.get("requested"), dict) else {}

        if ack is not None:
            ack_status = str(ack.get("status") or "")
            if ack_status:
                try:
                    status = MapActionStatus(ack_status)
                except ValueError:
                    # 无法解析的终态 = 不可信证据，保持 ISSUED。
                    status = MapActionStatus.ISSUED
            if isinstance(ack.get("actual"), dict):
                actual = ack["actual"]
            # 收敛防伪：requested 以后端铸造的 issued 快照为准 —— 它带相机状态
            # （center/zoom）时绝不能被客户端 ACK 声称的 requested 覆盖（否则
            # 客户端可上报 requested==actual 的假快照"自证收敛"，后端重算形同
            # 虚设）。ACK 侧 requested 仅在 issued 快照无相机状态时兜底。
            if not _has_camera_state(requested) and isinstance(ack.get("requested"), dict):
                requested = ack["requested"]
            error = str(ack.get("error") or "")
            started_at = str(ack.get("started_at") or "")
            finished_at = str(ack.get("finished_at") or "")
            dur = ack.get("duration_ms")
            if isinstance(dur, (int, float)):
                duration_ms = float(dur)
            corr = ack.get("correlation") if isinstance(ack.get("correlation"), dict) else {}
            run_id = str(corr.get("run_id") or run_id)
            turn_id = str(corr.get("turn_id") or turn_id)
            sse_event_id = str(corr.get("sse_event_id") or "")

        return MapActionEvidence(
            action_id=str(rec.get("action_id") or ""),
            command=str(rec.get("command") or ""),
            session_id=str(rec.get("session_id") or ""),
            status=status,
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=str(rec.get("tool_call_id") or ""),
            sse_event_id=sse_event_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error=error,
            requested=requested,
            actual=actual,
        )

    def _interaction_section(self) -> Dict[str, Any]:
        """评估结果中的 interaction 段：{issued, acked, actions}。"""
        actions = self._map_action_evidence
        return {
            "issued": len(actions),
            "acked": sum(1 for a in actions if a.has_terminal_evidence),
            "actions": [
                self._map_action_evidence_to_dict(a) for a in actions
            ],
        }

    @staticmethod
    def _map_action_evidence_to_dict(a: MapActionEvidence) -> Dict[str, Any]:
        """序列化单条 MapActionEvidence（含收敛判定，供评估/调试观测）。"""
        verifiable = _is_verifiable_ack(a)
        return {
            "action_id": a.action_id,
            "command": a.command,
            "session_id": a.session_id,
            "status": a.status.value,
            "run_id": a.run_id,
            "turn_id": a.turn_id,
            "tool_call_id": a.tool_call_id,
            "sse_event_id": a.sse_event_id,
            "started_at": a.started_at,
            "finished_at": a.finished_at,
            "duration_ms": a.duration_ms,
            "error": a.error,
            "requested": a.requested,
            "actual": a.actual,
            "verifiable": verifiable,
            "converged": _ack_converged(a) if verifiable else None,
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
            # V3: 该工具调用发出的地图动作证据（每条独立 correlation）。
            "map_actions": [
                PiAgentHarness._map_action_evidence_to_dict(a) for a in ev.map_actions
            ],
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

    # ── V3: 地图交互闭环指标（design §5，缺失证据 → 0.0，绝不为 100）────────

    def compute_interaction_evidence_coverage(self) -> float:
        """终态 ACK / 已发出的动作。无 issued 记录 → 0.0（缺失证据）。"""
        issued = self._map_action_evidence
        if not issued:
            return 0.0
        terminal = sum(1 for a in issued if a.has_terminal_evidence)
        return min(100.0, max(0.0, (terminal / len(issued)) * 100.0))

    def compute_map_command_execution_success_rate(self) -> float:
        """SUCCEEDED / 终态 ACK。无终态 ACK → 0.0（缺失证据）。"""
        terminal = [a for a in self._map_action_evidence if a.has_terminal_evidence]
        if not terminal:
            return 0.0
        succeeded = sum(1 for a in terminal if a.status == MapActionStatus.SUCCEEDED)
        return min(100.0, max(0.0, (succeeded / len(terminal)) * 100.0))

    def compute_interaction_state_convergence_rate(self) -> float:
        """可验证 ACK 中收敛的比例（后端从 requested/actual 重算，容差见
        CAMERA_CENTER_TOL_DEG / CAMERA_ZOOM_TOL）。无可验证 ACK → 0.0。"""
        verifiable = [a for a in self._map_action_evidence if _is_verifiable_ack(a)]
        if not verifiable:
            return 0.0
        converged = sum(1 for a in verifiable if _ack_converged(a))
        return min(100.0, max(0.0, (converged / len(verifiable)) * 100.0))

    def compute_interaction_recovery_rate(self) -> float:
        """非成功终态 ACK 中"结构完整"（具名 status + error/reason）的比例。

        无非成功 ACK → 0.0（缺失恢复证据，绝不为 100）。
        """
        non_succeeded = [
            a for a in self._map_action_evidence
            if a.has_terminal_evidence and a.status != MapActionStatus.SUCCEEDED
        ]
        if not non_succeeded:
            return 0.0
        well_formed = sum(1 for a in non_succeeded if _ack_is_well_formed(a))
        return min(100.0, max(0.0, (well_formed / len(non_succeeded)) * 100.0))

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
        metrics: Dict[str, float] = {
            "ToolChoiceAccuracy": round(self.compute_tool_choice_accuracy(expected_tools), 2),
            "MapSpecValidity": round(self.compute_mapspec_validity(), 2),
            "CursorResolutionRate": round(self.compute_cursor_resolution_rate(), 2),
            "StepEfficiency": round(self.compute_step_efficiency(ideal_step_count), 2),
            "ErrorRecoveryRate": round(self.compute_error_recovery_rate(), 2),
        }
        # V3 交互维度（additive）：仅当本次会话确实有 issued 交互证据时才输出，
        # 镜像 evaluate_evidence 的 'evaluated = issued > 0'。无交互证据时省略这
        # 4 个键 —— 同步 evaluate_session 对缺失的交互维度直接跳过，避免 0.0
        # 把无交互 run 的 V2 gate 拖垮（harness_runner run_benchmark_scenario
        # 全量走 evaluate_all→evaluate_session，此前每跑必 fail）。缺失证据本身
        # 仍是诚实的 0.0（有 issued 记录但无 ACK 时照常输出 0.0），绝不为 100。
        if self._map_action_evidence:
            metrics.update({
                "InteractionEvidenceCoverage": round(self.compute_interaction_evidence_coverage(), 2),
                "MapCommandExecutionSuccessRate": round(self.compute_map_command_execution_success_rate(), 2),
                "InteractionStateConvergenceRate": round(self.compute_interaction_state_convergence_rate(), 2),
                "InteractionRecoveryRate": round(self.compute_interaction_recovery_rate(), 2),
            })
        return metrics
