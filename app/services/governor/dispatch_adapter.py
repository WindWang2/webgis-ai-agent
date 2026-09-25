"""工具派发面的 governor 适配器（D3 唯一强集成点的逻辑载体）。

本模块把 :meth:`ToolDispatchService.dispatch` 的调用包装成 governor 管线
（估算 → 准入 → 预留 → 执行 → 记账/归还）。**全部逻辑住在 governor 包内**
—— 对 ``tool_dispatch_service.py`` 的侵入压缩到几行调用（该文件不属于任何
并行 PR 热区，recon §d）。

- 工具 → subsystem/resource_class：名字模式词表 + registry metadata 的
  ``cost`` 档（light/medium/heavy，registry.py:136 同源）；
- kill-switch：``GOVERNOR_TOOL_SURFACE=0`` → 整体直通（微升级路径）；
- 适配器绝不改变结果语义：governor 任何异常都 fail-open（内部再兜一层）。

ADR-0213 增量（R5/R6）：
- **actual 回填**：complete 时回填廉价实际用量（wall + O(1) 计数/长度字段）
  并记入有界 ``CalibrationStore``（只观测，绝不回写运行时先验）；
- **重试入账**：短窗内同 (session, tool) 重复派发按 attempt>1 计，经
  governor RetryBudget 咨询 + 实扣（R6 统一预算；窗口有界；预算拒绝是
  诚实 reject payload，不伪装成功）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.services.governor.calibration import get_calibration_store
from app.services.governor.config import GovernorMode
from app.services.governor.contract import (
    Dimension,
    ResourceClass,
    ResourceDemand,
    ResourceUsage,
    RetryClass,
    Subsystem,
)
from app.services.governor.estimation import DfCostView, estimate_for_tool

logger = logging.getLogger(__name__)

# ── R6：短窗失败感知（同 (session, tool) 重复派发 → attempt>1 入预算）────
_RETRY_WINDOW_S = 120.0
_MAX_TRACKED = 2048
_TRACKED: Dict[Tuple[str, str], List[float]] = {}
_TRACKED_LOCK = threading.Lock()

#: 结果里 O(1) 可读的特征计数键（actual 回填；绝不序列化大 payload）
_COUNT_KEYS = ("feature_count", "featureCount", "count", "total",
               "row_count", "rows")

#: render 细化通道的 subsystem 集（F13/ADR-0214 D2）：这些工具的执行
#: 成本由「当前地图的渲染面形状」主导 —— 当前 MapSpec 投影作为细化
#: 证据供给 estimate_for_tool（R13 seam；#1408 df_cost 通道同款模式）。
_RENDER_REFINED_SUBSYSTEMS = frozenset({
    Subsystem.RENDER,
    Subsystem.BROWSER,
    Subsystem.EXPORT,
})


async def _project_render_input(session_id: str):
    """会话 MapSpec → RenderWorkInput（fail-open：任何异常 → None）。

    细化是增值面：投影缺席时准入回退档位先验，绝不阻断派发。
    """
    try:
        from app.services.governor.render_projection import (
            render_input_for_session,
        )

        return await render_input_for_session(session_id)
    except Exception:  # noqa: BLE001 — projection is advisory
        return None


def _recent_attempt(session_id: str, tool_name: str) -> int:
    """短窗内存在未清失败 → 返回本次 attempt（>1）；否则 1。

    有界：表超限先清过期窗口，再按最旧时间驱逐（常数内存）。
    """
    key = ((session_id or "")[:64], (tool_name or "")[:128])
    now = time.monotonic()
    with _TRACKED_LOCK:
        entry = _TRACKED.get(key)
        if entry is None or now - entry[1] > _RETRY_WINDOW_S:
            return 1
        return int(entry[0]) + 1


def _note_outcome(session_id: str, tool_name: str, status: str) -> None:
    """执行结果回写失败计数（成功/取消清零；失败 +1 并续窗）。"""
    key = ((session_id or "")[:64], (tool_name or "")[:128])
    now = time.monotonic()
    with _TRACKED_LOCK:
        if status in ("completed", "cancelled"):
            _TRACKED.pop(key, None)
        else:
            entry = _TRACKED.get(key)
            count = int(entry[0]) + 1 if (
                entry is not None and now - entry[1] <= _RETRY_WINDOW_S
            ) else 1
            _TRACKED[key] = [count, now]
        if len(_TRACKED) > _MAX_TRACKED:
            stale = [k for k, v in _TRACKED.items()
                     if now - v[1] > _RETRY_WINDOW_S]
            for k in stale:
                _TRACKED.pop(k, None)
            if len(_TRACKED) > _MAX_TRACKED:
                oldest = sorted(_TRACKED, key=lambda k: _TRACKED[k][1])
                for k in oldest[:len(_TRACKED) - _MAX_TRACKED]:
                    _TRACKED.pop(k, None)

#: 工具名模式 → (subsystem, resource_class)（封闭词表；先命中先得）
_TOOL_PATTERNS: tuple = (
    ("raster", Subsystem.RASTER_COMPUTE, ResourceClass.RASTER),
    ("ndvi", Subsystem.RASTER_COMPUTE, ResourceClass.RASTER),
    ("remote_sense", Subsystem.REMOTE_SENSING, ResourceClass.RASTER),
    ("stac", Subsystem.REMOTE_SENSING, ResourceClass.RASTER),
    ("export", Subsystem.EXPORT, ResourceClass.EXPORT),
    ("pdf", Subsystem.EXPORT, ResourceClass.EXPORT),
    ("screenshot", Subsystem.BROWSER, ResourceClass.BROWSER),
    ("golden", Subsystem.BROWSER, ResourceClass.BROWSER),
    ("render", Subsystem.RENDER, ResourceClass.BROWSER),
    ("vlm", Subsystem.VLM_JUDGE, ResourceClass.LLM),
    ("visual_judge", Subsystem.VLM_JUDGE, ResourceClass.LLM),
    ("search", Subsystem.DATA_FABRIC, ResourceClass.MEDIUM),
    ("download", Subsystem.DOWNLOAD, ResourceClass.MEDIUM),
    ("acquire", Subsystem.DATA_FABRIC, ResourceClass.MEDIUM),
    ("query", Subsystem.DATA_FABRIC, ResourceClass.MEDIUM),
)



def _project_df_cost(args: Dict[str, Any]) -> Optional[DfCostView]:
    """Best-effort Data Fabric cost projection from tool args (#1408).

    Fail-open: any import/arg parse error returns None so admission still
    uses class priors. Consumes planning/cost_model.estimate_cost.
    """
    try:
        from app.services.data_fabric.planning.cost_model import estimate_cost
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(args, dict):
        return None
    try:
        limit = None
        for key in ("limit", "feature_limit", "max_features", "maxfeat", "k"):
            if key in args and args[key] is not None:
                limit = int(args[key])
                break
        feature_count = None
        for key in ("feature_count", "estimated_rows", "rows"):
            if key in args and args[key] is not None:
                feature_count = int(args[key])
                break
        bbox = args.get("bbox") or args.get("extent") or args.get("bounds")
        if isinstance(bbox, dict):
            bbox = [bbox.get("west"), bbox.get("south"),
                    bbox.get("east"), bbox.get("north")]
        protocol = str(args.get("protocol") or args.get("provider")
                       or args.get("source_type") or "ogc_api")
        fields = int(args.get("fields") or args.get("field_count") or 8)
        geometry_type = str(args.get("geometry_type")
                            or args.get("geom_type") or "point")
        local = bool(args.get("local") or protocol in (
            "geopackage", "local_file", "cog"))
        est = estimate_cost(
            feature_count=feature_count,
            fields=fields,
            geometry_type=geometry_type,
            protocol=protocol,
            request_bbox=bbox if isinstance(bbox, (list, tuple)) else None,
            limit=limit,
            local=local,
        )
        latency_s = None
        if getattr(est, "latency_ms", None) is not None:
            latency_s = float(est.latency_ms) / 1000.0
        return DfCostView(
            rows=float(est.rows) if est.rows is not None else None,
            bytes=float(est.bytes) if est.bytes is not None else None,
            latency_s=latency_s,
            source="df.cost_model.v1",
        )
    except Exception:  # noqa: BLE001 — projection is advisory
        return None


def classify_tool(tool_name: str, cost: str = "light") -> tuple:
    """工具名 + cost 档 → (Subsystem, ResourceClass)（确定性）。

    已知权衡（review P3，有意保留）：**名字模式优先于 cost 档**。存量 156
    个工具的注册 cost 几乎全为默认 light —— 若 cost 优先，export/browser/
    raster 通道记账会对真实工具面整体失效。代价是形如
    ``query_export_summary`` 的未来轻工具会被误路由进 export 串行通道；
    校准脚本（R17）的通道水位证据是发现该类误路由的第一信号，届时再引入
    cost 覆盖词表。
    """
    name = (tool_name or "").lower()
    for pattern, subsystem, rclass in _TOOL_PATTERNS:
        if pattern in name:
            return subsystem, rclass
    if cost == "heavy":
        return Subsystem.TOOL_DISPATCH, ResourceClass.HEAVY
    if cost == "medium":
        return Subsystem.TOOL_DISPATCH, ResourceClass.MEDIUM
    return Subsystem.TOOL_DISPATCH, ResourceClass.LIGHT


def surface_enabled() -> bool:
    """kill-switch：GOVERNOR_TOOL_SURFACE=0 → 工具面 governor 直通。"""
    return (os.getenv("GOVERNOR_TOOL_SURFACE", "1") != "0")


class GovernorDispatchAdapter:
    """一次工具调用的 governor 管线包装。"""

    def __init__(self, governor, metadata_fn: Optional[Callable[[str], dict]] = None):
        self._governor = governor
        self._metadata_fn = metadata_fn or (lambda _name: {"cost": "light"})

    async def run(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        session_id: str,
        turn_id: str = "",
        dispatch_inner: Callable[[], Awaitable[Any]],
    ) -> Any:
        """admit → execute → complete；返回 dispatch_inner 的原结果。

        governor 决策为 reject/degrade 且 **enforce 模式** 时，不执行原
        调用，返回一个诚实描述资源裁决的降级 payload（结构对齐工具结果
        惯例 ``{"success": False, ...}``，LLM 可读、可行动）。

        **observe 模式端到端语义（review P0 修复）**：facade 在 observe 下
        照常建立 reservation/ticket 并保持决策原值（可能是 reject）——
        本层必须执行且**必须 complete()**，否则既违背回滚语义又永久泄漏
        通道槽位。拒绝判定因此不能只看 ``decision.allowed``（它不含模式），
        必须显式检查 enforce。
        """
        governor = self._governor
        if not surface_enabled() or governor is None:
            return await dispatch_inner()

        started = time.monotonic()
        # R6：短窗内重复派发按重试计（governor RetryBudget 咨询 + 实扣）
        attempt = _recent_attempt(session_id, tool_name)
        # F13/ADR-0214 D2：render 族工具喂当前地图渲染面投影（细化估工；
        # fail-open —— 投影缺席回退档位先验）。
        render_input = None
        try:
            _subsystem, _rclass = classify_tool(tool_name, "")
            if _subsystem in _RENDER_REFINED_SUBSYSTEMS:
                render_input = await _project_render_input(session_id)
        except Exception:  # noqa: BLE001 — 细化通道绝不影响派发
            render_input = None
        demand = self._build_demand(
            tool_name, tool_args, session_id, turn_id,
            attempt=attempt,
            retry_class=(RetryClass.TOOL if attempt > 1 else None),
            render_input=render_input,
        )
        try:
            decision, reservation, ticket = await governor.admit_and_reserve(demand)
        except Exception:  # noqa: BLE001 — 双保险 fail-open
            # 注意：fail-open 直通不进 finally 记账面 —— 该次执行若失败
            # 不进重试窗口（R6 记账盲区，review P2-6；fail-open 纪律优先）
            logger.exception("[resource-governor] adapter admit failed; fail-open")
            return await dispatch_inner()

        enforce = getattr(governor.config, "mode", None) is GovernorMode.ENFORCE
        if not decision.allowed and enforce:
            # enforce 拒绝：facade 未建 reservation（r/t 为 None）——无槽位
            # 需要归还；observe 下 r/t 非 None，落入下方执行 + complete 路径。
            return self._rejection_payload(tool_name, decision)

        if attempt > 1 and reservation is not None:
            # 重试实扣（retry_allowed 已由 facade 咨询过；记账故障不阻断）
            try:
                governor.retries.charge(session_id, RetryClass.TOOL)
            except Exception:  # noqa: BLE001
                logger.exception("[resource-governor] retry charge failed")

        result: Any = None
        status = "failed"
        try:
            result = await dispatch_inner()
            status = self._status_of(result)
        except BaseException as exc:  # noqa: BLE001 — RUN-10: hard cancel must release
            # ``governor.complete`` releases ledger + channel ticket. The old
            # ``except Exception`` let ``CancelledError`` escape with a live
            # reservation, permanently saturating the heavy channel
            # (cancel_session/close_session have no production callers).
            status = (
                "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "failed"
            )
            raise
        finally:
            wall = time.monotonic() - started
            usage = ResourceUsage(
                session_id=session_id,
                tool_name=tool_name,
                subsystem=demand.subsystem,
                status=status,
                wall_time_s=wall,
                retries=max(0, attempt - 1),
            )
            usage.dims.update(self._cheap_actuals(result if status != "failed"
                                                  else None))
            try:
                await governor.complete(
                    reservation, ticket,
                    usage=usage,
                    actual={Dimension.WALL_TIME_S: wall,
                            **{d: float(v) for d, v in usage.dims.items()}},
                    estimate=demand.estimate,
                )
            except Exception:  # noqa: BLE001 — 记账故障绝不影响结果返回
                logger.exception("[resource-governor] complete accounting failed")
            # R5：actual → 有界校准统计（只观测；异常绝不外泄）
            try:
                get_calibration_store().record_usage(
                    f"{demand.subsystem.value}:{tool_name}"[:128],
                    usage, demand.estimate)
            except Exception:  # noqa: BLE001
                logger.exception("[resource-governor] calibration record failed")
            _note_outcome(session_id, tool_name, status)
        return result

    # ── 内部 ─────────────────────────────────────────────────────────

    def _build_demand(self, tool_name: str, args: Dict[str, Any],
                      session_id: str, turn_id: str, *,
                      attempt: int = 1,
                      retry_class: Optional[RetryClass] = None,
                      render_input=None) -> ResourceDemand:
        meta = self._metadata_fn(tool_name) or {}
        cost = str(meta.get("cost", "light"))
        subsystem, rclass = classify_tool(tool_name, cost)
        # #1408: consume DF cost model for data-fetch tools (estimation
        # already accepts df_cost; adapter previously never supplied it).
        df_cost = (
            _project_df_cost(args)
            if subsystem in (Subsystem.DATA_FABRIC, Subsystem.DOWNLOAD)
            else None
        )
        estimate = estimate_for_tool(
            tool_name, tool_class=cost, subsystem=subsystem, args=args,
            df_cost=df_cost,
            render_input=render_input,
        )
        est = estimate.model_copy(update={"resource_class": rclass})
        return ResourceDemand(
            session_id=session_id or "",
            turn_id=turn_id or "",
            subsystem=subsystem,
            tool_name=tool_name,
            estimate=est,
            attempt=max(1, int(attempt)),
            retry_class=retry_class,
        )

    @staticmethod
    def _status_of(result: Any) -> str:
        try:
            if getattr(result, "status", "") == "repeated":
                return "completed"
            raw = getattr(result, "raw_result", None)
            if isinstance(raw, dict) and raw.get("success") is False:
                return "failed"
        except Exception:  # noqa: BLE001
            pass
        return "completed"

    @staticmethod
    def _cheap_actuals(result: Any) -> Dict[Dimension, float]:
        """结果 → 廉价 actual 维（只 O(1) 访问，绝不序列化/遍历大 payload）。

        - 特征计数：raw_result 顶层声明计数键（feature_count/count/...）；
        - 输出体量：仅当顶层值本身是 str/bytes 时取 len（data fabric /
          download 面，输出≈抓取载荷）；其余结构跳过 —— 输出体量校准
          的深化属 follow-up，这里宁可少测不可阻塞事件循环。
        """
        actuals: Dict[Dimension, float] = {}
        try:
            raw = getattr(result, "raw_result", None)
            if not isinstance(raw, dict):
                return actuals
            for key in _COUNT_KEYS:
                v = raw.get(key)
                if isinstance(v, (int, float)) and v >= 0:
                    actuals[Dimension.FEATURE_COUNT] = float(v)
                    break
            payload = raw.get("data") if "data" in raw else raw.get("result")
            if isinstance(payload, (str, bytes)):
                actuals[Dimension.NETWORK_BYTES] = float(len(payload))
        except Exception:  # noqa: BLE001 — actual 是观测面，绝不抛
            pass
        return actuals

    @staticmethod
    def _rejection_payload(tool_name: str, decision) -> Dict[str, Any]:
        """enforce 模式下拒绝执行时的诚实 payload（不伪装成功）。

        形状对齐派发链已识别的错误族（``{"error": <str>, "success": False,
        "code": ...}``，tool_dispatch_service 的 is_error_like_result 折叠
        路径）—— 保证拒绝被当作「未执行的失败」处理：dedup 槽位释放、
        诚实重试，绝不被「已成功执行」谎言拦截。
        """
        degrade = decision.degrade_hint or {}
        reasons_txt = "; ".join(decision.reasons) or "unspecified"
        suggestion_txt = (" / ".join(decision.suggestions[:2])
                          if decision.suggestions else "")
        return {
            "success": False,
            "error": (
                f"resource governor blocked '{tool_name}': "
                f"decision={decision.decision.value} ({reasons_txt})"
            ),
            "code": f"RESOURCE_GOVERNOR_{decision.decision.value.upper()}",
            "correction_hint": suggestion_txt or (
                "retry with narrower scope, or cancel other in-flight work"),
            "governor": {
                "decision": decision.decision.value,
                "reasons": list(decision.reasons),
                "suggestions": list(decision.suggestions),
                "degrade_options": degrade.get("steps", []),
                "degrade_semantics": degrade.get("best_semantics"),
                "adr": "ADR-0182",
            },
        }


__all__ = [
    "GovernorDispatchAdapter",
    "classify_tool",
    "surface_enabled",
]
