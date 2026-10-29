"""AnalysisWorkflow 执行器（goal-02 M4/M7）—— 真实 dispatch 的薄执行循环。

执行真相（先例 run_3d_analysis / scene_orchestration_tools.py:739）：
每步经 ``registry.dispatch(tool, args, session_id)``（Pydantic 校验/别名
归一/指标/缓存/防御闸全继承——本层零旁路）。

诚实语义：

- 工具返回 ``success=false`` / 异常 → 步骤 failed + 下游 blocked，
  工作流 verdict 绝不 pass（工具失败不得得到 pass verdict）；
- **幂等**：satisfied 且指纹未变的步骤直接复用旧 envelope——不重新
  dispatch、不重复 store（恢复/重跑不重复写产物）；
- **预算**：角色 ref 水合前查 descriptor feature_count，超限 typed
  blocked（AWF_BUDGET_EXCEEDED）——大数据绝不无界进内存/上下文；
- **取消**：cancel_token 在步骤间与水合前检查；OperationCancelled →
  步骤 cancelled 终态（resume 拒绝 cancelled 语义由调用方持有）；
- **ref 纪律**：步骤 FC 产物由本层经 session_data.store(prefix="geojson")
  铸 ref（内层无外层缝可托付——与 dispatch 缝同族前缀，非第二协议）；
  工具结果里已有 ref: 的（ref:raster 等）直接沿用不重铸。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.lib.cancellation import OperationCancelled
from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DATA_ROLE_MISSING,
    AWF_TOOL_UNREGISTERED,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    AnalysisArtifactEnvelope,
)
from app.services.gis_harness.analysis_workflows.evidence import (
    StepReceipt,
    build_step_receipt,
)
from app.services.gis_harness.analysis_workflows.recompute import (
    ref_identity,
    step_fingerprint,
)
from app.services.gis_harness.analysis_workflows.state import (
    WorkflowRunState,
    verdict_of,
)

logger = logging.getLogger(__name__)

#: 单步骤输入行数预算（任务验收：>10 万 features 必须限额/阻断，不无界加载）。
DEFAULT_MAX_FEATURES = 100_000

#: envelope stats 截断键数（有界）。
MAX_ENVELOPE_STATS_KEYS = 12


def _extract_fc(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从工具结果提取主 FeatureCollection（data/geojson 键；无则 None）。"""
    for key in ("data", "geojson"):
        candidate = result.get(key)
        if (isinstance(candidate, dict)
                and candidate.get("type") == "FeatureCollection"):
            return candidate
    return None


