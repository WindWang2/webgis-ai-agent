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

import copy
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.lib.harness.evidence import (
    CartographicReviewEvidence,
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
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

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
# Session-owned desired MapSpec + frontend observation reader. The returned
# mapping must carry its session_id so a miswired/cross-tenant adapter fails
# closed rather than letting one session certify another session's map.
CartographyStateReader = Callable[[str], Awaitable[Dict[str, Any]]]

# ── V3 交互收敛判定（design §5，后端权威重算，绝不信 hint 单独）────────────
# 相机收敛容差：center ≤0.001°，zoom ≤0.05。浮点边界（如 116.001-116.0）会有
# ~1e-12 噪声，判定用 ε=1e-9 吸收，保持"≤ 容差"语义不被 float 噪声翻转。
CAMERA_CENTER_TOL_DEG = 0.001
CAMERA_ZOOM_TOL = 0.05
_FLOAT_EPSILON = 1e-9


def _expected_runtime_refs(
    layer: Dict[str, Any], sources: Dict[str, Any]
) -> List[str]:
    """Stable identities that may represent one desired layer in the HUD.

    The primary map mounts analysis outputs under their session-owned result
    ref, while MapSpec keeps the semantic layer id.  Provenance provides the
    only truthful bridge between those identities; names and list position are
    deliberately never used as substitutes.
    """
    candidates: List[Any] = [layer.get("id")]
    source = sources.get(layer.get("source"))
    if isinstance(source, dict):
        candidates.extend((source.get("ref"), source.get("ref_id")))
    provenance = layer.get("provenance")
    if isinstance(provenance, dict):
        candidates.extend((provenance.get("result_ref"), provenance.get("source_ref")))
    return list(dict.fromkeys(str(value) for value in candidates if value))


def _actual_runtime_refs(layer: Dict[str, Any]) -> List[str]:
    return list(dict.fromkeys(
        str(value) for value in (layer.get("id"), layer.get("_refId")) if value
    ))


def _runtime_identity_match(
    desired: Dict[str, Any], sources: Dict[str, Any], actual: Dict[str, Any]
) -> Optional[str]:
    """Match exact result provenance when the HUD exposes a result ref.

    A coincidentally equal semantic id must not let a HUD layer backed by a
    different analysis result certify this MapSpec.
    """
    expected_refs = _expected_runtime_refs(desired, sources)
    semantic_id = str(desired.get("id"))
    provenance_refs = [ref for ref in expected_refs if ref != semantic_id]
    actual_ref = actual.get("_refId")
    if provenance_refs:
        # Once desired state names an authoritative result, semantic ids are
        # presentation labels only.  Missing or different runtime provenance
        # cannot certify that the displayed layer is the analysis result.
        if not actual_ref:
            return None
        return str(actual_ref) if str(actual_ref) in provenance_refs else None
    actual_refs = _actual_runtime_refs(actual)
    return next((ref for ref in expected_refs if ref in actual_refs), None)


def _constant_layer_opacity(layer: Dict[str, Any]) -> Optional[float]:
    """Return an explicitly desired constant opacity, never an expression."""
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return None
    layer_type = str(layer.get("type") or "")
    keys = {
        "circle": ("circle-opacity", "opacity"),
        "line": ("line-opacity", "opacity"),
        "fill": ("fill-opacity", "opacity"),
        "raster": ("raster-opacity", "opacity"),
        "symbol": ("icon-opacity", "text-opacity", "opacity"),
    }.get(layer_type, ("opacity",))
    values: List[float] = []
    for key in keys:
        value = paint.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if math.isfinite(numeric):
            values.append(numeric)
    if not values or any(abs(value - values[0]) > _FLOAT_EPSILON for value in values[1:]):
        return None
    return values[0]


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
    """ACK 是否可验证收敛（Round-2 P1：存储侧直接 ACK 不可验证）：
    - 相机类：requested 与 actual 都带 center+zoom → 后端可重算；
    - 图层增删等：actual.confirmed 必须为 True（键存在不算，False 不算）；
    - store_mounted / store_updated 等"存储侧直接 ACK"只证明挂载/写入成功，
      不证明地图状态收敛 → 无论是否带 converged hint 都不可验证（排除出
      InteractionStateConvergenceRate 分母，但仍计终态 ACK：coverage/success 照算）；
    - 其它：仅当 actual 显式携带 converged 提示（无数据可重算时的兜底）。
    """
    actual = ev.actual or {}
    if _has_camera_state(ev.requested) and _has_camera_state(actual):
        return True
    if actual.get("store_mounted") or actual.get("store_updated"):
        return False
    return actual.get("confirmed") is True or actual.get("converged") is True


def _ack_converged(ev: MapActionEvidence) -> bool:
    """单条 ACK 是否收敛（design §5）。

    数据优先：相机数据齐全时后端重算（hint 仅供参考，绝不单独采信）；
    confirmed/converged 提示仅在无数据可重算时兜底；store_mounted /
    store_updated 存储侧 ACK 永不判收敛（挂载成功 ≠ 地图状态收敛）。
    """
    actual = ev.actual or {}
    if _has_camera_state(ev.requested) and _has_camera_state(actual):
        return _camera_match(ev.requested, actual)
    if actual.get("store_mounted") or actual.get("store_updated"):
        return False
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
        cartography_state_reader: Optional[CartographyStateReader] = None,
        map_action_reader: Optional[MapActionReader] = None,
    ):
        self.session_id = session_id
        self.ref_resolver = ref_resolver
        self.mapspec_validator = mapspec_validator
        self.cartography_state_reader = cartography_state_reader
        self.map_action_reader = map_action_reader

        # Raw event buffers (legacy-compatible shape), FIFO-capped.
        self.tool_calls: List[Dict[str, Any]] = []
        self.tool_results: List[Dict[str, Any]] = []
        self.sse_events: List[Dict[str, Any]] = []
        self.mapspec_mutations: List[Dict[str, Any]] = []
        self.ref_cursors: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        # Round-2 P2：单例 harness 跨 session 累积时，错误状态必须按 session
        # 隔离 —— session A 的错误绝不能被 session B 的后续成功"恢复"。
        self._recovered_exceptions_count: Dict[str, int] = {}
        self._in_error_state: set = set()

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
        # Round-2 P2：以 (session_id, ref) 为键 —— 单例跨 session 时同一 ref
        # 字符串在两个会话的解析结果不得互相污染。
        self._resolved_refs: Dict[Tuple[str, str], RefResolution] = {}
        self._validity_cache: Dict[str, MapSpecValidityEvidence] = {}
        # Desired reviews are pure and keyed by content fingerprint. Runtime
        # observations/ACKs are intentionally not cached; they remain live.
        self._cartography_review_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

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
        self._cartography_review_cache.clear()
        self._recovered_exceptions_count.clear()
        self._in_error_state.clear()

    # ── Raw event recording (sync, legacy-compatible) ────────────────────

    def record_tool_call(
        self,
        tool_call_id: str,
        name: str,
        arguments: Dict[str, Any],
        *,
        run_id: str = "",
        turn_id: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """记录一次工具调用。

        V3 新增可选 per-record run_id/turn_id：显式透传（生产路径绝不调用全局
        set_correlation —— 单例 harness 跨 session 累积，全局 correlation 会互相
        污染）。缺省回退到当前 _active_run_id/_active_turn_id（向后兼容）。
        Round-2 新增可选 per-record session_id：record_event 从事件显式透传真实
        session（绝不改写 self.session_id —— 那是评估目标）；缺省回退
        self.session_id（向后兼容）。
        """
        eff_run_id = run_id or self._active_run_id
        eff_turn_id = turn_id or self._active_turn_id
        eff_session_id = session_id or self.session_id
        call_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments or {},
            "run_id": eff_run_id,
            "turn_id": eff_turn_id,
            "session_id": eff_session_id,
        }
        self._append_capped(self.tool_calls, call_entry)
        self._scan_and_record_ref_cursors(tool_call_id, arguments, eff_session_id)

        if name in MAPSPEC_MUTATION_TOOLS:
            self._append_capped(self.mapspec_mutations, {
                "tool_call_id": tool_call_id,
                "tool_name": name,
                "arguments": arguments,
                "is_valid": False,
                "run_id": eff_run_id,
                "turn_id": eff_turn_id,
                "session_id": eff_session_id,
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
        session_id: str = "",
    ) -> Dict[str, Any]:
        """记录一次工具结果。

        V3 新增可选 per-record run_id/turn_id（语义同 record_tool_call）。
        Round-2 新增可选 per-record session_id：结果/异常条目按真实 session 归属，
        错误恢复状态（_in_error_state / _recovered_exceptions_count）按 session
        隔离 —— 单例 harness 跨 session 时，A 的错误绝不被 B 的成功"恢复"。
        """
        eff_run_id = run_id or self._active_run_id
        eff_turn_id = turn_id or self._active_turn_id
        eff_session_id = session_id or self.session_id
        result_entry = {
            "tool_call_id": tool_call_id,
            "name": name,
            "result": result or {},
            "is_error": is_error,
            "error_msg": error_msg or "",
            "run_id": eff_run_id,
            "turn_id": eff_turn_id,
            "session_id": eff_session_id,
        }
        self._append_capped(self.tool_results, result_entry)

        if is_error:
            self._append_capped(self.exceptions, {
                "tool_call_id": tool_call_id,
                "name": name,
                "error_msg": error_msg or "",
                "run_id": eff_run_id,
                "turn_id": eff_turn_id,
                "session_id": eff_session_id,
            })
            self._in_error_state.add(eff_session_id)
        else:
            if eff_session_id in self._in_error_state:
                self._recovered_exceptions_count[eff_session_id] = (
                    self._recovered_exceptions_count.get(eff_session_id, 0) + 1
                )
                self._in_error_state.discard(eff_session_id)

        if name in MAPSPEC_MUTATION_TOOLS:
            # V2: MapSpec validity derives from REAL evidence — the tool result
            # of a mutation carries ``is_compiled`` (pure-Python validate() outcome
            # from MapSpecLifecycleEngine) and ``success``. "Didn't error" alone
            # is mutation_accepted, NOT semantic validity.
            for mutation in reversed(self.mapspec_mutations):
                if (
                    mutation["tool_call_id"] == tool_call_id
                    and mutation.get("session_id") == eff_session_id
                ):
                    mutation_accepted = (
                        not is_error and result.get("success", True) is not False
                    )
                    semantic_valid = (
                        mutation_accepted
                        and result.get("is_compiled") is True
                    )
                    mutation["is_valid"] = semantic_valid  # SEMANTIC_VALID tier
                    mutation["mutation_accepted"] = mutation_accepted
                    # ADR-0052: semantic_errors carries BOTH the structural
                    # validate() warnings AND the deterministic cartography
                    # findings (paint↔legend drift, cardinality, domain, …) so
                    # "structurally valid but thematically inconsistent" is
                    # surfaced as evidence. Tier logic is unchanged — structural
                    # validity (is_compiled) ≠ thematic correctness; the findings
                    # are the evidence channel. Profile-dependent cartography
                    # checks report NOT_EVALUATED (never a fake pass).
                    cartography_errors = [
                        f"{f.get('check')}: {f.get('message')}"
                        for f in (result.get("cartography_findings") or [])
                        if isinstance(f, dict) and f.get("severity") == "error"
                        and f.get("evaluated", True)
                    ]
                    mutation["semantic_errors"] = result.get("warnings", []) + cartography_errors
                    break
        return result_entry

    def record_sse_event(self, event: Dict[str, Any]) -> None:
        """记录一条 SSE 事件。Round-2 P2：补 session_id 归属戳（事件本身不携带
        session 时回退评估目标），使 sse_events 可被 session 过滤/审计。"""
        if not event.get("session_id"):
            event["session_id"] = self.session_id
        self._append_capped(self.sse_events, event)

    def record_event(self, event: ToolCallEvent) -> None:
        # Round-2 P2：绝不改写 self.session_id —— 那是本 harness 的评估目标
        # （单例跨 session 时被 event 轮番改写会让"评估哪个 session"失去锚点）。
        # 事件携带的真实 session 通过 per-record 参数显式透传，记录按真实
        # session 归属，评估读取按 self.session_id 过滤，互不污染。
        self.record_tool_call(
            tool_call_id=event.tool_call_id,
            name=event.tool_name,
            arguments=event.arguments,
            session_id=event.session_id,
        )
        self.record_tool_result(
            tool_call_id=event.tool_call_id,
            name=event.tool_name,
            result=event.result,
            is_error=event.is_error,
            error_msg=event.error_msg,
            session_id=event.session_id,
        )

    def record_map_action_issued(
        self,
        session_id: str,
        tool_call_id: str,
        turn_id: str = "",
        action_id: str = "",
        command: str = "",
        requested: Optional[Dict[str, Any]] = None,
        mapspec_fingerprint: Optional[str] = None,
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
        # OBSERVABILITY (W7): stamp issued-at so ack_wait (issued → terminal ACK)
        # is computable when the closed loop is wired. Without a timestamp the
        # issued side carried no time evidence at all.
        entry = {
            "action_id": action_id,
            "command": command,
            "session_id": session_id,
            "run_id": self._active_run_id,
            "turn_id": turn_id or self._active_turn_id,
            "tool_call_id": tool_call_id,
            "issued_at_monotonic": time.monotonic(),
            "issued_at_ts": datetime.now(timezone.utc).isoformat(),
            "requested": dict(requested) if isinstance(requested, dict) else {},
            "mapspec_fingerprint": mapspec_fingerprint,
        }
        self._append_capped(self.map_actions_issued, entry)
        return entry

    # ── Ref cursor scanning ──────────────────────────────────────────────

    def _scan_and_record_ref_cursors(
        self, tool_call_id: str, args: Dict[str, Any], session_id: str = ""
    ) -> None:
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
                "session_id": session_id or self.session_id,
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

        # 1. Resolve every recorded ref cursor against the real SessionStore —
        #    Round-2 P2：仅当前评估 session 的 refs（单例 harness 跨 session 时，
        #    其它会话的 refs 不得用本 session 的 store 解析，也不得写坏它们的
        #    状态位）；解析结果按 (session_id, ref) 缓存，避免同 ref 跨会话污染。
        if self.ref_resolver is not None:
            for rc in self.ref_cursors:
                if rc.get("session_id") != self.session_id:
                    continue
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
                self._resolved_refs[(self.session_id, ref)] = resolution
                rc["is_resolved"] = resolution.is_resolved
                rc["status"] = resolution.status.value
        # If no resolver wired: refs remain SYNTACTICALLY_VALID (not resolved) —
        # the metrics below will honestly reflect "not verified".

        # 1b. V3: 读取 session store ACK，构建地图动作证据（issued ∪ ack）。
        self._map_action_evidence = await self._build_map_action_evidence(
            map_action_reader or self.map_action_reader
        )

        # 2. Build per-tool-call evidence with correlation + validity ladder.
        #    Round-2 P2：所有读取面按 session_id === self.session_id 过滤（镜像
        #    _build_map_action_evidence 的 issued 侧隔离）—— 单例 harness 跨
        #    session 累积时，非交互表面同样不得混入其它会话的工具/结果/refs。
        results_by_id = {
            r["tool_call_id"]: r for r in self.tool_results
            if r.get("session_id") == self.session_id
        }
        mutation_results = {
            m["tool_call_id"]: m for m in self.mapspec_mutations
            if m.get("session_id") == self.session_id
        }

        for tc in self.tool_calls:
            if tc.get("session_id") != self.session_id:
                continue
            tcid = tc["tool_call_id"]
            res = results_by_id.get(tcid, {})
            refs_for_call = [
                self._resolved_refs.get((self.session_id, rc["ref_cursor"]))
                or RefResolution(
                    ref=rc["ref_cursor"],
                    session_id=self.session_id,
                    status=RefResolutionStatus.SYNTACTICALLY_VALID,
                )
                for rc in self.ref_cursors
                if rc["tool_call_id"] == tcid
                and rc.get("session_id") == self.session_id
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
                runtime_evidence_path=(
                    res.get("result", {}).get("runtime_dir")
                    if isinstance(res.get("result"), dict) else None
                ),
            )
            run.add(ev)

        # 3. Re-read session-owned state and recompute the final cartographic
        # review. The review transported in a tool result is never an oracle;
        # it contributes repair history/correlation only after its fingerprint
        # matches the current desired MapSpec.
        cartography = await self._collect_cartographic_evidence(results_by_id)

        # 4. Structured + float metrics (both honest).
        float_metrics = self.evaluate_all(expected_tools, ideal_step_count)
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "evidence": [self._evidence_to_dict(e) for e in run.evidence],
            "metrics": float_metrics,
            "ref_resolutions": {
                ref: {"status": r.status.value, "resolved": r.is_resolved}
                for (sid, ref), r in self._resolved_refs.items()
                if sid == self.session_id
            },
            # V3: 交互段（issued 侧 vs 终态 acked 侧）。
            "interaction": self._interaction_section(),
            "cartography": cartography.to_dict(),
            "success_levels": self._success_levels(run, cartography),
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
            mapspec_fingerprint=rec.get("mapspec_fingerprint"),
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
    def _cartography_check(
        rule: str,
        status: str,
        evidence: Dict[str, Any],
        *,
        message: str,
        severity: str = "error",
    ) -> Dict[str, Any]:
        return {
            "rule": rule,
            "status": status,
            "severity": severity,
            "message": message,
            "evidence_class": "deterministic",
            "evidence": evidence,
            "repairability": "not_repairable",
            "suggested_fix": None,
        }

    async def _collect_cartographic_evidence(
        self, results_by_id: Dict[str, Dict[str, Any]]
    ) -> CartographicReviewEvidence:
        """Build the trusted final cartographic stage from owned state.

        The frontend observation is event-driven: it arrives with the next
        chat request and carries a monotonic per-session sequence. A snapshot
        observed at or before the mutation cannot certify the mutation, even
        if its layer ids happen to look identical.
        """
        evidence = CartographicReviewEvidence(session_id=self.session_id)
        mutation_calls = [
            call for call in self.mapspec_mutations
            if call.get("session_id") == self.session_id
        ]
        if not mutation_calls:
            evidence.termination_reason = "no_mapspec_mutation"
            return evidence

        latest = mutation_calls[-1]
        tool_call_id = str(latest.get("tool_call_id") or "")
        evidence.source_tool_call_id = tool_call_id
        result_entry = results_by_id.get(tool_call_id) or {}
        transported = (
            result_entry.get("result")
            if isinstance(result_entry.get("result"), dict)
            else {}
        )
        evidence.reported_fingerprint = transported.get("mapspec_fingerprint")
        transported_review = transported.get("cartographic_review")
        if isinstance(transported_review, dict):
            attempts = transported_review.get("attempts")
            if isinstance(attempts, list):
                # Lifecycle repair is hard-bounded; retain only that bounded
                # evidence and never copy MapSpec/source payloads here.
                evidence.repair_attempts = attempts[:2]

        if self.cartography_state_reader is None:
            evidence.termination_reason = "state_reader_unavailable"
            return evidence

        evidence.counters = {
            "state_reads": 1,
            "review_invocations": 0,
            "review_cache_hits": 0,
            "metadata_sources": 0,
            "full_data_loads": 0,
        }
        try:
            state = await self.cartography_state_reader(self.session_id)
        except Exception as exc:  # noqa: BLE001 - missing evidence must be visible
            evidence.checks.append(self._cartography_check(
                "CARTOGRAPHY_STATE_READ",
                "not_evaluated",
                {"error_type": type(exc).__name__},
                message="Session-owned cartographic state could not be read.",
            ))
            evidence.termination_reason = "state_reader_error"
            return evidence

        if not isinstance(state, dict) or state.get("session_id") != self.session_id:
            evidence.status = "failed_unrepairable"
            evidence.runtime_status = "fail"
            evidence.checks.append(self._cartography_check(
                "SESSION_OWNERSHIP",
                "fail",
                {
                    "expected_session_id": self.session_id,
                    "observed_session_id": state.get("session_id") if isinstance(state, dict) else None,
                },
                message="Cartographic state does not belong to the evaluated session.",
            ))
            evidence.termination_reason = "session_mismatch"
            return evidence

        mapspec = state.get("mapspec")
        if not isinstance(mapspec, dict):
            evidence.status = "failed_unrepairable"
            evidence.runtime_status = "fail"
            evidence.checks.append(self._cartography_check(
                "RESULT_MAPSPEC_PRESENCE",
                "fail",
                {"mapspec_present": False},
                message="The session has no current MapSpec to review.",
            ))
            evidence.termination_reason = "mapspec_missing"
            return evidence

        current_fingerprint = cartographic_fingerprint(mapspec)
        evidence.mapspec_fingerprint = current_fingerprint
        evidence.counters["metadata_sources"] = len(mapspec.get("sources") or {})
        # A matching headless runtime result is additional heuristic evidence;
        # it never replaces the deterministic desired/runtime checks below.
        for call in reversed(self.tool_calls):
            if (
                call.get("session_id") != self.session_id
                or call.get("name") != "webgis_runtime_validate"
            ):
                continue
            runtime_entry = results_by_id.get(str(call.get("tool_call_id") or "")) or {}
            runtime_result = runtime_entry.get("result")
            if (
                isinstance(runtime_result, dict)
                and runtime_result.get("mapspec_fingerprint") == current_fingerprint
                and isinstance(runtime_result.get("visual_evidence"), dict)
            ):
                evidence.visual_evidence.append(copy.deepcopy(runtime_result["visual_evidence"]))
            break
        cache_key = (self.session_id, current_fingerprint)
        cached_review = self._cartography_review_cache.get(cache_key)
        if cached_review is not None:
            desired = copy.deepcopy(cached_review)
            evidence.counters["review_cache_hits"] = 1
        else:
            try:
                desired = evaluate_cartography_semantics(mapspec).to_dict()
                evidence.counters["review_invocations"] = 1
            except Exception as exc:  # noqa: BLE001
                evidence.checks.append(self._cartography_check(
                    "CARTOGRAPHIC_REVIEW_EXECUTION",
                    "not_evaluated",
                    {"error_type": type(exc).__name__},
                    message="The deterministic cartographic review could not run.",
                ))
                evidence.termination_reason = "review_error"
                return evidence
            self._cartography_review_cache[cache_key] = copy.deepcopy(desired)
            if len(self._cartography_review_cache) > 128:
                oldest = next(iter(self._cartography_review_cache))
                del self._cartography_review_cache[oldest]

        evidence.trusted = True
        evidence.desired_review = desired
        evidence.desired_status = str(desired.get("status") or "not_evaluated")

        if not evidence.reported_fingerprint:
            evidence.status = "not_evaluated"
            evidence.checks.append(self._cartography_check(
                "MAPSPEC_FINGERPRINT_CONVERGENCE",
                "not_evaluated",
                {"reported": None, "current": current_fingerprint},
                message="The mutation did not report a MapSpec fingerprint.",
            ))
            evidence.termination_reason = "mapspec_fingerprint_missing"
            return evidence
        if evidence.reported_fingerprint != current_fingerprint:
            evidence.status = "superseded"
            evidence.checks.append(self._cartography_check(
                "MAPSPEC_FINGERPRINT_CONVERGENCE",
                "fail",
                {
                    "reported": evidence.reported_fingerprint,
                    "current": current_fingerprint,
                },
                message="The mutation review belongs to a different MapSpec generation.",
            ))
            evidence.termination_reason = "stale_mapspec_fingerprint"
            return evidence

        evidence.checks.append(self._cartography_check(
            "MAPSPEC_FINGERPRINT_CONVERGENCE",
            "pass",
            {"reported": evidence.reported_fingerprint, "current": current_fingerprint},
            message="The mutation review matches the current MapSpec generation.",
            severity="info",
        ))

        if evidence.desired_status == "fail":
            repairable = any(
                check.get("status") == "fail"
                and check.get("repairability") in ("auto_safe", "auto_with_semantic_risk")
                for check in desired.get("checks", [])
            )
            evidence.status = "failed_repairable" if repairable else "failed_unrepairable"
            evidence.termination_reason = "desired_quality_failed"
            return evidence
        if evidence.desired_status == "not_evaluated":
            evidence.status = "not_evaluated"
            evidence.termination_reason = "desired_quality_not_evaluated"
            return evidence
        if desired.get("passed") is not True:
            evidence.status = "partial"
            evidence.termination_reason = "desired_quality_evidence_incomplete"
            return evidence

        related_actions = [
            action for action in self._map_action_evidence
            if action.tool_call_id == tool_call_id
        ]
        for action in related_actions:
            if not action.mapspec_fingerprint:
                evidence.status = "not_evaluated"
                evidence.checks.append(self._cartography_check(
                    "MAP_ACTION_GENERATION",
                    "not_evaluated",
                    {
                        "action_id": action.action_id,
                        "action_fingerprint": None,
                        "current_fingerprint": current_fingerprint,
                    },
                    message="The runtime action has no MapSpec generation tag.",
                ))
                evidence.termination_reason = "action_fingerprint_missing"
                return evidence
            if action.mapspec_fingerprint != current_fingerprint:
                evidence.status = "superseded"
                evidence.checks.append(self._cartography_check(
                    "MAP_ACTION_GENERATION",
                    "fail",
                    {
                        "action_id": action.action_id,
                        "action_fingerprint": action.mapspec_fingerprint,
                        "current_fingerprint": current_fingerprint,
                    },
                    message="The runtime action belongs to a stale MapSpec generation.",
                ))
                evidence.termination_reason = "stale_action_fingerprint"
                return evidence
            if action.status in (MapActionStatus.SUPERSEDED, MapActionStatus.CANCELLED):
                evidence.status = "superseded"
                evidence.checks.append(self._cartography_check(
                    "MAP_ACTION_ACK",
                    "fail",
                    {"action_id": action.action_id, "status": action.status.value},
                    message="The runtime action was superseded or cancelled by newer intent.",
                ))
                evidence.termination_reason = "user_or_newer_intent"
                return evidence
            if action.status is MapActionStatus.FAILED:
                evidence.status = "failed_repairable"
                evidence.runtime_status = "fail"
                evidence.checks.append(self._cartography_check(
                    "MAP_ACTION_ACK",
                    "fail",
                    {
                        "action_id": action.action_id,
                        "status": action.status.value,
                        "error": action.error,
                    },
                    message="The frontend rejected the current MapSpec action.",
                ))
                evidence.termination_reason = "runtime_action_failed"
                return evidence
            if action.status is not MapActionStatus.SUCCEEDED:
                evidence.status = "not_evaluated"
                evidence.checks.append(self._cartography_check(
                    "MAP_ACTION_ACK",
                    "not_evaluated",
                    {"action_id": action.action_id, "status": action.status.value},
                    message="The current MapSpec action has no terminal frontend ACK.",
                ))
                evidence.termination_reason = "runtime_action_ack_pending"
                return evidence
            verifiable = _is_verifiable_ack(action)
            converged = _ack_converged(action) if verifiable else None
            evidence.checks.append(self._cartography_check(
                "MAP_ACTION_ACK",
                "pass" if verifiable and converged else "not_evaluated",
                {
                    "action_id": action.action_id,
                    "status": action.status.value,
                    "verifiable": verifiable,
                    "converged": converged,
                },
                message=(
                    "The frontend ACK proves the current action converged."
                    if verifiable and converged else
                    "The frontend ACK does not by itself prove state convergence."
                ),
                severity="info" if verifiable and converged else "warning",
            ))

        map_state = state.get("map_state") if isinstance(state.get("map_state"), dict) else {}
        observation = map_state.get("_cartographic_observation")
        baseline = transported.get("runtime_observation_seq")
        try:
            observed_seq = int(observation.get("sequence")) if isinstance(observation, dict) else -1
            baseline_seq = int(baseline) if baseline is not None else -1
        except (TypeError, ValueError):
            observed_seq, baseline_seq = -1, -1
        observation_owned = (
            isinstance(observation, dict)
            and observation.get("source") == "frontend_runtime"
            and observation.get("session_id") == self.session_id
        )
        if not observation_owned or observed_seq <= baseline_seq:
            evidence.checks.append(self._cartography_check(
                "RUNTIME_OBSERVATION_FRESHNESS",
                "not_evaluated",
                {
                    "source": observation.get("source") if isinstance(observation, dict) else None,
                    "session_id": observation.get("session_id") if isinstance(observation, dict) else None,
                    "observed_sequence": observed_seq,
                    "mutation_baseline_sequence": baseline_seq,
                },
                message="No newer session-owned frontend observation exists for this mutation.",
            ))
            evidence.status = "not_evaluated"
            evidence.termination_reason = "stale_runtime_observation"
            return evidence

        evidence.checks.append(self._cartography_check(
            "RUNTIME_OBSERVATION_FRESHNESS",
            "pass",
            {
                "observed_sequence": observed_seq,
                "mutation_baseline_sequence": baseline_seq,
            },
            message="Frontend state was observed after the MapSpec mutation.",
            severity="info",
        ))

        observed_fingerprint = (
            observation.get("mapspec_fingerprint")
            if isinstance(observation, dict) else None
        )
        if not observed_fingerprint:
            evidence.checks.append(self._cartography_check(
                "RUNTIME_MAPSPEC_GENERATION",
                "not_evaluated",
                {"observed": None, "current": current_fingerprint},
                message="The live runtime observation has no MapSpec generation tag.",
            ))
            evidence.status = "not_evaluated"
            evidence.termination_reason = "runtime_fingerprint_missing"
            return evidence
        if observed_fingerprint != current_fingerprint:
            evidence.checks.append(self._cartography_check(
                "RUNTIME_MAPSPEC_GENERATION",
                "fail",
                {"observed": observed_fingerprint, "current": current_fingerprint},
                message="The live runtime observation belongs to a stale MapSpec generation.",
            ))
            evidence.status = "superseded"
            evidence.termination_reason = "stale_runtime_fingerprint"
            return evidence
        evidence.checks.append(self._cartography_check(
            "RUNTIME_MAPSPEC_GENERATION",
            "pass",
            {"observed": observed_fingerprint, "current": current_fingerprint},
            message="The live runtime observation matches the current MapSpec generation.",
            severity="info",
        ))

        runtime_failed = False
        runtime_incomplete = False
        actual_layers = [
            layer for layer in (observation.get("layers") or [])
            if isinstance(layer, dict) and layer.get("id")
        ]
        sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
        claimed_runtime_layer_ids: set[str] = set()
        expected_layers = [
            layer for layer in (mapspec.get("layers") or [])
            if isinstance(layer, dict) and layer.get("id")
        ]
        for layer in expected_layers:
            layer_id = str(layer["id"])
            expected_refs = _expected_runtime_refs(layer, sources)
            actual = next(
                (
                    candidate for candidate in actual_layers
                    if str(candidate.get("id")) not in claimed_runtime_layer_ids
                    and _runtime_identity_match(layer, sources, candidate) is not None
                ),
                None,
            )
            present = actual is not None
            matched_ref = (
                _runtime_identity_match(layer, sources, actual)
                if actual is not None else None
            )
            evidence.checks.append(self._cartography_check(
                "RUNTIME_RESULT_PRESENCE",
                "pass" if present else "fail",
                {
                    "layer_id": layer_id,
                    "expected_identities": expected_refs,
                    "runtime_layer_present": present,
                    "runtime_layer_id": actual.get("id") if actual is not None else None,
                    "matched_identity": matched_ref,
                },
                message=(
                    "The expected result identity is present in the frontend observation."
                    if present else
                    "No frontend layer carries the expected result identity."
                ),
                severity="info" if present else "error",
            ))
            if not present:
                runtime_failed = True
                continue
            claimed_runtime_layer_ids.add(str(actual["id"]))

            expected_visible = (
                layer.get("visible") is not False
                and (layer.get("layout") or {}).get("visibility") != "none"
            )
            actual_visible = actual.get("visible")
            visibility_evaluated = isinstance(actual_visible, bool)
            visible_match = visibility_evaluated and actual_visible == expected_visible
            evidence.checks.append(self._cartography_check(
                "RUNTIME_RESULT_VISIBILITY",
                (
                    "pass" if visible_match
                    else "fail" if visibility_evaluated
                    else "not_evaluated"
                ),
                {
                    "layer_id": layer_id,
                    "expected_visible": expected_visible,
                    "actual_visible": actual_visible,
                },
                message=(
                    "Runtime layer visibility matches the desired MapSpec."
                    if visible_match else
                    (
                        "Runtime layer visibility differs from the desired MapSpec."
                        if visibility_evaluated
                        else "Runtime layer visibility evidence is missing."
                    )
                ),
                severity="info" if visible_match else "error" if visibility_evaluated else "warning",
            ))
            runtime_failed = runtime_failed or (visibility_evaluated and not visible_match)
            runtime_incomplete = runtime_incomplete or not visibility_evaluated

            expected_opacity = _constant_layer_opacity(layer)
            if expected_opacity is not None:
                actual_opacity = actual.get("opacity")
                opacity_evaluated = (
                    not isinstance(actual_opacity, bool)
                    and isinstance(actual_opacity, (int, float))
                    and math.isfinite(float(actual_opacity))
                )
                opacity_matches = bool(
                    opacity_evaluated
                    and abs(float(actual_opacity) - expected_opacity) <= _FLOAT_EPSILON
                )
                evidence.checks.append(self._cartography_check(
                    "RUNTIME_OPACITY_CONVERGENCE",
                    (
                        "pass" if opacity_matches
                        else "fail" if opacity_evaluated
                        else "not_evaluated"
                    ),
                    {
                        "layer_id": layer_id,
                        "runtime_layer_id": actual.get("id"),
                        "expected_opacity": expected_opacity,
                        "actual_opacity": actual_opacity,
                    },
                    message=(
                        "Runtime opacity matches the desired MapSpec."
                        if opacity_matches else
                        (
                            "Runtime opacity differs from the desired MapSpec."
                            if opacity_evaluated
                            else "Runtime opacity evidence is missing."
                        )
                    ),
                    severity="info" if opacity_matches else "error" if opacity_evaluated else "warning",
                ))
                runtime_failed = runtime_failed or (opacity_evaluated and not opacity_matches)
                runtime_incomplete = runtime_incomplete or not opacity_evaluated

            expected_legend = layer.get("legend_spec")
            if expected_legend is not None:
                legend_match = actual.get("legend_spec") == expected_legend
                evidence.checks.append(self._cartography_check(
                    "RUNTIME_LEGEND_CONVERGENCE",
                    "pass" if legend_match else "fail",
                    {
                        "layer_id": layer_id,
                        "expected_legend_present": True,
                        "actual_legend_present": actual.get("legend_spec") is not None,
                        "legend_matches": legend_match,
                    },
                    message=(
                        "Runtime legend matches the authoritative MapSpec legend."
                        if legend_match else
                        "Runtime legend is missing or stale relative to MapSpec."
                    ),
                    severity="info" if legend_match else "error",
                ))
                runtime_failed = runtime_failed or not legend_match

            style_converged = actual.get("style_converged")
            style_evaluated = isinstance(style_converged, bool)
            evidence.checks.append(self._cartography_check(
                "RUNTIME_STYLE_CONVERGENCE",
                (
                    "pass" if style_converged is True
                    else "fail" if style_evaluated
                    else "not_evaluated"
                ),
                {
                    "layer_id": layer_id,
                    "runtime_layer_id": actual.get("id"),
                    "style_converged": style_converged,
                    "runtime_layer_count": actual.get("runtime_layer_count"),
                },
                message=(
                    "Live MapLibre style matches the reconciled desired layer."
                    if style_converged is True
                    else (
                        "Live MapLibre style differs from the reconciled desired layer."
                        if style_evaluated
                        else "Live MapLibre style convergence was not observed."
                    )
                ),
                severity="info" if style_converged is True else "error" if style_evaluated else "warning",
            ))
            runtime_failed = runtime_failed or style_converged is False
            runtime_incomplete = runtime_incomplete or not style_evaluated

        desired_view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
        if _has_camera_state(desired_view):
            # A camera command can settle just after the layer reconcile
            # observation. Its terminal ACK carries a live MapLibre snapshot,
            # so prefer that newer exact-action evidence over the earlier
            # observation while still requiring the latter for layer/style
            # convergence.
            acknowledged_view = next(
                (
                    action.actual for action in reversed(related_actions)
                    if action.status is MapActionStatus.SUCCEEDED
                    and _has_camera_state(action.actual)
                ),
                None,
            )
            actual_view = (
                acknowledged_view
                if acknowledged_view is not None
                else observation.get("viewport")
                if isinstance(observation.get("viewport"), dict)
                else {}
            )
            camera_matches = _camera_match(desired_view, actual_view)
            evidence.checks.append(self._cartography_check(
                "RUNTIME_VIEW_CONVERGENCE",
                "pass" if camera_matches else "fail",
                {"requested": desired_view, "actual": actual_view},
                message=(
                    "Runtime camera converged to the desired MapSpec view."
                    if camera_matches else
                    "Runtime camera has not converged to the desired MapSpec view."
                ),
                severity="info" if camera_matches else "error",
            ))
            runtime_failed = runtime_failed or not camera_matches

        if runtime_failed:
            evidence.status = "failed_repairable"
            evidence.runtime_status = "fail"
            evidence.termination_reason = "runtime_state_mismatch"
        elif runtime_incomplete:
            evidence.status = "partial"
            evidence.runtime_status = "not_evaluated"
            evidence.termination_reason = "runtime_evidence_incomplete"
        else:
            evidence.status = (
                "passed_with_warnings" if evidence.desired_status == "warning" else "passed"
            )
            evidence.runtime_status = "pass"
            evidence.termination_reason = "quality_converged"
        return evidence

    @staticmethod
    def _success_levels(
        run: EvaluationRun, cartography: CartographicReviewEvidence
    ) -> Dict[str, Dict[str, Any]]:
        execution_failed = any(item.is_error for item in run.evidence)
        execution_status = "fail" if execution_failed else ("pass" if run.evidence else "not_evaluated")
        structural_status = (
            "pass" if cartography.desired_status in ("pass", "warning")
            else cartography.desired_status
        )
        return {
            "execution_validity": {"level": 1, "status": execution_status},
            "map_state_validity": {"level": 2, "status": cartography.runtime_status},
            "cartographic_structural_validity": {"level": 3, "status": structural_status},
            "cartographic_quality": {
                "level": 4,
                "status": "pass" if cartography.passed else cartography.status,
            },
            # No structured visual/goal oracle is installed. Missing evidence
            # stays explicit rather than inheriting L4 success.
            "goal_satisfaction": {"level": 5, "status": "not_evaluated"},
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
            "mapspec_fingerprint": a.mapspec_fingerprint,
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
        invoked_tools = [
            tc["name"] for tc in self.tool_calls
            if tc.get("session_id") == self.session_id
        ]
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
        mutations = [
            m for m in self.mapspec_mutations
            if m.get("session_id") == self.session_id
        ]
        if not mutations:
            return 0.0
        valid_count = sum(1 for m in mutations if m.get("is_valid", False))
        validity = (valid_count / len(mutations)) * 100.0
        return min(100.0, max(0.0, validity))

    def compute_cursor_resolution_rate(self) -> float:
        """% of refs that genuinely RESOLVED against the SessionStore.

        V2: syntactic-only refs (no resolver run, or resolver returned not-found /
        wrong-session) do NOT count. No refs recorded → 0.0 (no evidence), not 100.0.
        """
        cursors = [
            c for c in self.ref_cursors
            if c.get("session_id") == self.session_id
        ]
        if not cursors:
            return 0.0
        resolved_count = sum(1 for c in cursors if c.get("is_resolved", False))
        rate = (resolved_count / len(cursors)) * 100.0
        return min(100.0, max(0.0, rate))

    def compute_step_efficiency(self, ideal_step_count: int) -> float:
        actual_step_count = sum(
            1 for tc in self.tool_calls
            if tc.get("session_id") == self.session_id
        )
        if actual_step_count == 0:
            return 100.0 if ideal_step_count == 0 else 0.0
        efficiency = (ideal_step_count / actual_step_count) * 100.0
        return min(100.0, max(0.0, efficiency))

    def compute_error_recovery_rate(self) -> float:
        total_exceptions = sum(
            1 for e in self.exceptions
            if e.get("session_id") == self.session_id
        )
        if total_exceptions == 0:
            return 100.0
        recovered = self._recovered_exceptions_count.get(self.session_id, 0)
        rate = (recovered / total_exceptions) * 100.0
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

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Production telemetry digest (consumed by /metrics/digest).

        OBSERVABILITY (false-success fix): a rate with NO positive evidence is
        emitted as ``null`` (not 100.0), and an additive ``evaluated`` map says
        which rates actually had evidence. "missing evidence ≠ success" — a null
        rate cannot be read as 100% by any programmatic consumer (a gate, an
        alert). The legacy compute_* still return 100.0 for the gate-facing
        evaluate_all path; this production surface overrides to null.
        """
        exc_count = sum(1 for e in self.exceptions if e.get("session_id") == self.session_id)
        mutations = [m for m in self.mapspec_mutations if m.get("session_id") == self.session_id]
        cursors = [c for c in self.ref_cursors if c.get("session_id") == self.session_id]
        eval_mapspec = len(mutations) > 0
        eval_cursor = len(cursors) > 0
        eval_recovery = exc_count > 0
        return {
            "rates": {
                # null when unevaluated; round when there is evidence.
                "MapSpecValidity": round(self.compute_mapspec_validity(), 2) if eval_mapspec else None,
                "CursorResolutionRate": round(self.compute_cursor_resolution_rate(), 2) if eval_cursor else None,
                "ErrorRecoveryRate": round(self.compute_error_recovery_rate(), 2) if eval_recovery else None,
            },
            "evaluated": {
                "MapSpecValidity": eval_mapspec,
                "CursorResolutionRate": eval_cursor,
                "ErrorRecoveryRate": eval_recovery,
            },
            "counts": {
                "ToolCallsCount": float(sum(
                    1 for tc in self.tool_calls
                    if tc.get("session_id") == self.session_id
                )),
                "ExceptionsCount": float(exc_count),
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
