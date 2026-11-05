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
from app.services.gis_harness.analysis_workflows.envelope_facts import (
    aggregation_basis_for,
    collect_fc_facts,
    collect_raster_facts,
    collect_result_semantics,
)
from app.services.gis_harness.analysis_workflows.evidence import (
    StepReceipt,
    build_step_receipt,
)
from app.services.gis_harness.analysis_workflows.semantic_gate import (
    check_envelope_semantics,
    check_payload_semantics,
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
    """从工具结果提取主 FeatureCollection。

    适配两族工具响应形状（不改变任何工具的返回契约）：
    - GeoAnalysisResult 族：``data`` / ``geojson`` 键直挂 FC；
    - NetworkAnalysisResult 族（network_tools 10 工具）：``status=success`` +
      ``routes`` 列表（每 route 自带 geometry/properties）→ 就地组 FC
      （路线即产物；数量已被输入预算门与 number_to_find 约束）。
    """
    for key in ("data", "geojson", "result_geojson"):
        candidate = result.get(key)
        if (isinstance(candidate, dict)
                and candidate.get("type") == "FeatureCollection"):
            return candidate
    routes = result.get("routes")
    if isinstance(routes, list) and routes:
        features = []
        trimmed_stub = 0
        for route in routes:
            if not (isinstance(route, dict) and isinstance(
                    route.get("geometry"), dict)):
                continue
            # R1 评审 F-1 修复：trim_network_result 在结果超预算时把
            # route.geometry 替换为无 coordinates 的摘要桩——桩不是几何，
            # 绝不进 FC（否则步骤 satisfied 而产物 ref 内是无效几何）。
            coords = route["geometry"].get("coordinates")
            if not (isinstance(coords, (list, tuple)) and coords):
                trimmed_stub += 1
                continue
            props = {
                k: v for k, v in (route.get("properties")
                                  or {}).items()}
            for key in ("route_id", "origin_id", "destination_id",
                        "total_cost", "total_distance_m", "total_time_s"):
                if key in route:
                    props.setdefault(key, route[key])
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": route["geometry"],
            })
        if features:
            return {"type": "FeatureCollection", "features": features}
        if trimmed_stub:
            # 全部是 trim 桩（超预算结果）——诚实返回 None（无 FC 产物），
            # 不把摘要桩伪装成几何产物。
            return None
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
    ) -> None:
        self._registry = registry
        self._sd = session_data
        self._max_features = max_features
        self._persist = persist
        self._cancel_token = cancel_token
        self._turn = current_turn
        self._dispatch_fn = dispatch
        #: 本次运行内步骤产物（内存 FC，供 step: 下游直传不重复水合）
        self._step_payloads: Dict[str, Dict[str, Any]] = {}
        #: (role, ref) → (payload, identity) 角色水合缓存（同输入多消费
        #: 步骤只水合一次；预算门在首水合处一次性裁决）
        self._role_cache: Dict[Tuple[str, str], Tuple[
            Optional[Dict[str, Any]], str]] = {}

    # ── 输入解析 ─────────────────────────────────────────────────────────

    async def _role_ref(self, contract: AnalysisWorkflowContract,
                        role: str) -> Optional[str]:
        binding = contract.data_contracts.get(role)
        if binding is None or binding.missing or not binding.ref:
            return None
        return binding.ref

    async def _role_revision(self, session_id: str,
                             ref: str) -> Optional[str]:
        """ref 的活体 content_revision（descriptor 可得时；失败=None）。"""
        if self._sd is None:
            return None
        try:
            descriptor = await self._sd.get_ref_descriptor(session_id, ref)
        except Exception:  # noqa: BLE001 — descriptor 是优化面
            return None
        if isinstance(descriptor, dict):
            rev = descriptor.get("content_revision")
            if rev is not None:
                return str(rev)
        return None

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
                binding = contract.data_contracts.get(role)
                ref = await self._role_ref(contract, role)
                if ref is None:
                    warnings.append(
                        f"数据角色 {role} 未绑定 [AWF_DATA_ROLE_MISSING:{role.upper()}]")
                    return None, identities, warnings
                # ADR-0282 D3：raster/table 角色 ref 直通（不水合为 dict——
                # zonal_stats.raster_path 等原生接受 ref/路径；registry
                # _resolve_references 既有语义不变）。行数预算门不适用。
                if binding is not None and binding.role_kind in (
                        "raster", "table"):
                    rev = await self._role_revision(session_id, ref)
                    args[param] = ref
                    identities[param] = ref_identity(ref, rev)
                    continue
                cache_key = (role, ref)
                if cache_key in self._role_cache:
                    payload, ident = self._role_cache[cache_key]
                else:
                    payload, ident, hydrate_warnings = await self._hydrate(
                        session_id, ref, param, step.step_id)
                    warnings.extend(hydrate_warnings)
                    if payload is None and hydrate_warnings:
                        return None, identities, warnings
                    self._role_cache[cache_key] = (payload, ident)
                # ADR-0282 D2：执行期衔接门（实测几何 vs 步骤语义合同）
                gate = check_payload_semantics(
                    param, payload, step.semantic_contract)
                if gate is not None:
                    warnings.append(f"{gate.message} [{gate.code}]")
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
                upstream_env = state.envelopes.get(upstream)
                # ADR-0282 D3：上游产物是栅格 ref（无 FC）→ ref 直通下游
                if upstream_env is not None and upstream_env.ref \
                        and not self._step_payloads.get(upstream) \
                        and (upstream_env.raster
                             or upstream_env.ref.startswith("ref:raster")):
                    args[param] = upstream_env.ref
                    identities[param] = f"fp:{entry.fingerprint}"
                    continue
                # ADR-0282 D2：上游 envelope 实测几何 vs 本步骤语义合同
                gate = check_envelope_semantics(
                    param, upstream_env, step.semantic_contract)
                if gate is not None:
                    warnings.append(f"{gate.message} [{gate.code}]")
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
                # P2-1b：step: 上游输入同受预算门（中间产物可能远大于源输入）
                upstream_count = _feature_count(payload)
                if upstream_count > self._max_features:
                    warnings.append(
                        f"上游 {upstream} 产物行数 {upstream_count} 超预算"
                        f" {self._max_features} [AWF_BUDGET_EXCEEDED]")
                    return None, identities, warnings
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
                if str(status) == "repeated":
                    # 去重拦截无产物（同 turn 同参数重复调用语义）——
                    # 诚实失败而非把空载荷当成功。
                    return {"success": False,
                            "message": "dispatch 去重拦截（repeated）",
                            "reason_code": "TOOL_DISPATCH_REPEATED"}
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
                        logger.warning("Invalid dispatch feature count; omitting count", exc_info=True)
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
        if (self._dispatch_fn is None
                and not self._registry.has(step.tool_name)):
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

        # R1 评审（P3）收紧：success 键存在时以它为权威（{"success": 1} 类
        # 真值不再翻转）；NetworkAnalysisResult 族（无 success 键）以
        # status=="success" 判定。
        if not isinstance(result, dict) or not (
                result.get("success")
                if "success" in result
                else str(result.get("status", "")).lower() == "success"):
            inner = result if isinstance(result, dict) else {}
            reason_code = str(
                inner.get("reason_code") or inner.get("code")
                or inner.get("error_type") or "TOOL_FAILED")
            # ADR-0282 D7：同 run 内 dispatch 去重（repeated）命中而本步骤
            # 与某已 satisfied 步骤指纹相同 → 步骤级幂等复用其产物
            # （同工具+同参+同输入身份=同一计算，不重复执行也绝不把空
            # 载荷当新成功）；无孪生步骤时保持诚实 failed。
            if reason_code == "TOOL_DISPATCH_REPEATED":
                twin = next(
                    (sid for sid, other in state.steps.items()
                     if sid != step.step_id and other.state == "satisfied"
                     and other.fingerprint == fp), None)
                if twin is not None and twin in state.envelopes:
                    twin_env = state.envelopes[twin]
                    envelope_clone = twin_env.model_copy(deep=True)
                    envelope_clone.step_id = step.step_id
                    envelope_clone.fingerprint = fp
                    state.envelopes[step.step_id] = envelope_clone
                    twin_receipt = dict(state.receipts.get(twin) or {})
                    twin_receipt["step_id"] = step.step_id
                    twin_receipt["reused_from_step"] = twin
                    state.receipts[step.step_id] = twin_receipt
                    if envelope_clone.ref and self._sd is not None:
                        self._step_payloads[step.step_id] = await self._sd.get(
                            session_id, envelope_clone.ref)  # type: ignore[assignment]
                    entry.state = "satisfied"
                    entry.reason_code = ""
                    entry.message = ""
                    entry.envelope_ref_hint = envelope_clone.ref
                    entry.touch()
                    state.log("step_reused", step=step.step_id,
                              reused_from=twin, cause="dispatch_repeated")
                    return
            entry.state = "failed"
            entry.reason_code = reason_code
            entry.message = str(
                inner.get("message") or inner.get("error") or "")[:300]
            entry.touch()
            state.log("step_failed", step=step.step_id, code=entry.reason_code)
            return

        # 产物：分发缝已铸 ref（dispatch_ref/结果内 ref:）直接沿用；
        # 未铸（registry 直调路径）才由本层 store（prefix=geojson 同族）。
        fc = _extract_fc(result)
        ref = _extract_ref(result) or str(result.get("dispatch_ref", "") or "")
        # ADR-0282 D3：非 FC 的栅格工件（raster_resample 族——结果形状
        # ``data.output_path`` 文件路径）→ 路径即产物引用（下游 raster
        # 参数直通；envelope.raster 带 output_kind=raster_file 事实）。
        # 相对路径规范化为绝对（工具相对 cwd 返回 ``data/artifacts/…``，
        # 下游 validate_data_path 会把相对路径再拼到 data_dir 下——二次
        # 前缀导致假「文件不存在」；工作流层记录规范工件身份）。
        raster_output = False
        if fc is None and not ref:
            data_obj = result.get("data")
            output_path = (data_obj.get("output_path")
                           if isinstance(data_obj, dict) else None)
            if isinstance(output_path, str) and output_path:
                from pathlib import Path as _Path

                ref = str(_Path(output_path).resolve())
                raster_output = True
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
        # ADR-0282 D1：实测事实采集（有界；采集不到=缺省，绝不虚填）。
        fc_facts = collect_fc_facts(fc) if fc is not None else None
        result_semantics = collect_result_semantics(result)
        raster_facts = collect_raster_facts(result)
        if raster_output:
            raster_facts.setdefault("output_kind", "raster_file")
        crs_value = str(result.get("crs", "") or "")
        if not crs_value and fc_facts is not None and fc_facts.crs:
            crs_value = fc_facts.crs
        envelope = AnalysisArtifactEnvelope(
            step_id=step.step_id,
            workflow_id=contract.workflow_id,
            tool_name=step.tool_name,
            ref=ref,
            crs=crs_value,
            units=dict(step.unit_contract),
            feature_count=feature_count,
            empty=((fc is not None and _feature_count(fc) == 0)
                   or (fc is None and ref != "" and feature_count == 0
                       and not raster_facts)),
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
            # ── v2 实测（envelope_facts 有界采集）────────────────────
            geometry_types=(fc_facts.geometry_types if fc_facts else ()),
            extent=(fc_facts.extent if fc_facts else ()),
            result_semantics=result_semantics,
            raster=raster_facts,
            aggregation_basis=aggregation_basis_for(
                step.tool_name, step.step_params),
            owner_session_id=session_id,
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
        # ADR-0282 D5：全步骤终态后写科学终审投影（G02 run_science_final_gate
        # 的结论进状态机——不再是只有返回体字段；fail-open：门内部错误=
        # not_evaluated，绝不阻断状态落盘）。
        if all(e.state not in ("pending", "running")
               for e in state.steps.values()):
            try:
                from app.services.gis_harness.analysis_workflows.evidence import (
                    run_final_gate,
                )
                gate = run_final_gate(state)
                state.science_validation = {
                    "final_gate": ("blocked" if gate.get("blocked")
                                   else "pass"),
                    "checked_in_turn": self._turn,
                    "findings_n": len(gate.get("findings") or []),
                }
            except Exception:  # noqa: BLE001 — 终审失败=not_evaluated
                state.science_validation = {
                    "final_gate": "not_evaluated",
                    "checked_in_turn": self._turn,
                }
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