def _extract_ref(result: Dict[str, Any]) -> str:
    """工具结果自带 ref: 时直接沿用（ref:raster 等——不重铸）。"""
    for key in ("result_ref", "ref", "output_ref"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("ref:"):
            return value
    return ""


def _feature_count(fc: Dict[str, Any]) -> int:
    try:
        return len(fc.get("features") or [])
    except (TypeError, AttributeError):
        return 0


class WorkflowExecutor:
    """执行器（依赖注入：registry / session_data / persist 回调）。"""

    def __init__(
        self,
        *,
        registry: Any,
        session_data: Any = None,
        max_features: int = DEFAULT_MAX_FEATURES,
        persist: Optional[Callable[[WorkflowRunState], Any]] = None,
        cancel_token: Any = None,
        current_turn: int = 0,
        dispatch: Optional[Callable[[str, Dict[str, Any], str],
                                    Any]] = None,
    ):
        self._registry = registry
        self._sd = session_data
        self._max_features = max_features
        self._persist = persist
        self._cancel_token = cancel_token
        self._turn = current_turn
        self._dispatch_fn = dispatch
        #: 本次运行内步骤产物（内存 FC，供 step: 下游直传不重复水合）
        self._step_payloads: Dict[str, Dict[str, Any]] = {}

    # ── 输入解析 ─────────────────────────────────────────────────────────

    async def _role_ref(self, contract: AnalysisWorkflowContract,
                        role: str) -> Optional[str]:
        binding = contract.data_contracts.get(role)
        if binding is None or binding.missing or not binding.ref:
            return None
        return binding.ref

    async def _hydrate(self, session_id: str, ref: str,
                       param: str, step_id: str) -> Tuple[
            Optional[Dict[str, Any]], str, List[str]]:
        """ref → FC 载荷（预算门在 descriptor 层先行——不盲目加载）。

        返回 (payload_or_None, identity, warnings)。
        """
        warnings: List[str] = []
        descriptor = None
        if self._sd is not None:
            try:
                descriptor = await self._sd.get_ref_descriptor(
                    session_id, ref)
            except Exception:  # noqa: BLE001 — descriptor 是优化面，失败不阻断
                descriptor = None
        if descriptor is not None:
            count = 0
            try:
                count = int(descriptor.get("feature_count", 0) or 0)
            except (TypeError, ValueError):
                count = 0
            if count > self._max_features:
                warnings.append(
                    f"输入 {param} 行数 {count} 超预算 {self._max_features}"
                    f" [AWF_BUDGET_EXCEEDED]")
                return None, "", warnings
        identity = ref_identity(ref, (descriptor or {}).get("content_revision"))
        if self._sd is None:
            return None, identity, warnings
        payload = None
        try:
            payload = await self._sd.get(session_id, ref)
        except Exception as exc:  # noqa: BLE001 — ref 死亡=typed 缺失
            warnings.append(f"输入 {param} 的 ref 水合失败：{exc}")
            return None, identity, warnings
        if not isinstance(payload, dict):
            warnings.append(f"输入 {param} 的 ref 载荷不是对象")
            return None, identity, warnings
        fc_count = _feature_count(payload)
        if fc_count > self._max_features:
            warnings.append(
                f"输入 {param} 实测行数 {fc_count} 超预算"
                f" {self._max_features} [AWF_BUDGET_EXCEEDED]")
            return None, identity, warnings
        return payload, identity, warnings

    async def _resolve_inputs(
        self, session_id: str, contract: AnalysisWorkflowContract,
        step: AnalysisStepSpec, state: WorkflowRunState,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, str], List[str]]:
        """解析 role:/step: 输入 → 工具实参（含身份集）。

        返回 (args_or_None, identities, warnings)；args=None 表示 typed
        不可执行（warnings 带原因码）。
        """
        args: Dict[str, Any] = dict(step.step_params)
        identities: Dict[str, str] = {}
        warnings: List[str] = []
        for param, source in step.role_inputs.items():
            if source.startswith("role:"):
                role = source.removeprefix("role:")
                ref = await self._role_ref(contract, role)
                if ref is None:
                    warnings.append(
                        f"数据角色 {role} 未绑定 [AWF_DATA_ROLE_MISSING:{role.upper()}]")
                    return None, identities, warnings
                payload, ident, hydrate_warnings = await self._hydrate(
                    session_id, ref, param, step.step_id)
                warnings.extend(hydrate_warnings)
                if payload is None and hydrate_warnings:
                    return None, identities, warnings
                args[param] = payload
                identities[param] = ident
            else:  # step:<upstream>
                upstream = source.removeprefix("step:")
                entry = state.steps.get(upstream)
                if entry is None or entry.state != "satisfied":
                    warnings.append(
                        f"上游步骤 {upstream} 未满足（{entry.state if entry else 'absent'}）")
                    return None, identities, warnings
                payload = self._step_payloads.get(upstream)
                if payload is None:
                    envelope = state.envelopes.get(upstream)
                    if envelope and envelope.ref and self._sd is not None:
                        payload = await self._sd.get(session_id, envelope.ref)
                    if not isinstance(payload, dict):
                        warnings.append(
                            f"上游步骤 {upstream} 的产物不可水合（ref 失效）")
                        return None, identities, warnings
                    self._step_payloads[upstream] = payload
                args[param] = payload
                identities[param] = f"fp:{entry.fingerprint}"
        return args, identities, warnings

    # ── 执行 ─────────────────────────────────────────────────────────────

    async def _dispatch(self, tool_name: str, args: Dict[str, Any],
                        session_id: str) -> Dict[str, Any]:
        """单入口分发：注入的 dispatch 可调用（生产=ToolDispatchService，
        含 capability 记账/guardrail/复用/ref 卸载）优先；回落 registry
        （直调语义——单测用）。

        返回统一形状 ``{"success", "data"?, "geojson"?, "stats"?,
        "scientific_evidence"?, "warnings"?, "dispatch_ref"?}``；
        ``dispatch_ref`` = 分发缝已铸造的 ref（复用，不重铸）。
        """
        if self._dispatch_fn is not None:
            outcome = await self._dispatch_fn(tool_name, args, session_id)
            # ToolDispatchResult → dict（status 判别式）
            status = getattr(outcome, "status", None)
            if status is not None:
                raw = getattr(outcome, "raw_result", None)
                if str(status) == "error":
                    msg = str(getattr(outcome, "error_msg", "") or
                              "dispatch error")[:300]
                    return {"success": False, "message": msg,
                            "reason_code": "TOOL_DISPATCH_ERROR"}
                payload = raw if isinstance(raw, dict) else {}
                out = dict(payload) if payload else {"success": True}
                # 分发缝铸 ref 后剥内联 FC（slim 纪律）——ref 与行数事实
                # 从 result_ref / geojson_ref / ref_descriptor 透传。
                ref = (getattr(outcome, "geojson_ref", None)
                       or str(payload.get("result_ref", "") or ""))
                if ref:
                    out.setdefault("dispatch_ref", ref)
                desc = getattr(outcome, "ref_descriptor", None)
                if isinstance(desc, dict):
                    try:
                        out.setdefault(
                            "dispatch_feature_count",
                            int(desc.get("feature_count", 0) or 0))
                    except (TypeError, ValueError):
                        pass
                return out
            return outcome if isinstance(outcome, dict) else {
                "success": False, "message": "dispatch 返回形状异常"}
        result = await self._registry.dispatch(
            tool_name, args, session_id=session_id)
        return result if isinstance(result, dict) else {
            "success": False, "message": "dispatch 返回形状异常"}

    def _cancelled(self) -> bool:
        token = self._cancel_token
        if token is None:
            return False
        try:
            return bool(token.cancelled or token.is_cancelled())
        except AttributeError:
            try:
                return bool(token.cancelled)
            except Exception:  # noqa: BLE001
                return False

    async def _run_step(
        self, session_id: str, contract: AnalysisWorkflowContract,
        step: AnalysisStepSpec, state: WorkflowRunState,
    ) -> None:
        entry = state.steps[step.step_id]
        if entry.state in ("blocked", "skipped"):
            return
        if self._cancelled():
            entry.state = "cancelled"
            entry.reason_code = "CANCELLED_BEFORE_START"
            entry.touch()
            state.log("step_cancelled", step=step.step_id)
            return
        if not self._registry.has(step.tool_name):
            entry.state = "blocked"
            entry.reason_code = f"{AWF_TOOL_UNREGISTERED}:{step.tool_name}"
            entry.message = f"工具 {step.tool_name} 未注册"
            entry.touch()
            state.log("step_blocked", step=step.step_id,
                      code=entry.reason_code)
            return
        args, identities, warnings = await self._resolve_inputs(
            session_id, contract, step, state)
        if args is None:
            reason = next((w.split("[")[-1].rstrip("]") for w in warnings
                           if "[" in w), AWF_DATA_ROLE_MISSING)
            entry.state = "blocked"
            entry.reason_code = reason
            entry.message = "; ".join(warnings)[:300]
            entry.touch()
            state.log("step_blocked", step=step.step_id, code=reason)
            return

        fp = step_fingerprint(
            step, input_identities=identities,
            science=contract.science_context)
        # 幂等复用：同指纹已 satisfied → 不重跑不重铸（恢复/重跑不重复写产物）
        if entry.state == "satisfied" and entry.fingerprint == fp:
            state.log("step_reused", step=step.step_id)
            return

        entry.state = "running"
        entry.attempts += 1
        entry.fingerprint = fp
        entry.touch()
        state.log("step_start", step=step.step_id,
                  attempt=entry.attempts)
        if self._persist is not None:
            await self._persist(state)

        try:
            result = await self._dispatch(
                step.tool_name, args, session_id)
        except OperationCancelled:
            entry.state = "cancelled"
            entry.reason_code = "CANCELLED"
            entry.touch()
            state.log("step_cancelled", step=step.step_id)
            return
        except Exception as exc:  # noqa: BLE001 — 工具异常=步骤失败（诚实）
            entry.state = "failed"
            entry.reason_code = type(exc).__name__
            entry.message = str(exc)[:300]
            entry.touch()
            state.log("step_failed", step=step.step_id,
                      error=entry.reason_code)
            return

        if not isinstance(result, dict) or not result.get("success"):
            inner = result if isinstance(result, dict) else {}
            entry.state = "failed"
            entry.reason_code = str(
                inner.get("reason_code") or inner.get("code")
                or inner.get("error_type") or "TOOL_FAILED")
            entry.message = str(
                inner.get("message") or inner.get("error") or "")[:300]
            entry.touch()
            state.log("step_failed", step=step.step_id, code=entry.reason_code)
            return

        # 产物：分发缝已铸 ref（dispatch_ref/结果内 ref:）直接沿用；
        # 未铸（registry 直调路径）才由本层 store（prefix=geojson 同族）。
        fc = _extract_fc(result)
        ref = _extract_ref(result) or str(result.get("dispatch_ref", "") or "")
        if fc is not None and not ref:
            if self._sd is not None:
                try:
                    ref = await self._sd.store(
                        session_id, fc, prefix="geojson")
                except Exception as exc:  # noqa: BLE001 — 存储失败=步骤失败
                    entry.state = "failed"
                    entry.reason_code = "REF_STORE_FAILED"
                    entry.message = str(exc)[:300]
                    entry.touch()
                    state.log("step_failed", step=step.step_id,
                              code=entry.reason_code)
                    return
            else:
                ref = ""
        if fc is not None:
            self._step_payloads[step.step_id] = fc

        stats = result.get("stats") if isinstance(
            result.get("stats"), dict) else {}
        result_warnings = [str(w) for w in (
            result.get("warnings") or []) if w]
        feature_count = _feature_count(fc) if fc else int(
            result.get("dispatch_feature_count", 0) or 0)
        envelope = AnalysisArtifactEnvelope(
            step_id=step.step_id,
            workflow_id=contract.workflow_id,
            tool_name=step.tool_name,
            ref=ref,
            crs=str(result.get("crs", "") or ""),
            units=dict(step.unit_contract),
            feature_count=feature_count,
            empty=((fc is not None and _feature_count(fc) == 0)
                   or (fc is None and ref != "" and feature_count == 0)),
            stats={k: stats[k] for k in sorted(stats)
                   [:MAX_ENVELOPE_STATS_KEYS]} if stats else {},
            warnings=tuple(result_warnings[:8]),
            lineage_inputs=tuple(
                v for v in [
                    await self._role_ref(contract, s.removeprefix("role:"))
                    for s in step.role_inputs.values()
                    if s.startswith("role:")] if v)[:16],
            fingerprint=fp,
            output_roles=step.output_roles,
        )
        state.envelopes[step.step_id] = envelope
        receipt: StepReceipt = build_step_receipt(
            contract, step, result=result,
            input_refs=tuple(
                v for v in [
                    await self._role_ref(contract, s.removeprefix("role:"))
                    for s in step.role_inputs.values()
                    if s.startswith("role:")] if v),
            input_fingerprints=identities,
            output_ref=ref,
            output_fingerprint=fp,
            executed_in_turn=self._turn,
            extra_warnings=tuple(result_warnings[:4]),
        )
        state.receipts[step.step_id] = receipt.model_dump()
        entry.state = "satisfied"
        entry.reason_code = ""
        entry.message = ""
        entry.envelope_ref_hint = ref
        entry.touch()
        state.log("step_satisfied", step=step.step_id, ref=ref,
                  features=envelope.feature_count)

    async def execute(
        self,
        session_id: str,
        contract: AnalysisWorkflowContract,
        state: WorkflowRunState,
        *,
        only_steps: Optional[Set[str]] = None,
    ) -> WorkflowRunState:
        """波次执行（同波无依赖步骤并发；波间严格顺序；失败停波）。"""
        state.status = "running"
        state.touch()
        state.log("workflow_start", turn=self._turn)
        pending = [s for s in contract.steps
                   if only_steps is None or s.step_id in only_steps]
        executed_any = False
        for wave in _waves(contract):
            runnable = [s for s in wave if s in pending]
            if not runnable:
                continue
            # 波前检查：依赖未满足的步骤 blocked（上游失败传播）
            for step in runnable:
                entry = state.steps[step.step_id]
                deps = set(step.depends_on) | {
                    v.removeprefix("step:") for v in step.role_inputs.values()
                    if v.startswith("step:")}
                unmet = [d for d in deps
                         if state.steps.get(d) is None
                         or state.steps[d].state != "satisfied"]
                if unmet:
                    entry.state = "blocked"
                    entry.reason_code = "UPSTREAM_NOT_SATISFIED"
                    entry.message = f"上游未满足: {unmet}"
                    entry.touch()
            wave_runnable = [s for s in runnable
                             if state.steps[s.step_id].state
                             not in ("blocked", "skipped")]
            if not wave_runnable:
                break
            await asyncio.gather(*[
                self._run_step(session_id, contract, step, state)
                for step in wave_runnable])
            executed_any = executed_any or any(
                state.steps[s.step_id].state in ("satisfied", "failed",
                                                 "cancelled", "blocked")
                for s in wave_runnable)
            if self._persist is not None:
                await self._persist(state)
            # 波内失败/取消 → 不再启动新波（siblings-finish 契约，对齐
            # plan_mode）。
            if any(state.steps[s.step_id].state in ("failed", "cancelled")
                   for s in wave_runnable):
                break
            if self._cancelled():
                break
        state.status = verdict_of(state)
        state.touch()
        state.log("workflow_end", status=state.status)
        if self._persist is not None:
            await self._persist(state)
        return state


def _waves(contract: AnalysisWorkflowContract) -> List[List[AnalysisStepSpec]]:
    """拓扑波次（Kahn；确定性序——同波内按契约声明顺序）。"""
    steps = list(contract.steps)
    by_id = {s.step_id: s for s in steps}
    deps: Dict[str, Set[str]] = {}
    for s in steps:
        deps[s.step_id] = set(s.depends_on) | {
            v.removeprefix("step:") for v in s.role_inputs.values()
            if v.startswith("step:")}
    waves: List[List[AnalysisStepSpec]] = []
    done: Set[str] = set()
    remaining = list(steps)
    while remaining:
        wave = [s for s in remaining if deps[s.step_id] <= done]
        if not wave:
            # 环已在校验期拒绝；防御性阻断避免死循环
            break
        waves.append(wave)
        done.update(s.step_id for s in wave)
        remaining = [s for s in remaining if s.step_id not in done]
    del by_id
    return waves
