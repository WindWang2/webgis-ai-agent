"""Analysis Workflow 工具面（goal-02 M4）—— Pi/Agent 可调用的生产入口。

四个 tier-1 工具（先例：scene_orchestration_tools / plan_mode 工具族）：

- ``webgis_analysis_workflow_plan``    场景→契约+typed 裁决（零执行）
- ``webgis_analysis_workflow_run``     执行 pending 步骤（真实 dispatch/幂等/可取消）
- ``webgis_analysis_workflow_status``  状态/产物/lineage/失效 只读投影
- ``webgis_analysis_workflow_recompute`` 输入或参数变更→受影响子图重算

纪律：
- 执行真相 = registry.dispatch（与 Pi 直调工具同缝）；
- 状态单键持久（session store alias）+ per-workflow 分布式锁；
- 返回载荷有界（refs/统计键/receipts 投影——FC 本体绝不进上下文）；
- 命令发射 = 工具名本身（不新增前端 command 字符串面）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_UNKNOWN_FAMILY,
    DataRoleBinding,
    canonical_json,
)
from app.services.gis_harness.analysis_workflows.evidence import (
    StepReceipt,
    evidence_summary,
    run_final_gate,
)
from app.services.gis_harness.analysis_workflows.executor import (
    DEFAULT_MAX_FEATURES,
    WorkflowExecutor,
)
from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    family_label,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.recompute import (
    collect_stale_steps,
    downstream_closure,
    ref_identity,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
    state_alias,
    state_from_payload,
    state_to_payload,
)
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

def register_analysis_workflow_tools(registry: ToolRegistry):
    """注册 4 个分析工作流工具（append-only；不改既有工具）。"""

    # ── 内部助手 ─────────────────────────────────────────────────────────

    async def _load_state(session_id: str, workflow_id: str):
        alias = state_alias(workflow_id)
        try:
            ref = await session_data_manager.resolve_alias(session_id, alias)
        except Exception:  # noqa: BLE001 — Redis 降级时按无状态处理
            ref = alias
        payload = None
        try:
            payload = await session_data_manager.get(session_id, ref)
        except Exception:  # noqa: BLE001
            payload = None
        return state_from_payload(payload if isinstance(payload, dict) else {})

    async def _persist_state(session_id: str, state) -> None:
        ref = await session_data_manager.store(
            session_id, state_to_payload(state), prefix="awf-state")
        await session_data_manager.set_alias(
            session_id, ref, state_alias(state.workflow_id))

    def _registry_sync() -> Any:
        from app.agent_pi_bridge import try_get_tool_registry
        return try_get_tool_registry()

    def _production_dispatch():
        """步骤分发 = 共享 ToolDispatchService（capability 记账/guardrail/
        复用/ref 卸载/SessionPlan 行——内层步骤与 Pi 直调同一生产缝）。

        返回 None = 服务不可用（诚实 decline，不回落旁路）。
        """
        import uuid as _uuid
        from app.agent_pi_bridge import (
            _get_shared_dispatch_service,
            try_get_tool_registry,
        )
        if try_get_tool_registry() is None:
            return None

        async def _dispatch(tool_name: str, args: Dict[str, Any],
                            session_id: str):
            service = _get_shared_dispatch_service()
            tc = {"id": f"awf-{_uuid.uuid4().hex[:8]}",
                  "function": {"name": tool_name, "arguments": args}}
            return await service.dispatch(tc, session_id, set())

        return _dispatch

    def _parse_bindings(raw: Optional[str]) -> Dict[str, DataRoleBinding]:
        """role_refs JSON → 绑定（支持 {"role": "ref:..."} 与
        {"role": {"ref": ..., "qualifier": {...}}} 两种形态）。"""
        if not raw:
            return {}
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            raise ValueError("role_refs 必须是 {role: ref|{ref,qualifier}} 对象")
        bindings: Dict[str, DataRoleBinding] = {}
        for role, value in data.items():
            if isinstance(value, str):
                bindings[role] = DataRoleBinding(role=role, ref=value)
            elif isinstance(value, dict):
                bindings[role] = DataRoleBinding(
                    role=role,
                    ref=str(value.get("ref", "") or ""),
                    qualifier=dict(value.get("qualifier") or {}),
                    dataset_hint=str(value.get("dataset_hint", "") or ""))
            else:
                raise ValueError(f"角色 {role} 的绑定形态非法")
        return bindings

    # ── 工具 1：plan ─────────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_plan",
        capabilities=["plan_workflow_orchestration"],
        tier=1,
        description=(
            "多步 GIS 分析工作流规划：把用户场景装配成带数据角色合同/科学前置/"
            "单位契约的步骤 DAG，并给出 typed 裁决（缺数据→blocked+获取建议；"
            "无路网→不得给等时数值结论；缺分母→只给计数不伪装密度）。**不执行**。"
            "\n五族：admin_facility_distribution（行政区设施分布）/ "
            "service_accessibility（路网可达性）/ hazard_exposure（风险暴露）/"
            " temporal_change（多期变化）/ statistical_zones（统计分区）。"
            "\n何时不用：单工具即可回答的问题（直接调该工具）；"
            "LLM 自由组合非常规步骤（用 propose_plan）。"
        ),
        param_descriptions={
            "family": "工作流族 id（五族词表之一）",
            "role_refs": (
                "数据角色绑定 JSON：{role: 'ref:geojson-…'} 或 "
                "{role: {'ref': …, 'qualifier': {'year': 2018}}}；"
                "temporal 族必须带 qualifier.year"),
            "params": "步骤参数覆盖 JSON：{step_id: {参数名: 值}}",
            "workflow_id": "复用既有工作流 id（默认按族+内容派生）",
        },
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("分析工作流", "多步分析", "编排", "analysis workflow", "plan"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_analysis_workflow_plan(
        family: str,
        role_refs: Optional[str] = None,
        params: Optional[str] = None,
        workflow_id: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        command = "webgis_analysis_workflow_plan"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": command}
        try:
            bindings = _parse_bindings(role_refs)
            overrides = (json.loads(params) if isinstance(params, str)
                         else params) if params else None
        except (ValueError, json.JSONDecodeError) as exc:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_BAD_JSON",
                    "message": f"参数 JSON 非法：{exc}", "alternatives": []}
        # P3-1：内容寻址 id（sha256 前 10 hex——str hash 受 PYTHONHASHSEED
        # 随机化，跨进程不稳定，与工具 deterministic=True 声明冲突）
        digest = hashlib.sha256(
            f"{family}|{role_refs or ''}|{params or ''}".encode(
                "utf-8")).hexdigest()[:10]
        wf_id = workflow_id or f"awf.{family}.{digest}"
        try:
            result = plan_workflow(
                family, workflow_id=wf_id, bindings=bindings,
                params_overrides=overrides,
                tool_exists=(lambda n: True),  # run 期再真实校验注册面
            )
        except KeyError:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": AWF_UNKNOWN_FAMILY,
                    "message": f"未知族 {family}（词表: {FAMILY_IDS}）",
                    "alternatives": list(FAMILY_IDS)}
        # P1-2/P3-3：单写者协议——plan 的 load/init/persist 全程持锁；
        # 同 id 同契约指纹的重复 plan 幂等保留既有运行态（envelopes/
        # receipts 不被空状态覆写），指纹变化 = 显式重规划才换新状态。
        from app.services.distributed_lock import session_lock_registry

        def _binding_refs(contracts: Any) -> Dict[str, str]:
            """绑定身份 = ref + qualifier（R2b P3：qualifier 是 temporal 门
            的唯一裁决输入，同 ref 换 qualifier = 不同绑定解释）。"""
            refs: Dict[str, str] = {}
            for role, binding in (contracts or {}).items():
                if isinstance(binding, dict):
                    ref = str(binding.get("ref", "") or "")
                    qualifier = binding.get("qualifier") or {}
                else:
                    ref = str(getattr(binding, "ref", "") or "")
                    qualifier = getattr(binding, "qualifier", {}) or {}
                refs[role] = canonical_json(
                    {"ref": ref, "qualifier": qualifier})
            return refs

        async with session_lock_registry.lock(
                f"awf:{wf_id}", fail_on_degraded=True):
            existing = await _load_state(session_id, wf_id)
            # B1：复用谓词 = 契约指纹 **且 数据绑定一致**——contract_
            # fingerprint 刻意不含绑定（重算语义：同内容换 ref 不变指纹），
            # 但 plan 的幂等复用必须钉住绑定：同 id 换绑 = 显式重规划
            # （新状态），绝不静默复用旧绑定（换绑的合规通道是 recompute）。
            new_refs = _binding_refs(result.contract.data_contracts)
            old_refs = _binding_refs(
                (existing.contract_snapshot or {}).get("data_contracts")
                if existing is not None else None)
            if (existing is not None
                    and existing.contract_fingerprint == result.fingerprint
                    and old_refs == new_refs):
                state = existing
                state.log("plan_idempotent_reuse")
            else:
                state = new_state(result.contract,
                                  contract_fingerprint=result.fingerprint)
                apply_plan_verdicts(state, result.step_verdicts)
                if existing is not None:
                    state.log("plan_replanned",
                              reason="binding_or_contract_changed")
            await _persist_state(session_id, state)
        return {
            "success": True,
            "command": command,
            "workflow_id": wf_id,
            "family": family,
            "family_label": family_label(family),
            "runnable": result.runnable,
            "steps": [
                {"step_id": v.step_id, "verdict": v.verdict,
                 "reason_code": v.reason_code, "tool": next(
                     (s.tool_name for s in result.contract.steps
                      if s.step_id == v.step_id), ""),
                 "message": v.message}
                for v in result.step_verdicts],
            "blocked_reasons": list(result.blocked_reasons),
            "alternatives": list(result.alternatives),
            "visualization_hints": list(result.visualization_hints),
            "risk_notes": list(result.contract.risk_notes),
            "next_action": (
                "webgis_analysis_workflow_run" if result.runnable
                else "先按 alternatives 补数据角色绑定，再重新 plan"),
        }

    # ── 工具 2：run ──────────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_run",
        capabilities=["plan_workflow_orchestration"],
        tier=1,
        description=(
            "执行分析工作流的 pending 步骤：每步经真实工具分发（单一执行真相），"
            "产物铸 ref:geojson-*、每步留 MethodUseContract receipt；同输入幂等"
            "（已 satisfied 且指纹未变的步骤直接复用，不重复写产物）。"
            "\n返回有界摘要（refs/统计/证据）——大数据不进上下文。"
            "\n何时不用：未经 plan 建立工作流（先调 plan）。"
        ),
        param_descriptions={
            "workflow_id": "plan 返回的工作流 id",
            "only_steps": "只执行指定步骤 id 的 JSON 数组（默认全部 pending）",
            "max_features": "单输入行数预算（默认 100000，超限 typed blocked）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("分析工作流", "执行", "多步分析", "run", "evidence"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "capability_unavailable",
                       "memory"),
    )
    async def webgis_analysis_workflow_run(
        workflow_id: str,
        only_steps: Optional[str] = None,
        max_features: int = DEFAULT_MAX_FEATURES,
        session_id: Optional[str] = None,
    ) -> dict:
        command = "webgis_analysis_workflow_run"
        # P2-1a：预算是安全上限不是可协商参数——服务端钳制到
        # [1, DEFAULT_MAX_FEATURES]（调用方只能调低，不能解锁）。
        try:
            max_features = max(1, min(int(max_features),
                                      DEFAULT_MAX_FEATURES))
        except (TypeError, ValueError):
            max_features = DEFAULT_MAX_FEATURES
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": command}
        state = await _load_state(session_id, workflow_id)
        if state is None:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_WORKFLOW_NOT_FOUND",
                    "message": f"工作流 {workflow_id} 不存在——先 plan",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        registry_sync = _registry_sync()
        dispatch_fn = _production_dispatch()
        if registry_sync is None or dispatch_fn is None:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_REGISTRY_UNAVAILABLE",
                    "message": "工具注册表/分发服务不可用", "alternatives": []}
        from app.services.gis_harness.analysis_workflows.contracts import (
            AnalysisWorkflowContract,
        )
        try:
            contract = AnalysisWorkflowContract.model_validate(
                state.contract_snapshot)
        except Exception:  # noqa: BLE001 — 快照损坏=诚实失败
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_CONTRACT_SNAPSHOT_CORRUPT",
                    "message": "契约快照不可解析（需重新 plan）",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        only: Optional[set] = None
        if only_steps:
            try:
                parsed_only = json.loads(only_steps)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "command": command,
                        "reason_code": "AWF_BAD_JSON",
                        "message": "only_steps 必须是步骤 id 的 JSON 数组"}
            # B6b：拒绝裸字符串（set("ab") 拆字符成怪过滤器——静默错行为）
            if not isinstance(parsed_only, list) or not all(
                    isinstance(x, str) for x in parsed_only):
                return {"success": False, "command": command,
                        "reason_code": "AWF_BAD_JSON",
                        "message": "only_steps 必须是字符串数组（非裸字符串）"}
            only = set(parsed_only)
        from app.services.distributed_lock import session_lock_registry
        async with session_lock_registry.lock(
                f"awf:{workflow_id}", fail_on_degraded=True):
            # B4：锁内 load 失败（Redis/alias 抖动）→ 诚实 decline——
            # 不回退锁外快照（可能缺锁窗口内他方写回）。
            reloaded = await _load_state(session_id, workflow_id)
            if reloaded is None:
                return {"success": False, "command": command, "declined": True,
                        "reason_code": "AWF_STATE_UNREADABLE",
                        "message": "锁内状态读取失败（不使用可能陈旧的快照）",
                        "alternatives": ["webgis_analysis_workflow_status"]}
            state = reloaded
            if state.running_stale():
                state.log("resume_after_stale")
            executor = WorkflowExecutor(
                registry=registry_sync,
                session_data=session_data_manager,
                max_features=max_features,
                persist=(lambda st: _persist_state(session_id, st)),
                dispatch=dispatch_fn,
            )
            state = await executor.execute(
                session_id, contract, state, only_steps=only)
        summary = evidence_summary(contract, {
            k: StepReceipt.model_validate(v)
            for k, v in state.receipts.items()})
        return {
            "success": state.status in ("satisfied",),
            "command": command,
            "workflow_id": workflow_id,
            "status": state.status,
            "steps": [
                {"step_id": sid, "state": e.state,
                 "reason_code": e.reason_code, "ref": e.envelope_ref_hint}
                for sid, e in state.steps.items()],
            "layers": [
                {"step_id": sid, "ref": env.ref,
                 "feature_count": env.feature_count,
                 "output_roles": list(env.output_roles)}
                for sid, env in state.envelopes.items() if env.ref],
            "evidence": summary,
            "science_gate": run_final_gate(state),
            "run_log_tail": state.run_log[-6:],
        }

    # ── 工具 3：status ───────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_status",
        capabilities=["plan_workflow_orchestration"],
        tier=1,
        description=(
            "查询分析工作流状态：步骤三态/产物 ref/输入指纹/失效视图/"
            "证据摘要。用于回看哪步失败、检查产物 lineage、判断是否需要重算。"
        ),
        param_descriptions={"workflow_id": "工作流 id"},
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("分析工作流", "状态", "status", "lineage"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def webgis_analysis_workflow_status(
        workflow_id: str,
        session_id: Optional[str] = None,
    ) -> dict:
        command = "webgis_analysis_workflow_status"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": command}
        state = await _load_state(session_id, workflow_id)
        if state is None:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_WORKFLOW_NOT_FOUND",
                    "message": f"工作流 {workflow_id} 不存在",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        return {
            "success": True,
            "command": command,
            "workflow_id": workflow_id,
            "status": state.status,
            "steps": [
                {"step_id": sid, "state": e.state,
                 "reason_code": e.reason_code, "attempts": e.attempts,
                 "fingerprint": e.fingerprint[:16]}
                for sid, e in state.steps.items()],
            "layers": [
                {"step_id": sid, "ref": env.ref,
                 "feature_count": env.feature_count,
                 "stats": env.stats, "warnings": list(env.warnings)}
                for sid, env in state.envelopes.items()],
            "receipts": list(state.receipts.keys()),
            "run_log_tail": state.run_log[-8:],
        }

    # ── 工具 4：recompute ────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_recompute",
        capabilities=["plan_workflow_orchestration"],
        tier=1,
        description=(
            "选择性局部重算：输入数据或步骤参数变更后，只重跑受影响子图"
            "（指纹对账 → stale 步骤及其下游闭包）；未受影响步骤的产物直接"
            "复用（ref 不重铸）。纯样式变更不属于本工具范围（配色等呈现态"
            "永不触发科学重算）。"
            "\n何时不用：首次执行（用 run）；只改了图例/配色（无需重算）。"
        ),
        param_descriptions={
            "workflow_id": "工作流 id",
            "role_refs": "变更后的角色绑定 JSON（未变更的角色可省略）",
            "params": "变更后的步骤参数 JSON（只列变更的步骤）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("分析工作流", "重算", "失效", "recompute", "增量"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_analysis_workflow_recompute(
        workflow_id: str,
        role_refs: Optional[str] = None,
        params: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        command = "webgis_analysis_workflow_recompute"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": command}
        registry_sync = _registry_sync()
        dispatch_fn = _production_dispatch()
        if registry_sync is None or dispatch_fn is None:
            return {"success": False, "command": command, "declined": True,
                    "reason_code": "AWF_REGISTRY_UNAVAILABLE",
                    "message": "工具注册表/分发服务不可用", "alternatives": []}
        from app.services.distributed_lock import session_lock_registry
        # P1-2：recompute 的 load→mutate→persist 全程持锁（与 run 同协议；
        # 锁外快照会被并发 run 的写回覆盖 = 丢失更新）。
        async with session_lock_registry.lock(
                f"awf:{workflow_id}", fail_on_degraded=True):
            state = await _load_state(session_id, workflow_id)
            if state is None:
                return {"success": False, "command": command, "declined": True,
                        "reason_code": "AWF_WORKFLOW_NOT_FOUND",
                        "message": f"工作流 {workflow_id} 不存在",
                        "alternatives": ["webgis_analysis_workflow_plan"]}
            from app.services.gis_harness.analysis_workflows.contracts import (
                AnalysisWorkflowContract,
            )
            contract = AnalysisWorkflowContract.model_validate(
                state.contract_snapshot)
            try:
                bindings = _parse_bindings(role_refs)
                param_changes = (json.loads(params) if isinstance(params, str)
                                 else params) if params else {}
            except (ValueError, json.JSONDecodeError) as exc:
                return {"success": False, "command": command,
                        "reason_code": "AWF_BAD_JSON",
                        "message": f"参数 JSON 非法：{exc}"}
            # 重建契约（角色/参数变更进新快照），plan 复裁决
            merged_bindings = dict(contract.data_contracts)
            for role, binding in bindings.items():
                merged_bindings[role] = binding
            merged_params = {
                s.step_id: dict(s.step_params) for s in contract.steps}
            for step_id, changes in (param_changes or {}).items():
                if step_id not in merged_params:
                    return {"success": False, "command": command,
                            "reason_code": "AWF_UNKNOWN_STEP",
                            "message": f"未知步骤 {step_id}"}
                merged_params[step_id].update(changes or {})
            replan = plan_workflow(
                contract.family, workflow_id=workflow_id,
                bindings=merged_bindings, params_overrides=merged_params,
                recipe_refs=contract.recipe_refs,
                tool_exists=(lambda n: True))
            contract = replan.contract
            state.contract_snapshot = contract.model_dump()
            state.contract_fingerprint = replan.fingerprint
            # 需重跑面 = 显式变更（新绑定角色/参数）的直接消费者+下游闭包
            affected: set = set()
            for role in bindings:
                for step in contract.steps:
                    if any(v == f"role:{role}"
                           for v in step.role_inputs.values()):
                        affected.add(step.step_id)
            for step_id in (param_changes or {}):
                affected.add(step_id)
            affected |= downstream_closure(contract, affected)
            # P2-1c：全量指纹对账——未显式提及角色的原地 overwrite（revision
            # bump）也会被 collect_stale_steps 抓到，杜绝「子集外静默服旧」。
            current_identities: Dict[str, Dict[str, str]] = {}
            for role, binding in contract.data_contracts.items():
                if not binding.ref:
                    continue
                try:
                    desc = await session_data_manager.get_ref_descriptor(
                        session_id, binding.ref)
                except Exception:  # noqa: BLE001 — descriptor 优化面 fail-open
                    desc = None
                ident = ref_identity(
                    binding.ref, (desc or {}).get("content_revision"))
                for step in contract.steps:
                    for param, source in step.role_inputs.items():
                        if source == f"role:{role}":
                            current_identities.setdefault(role, {})[
                                param] = ident
            for drift in collect_stale_steps(
                    contract, state, current_identities=current_identities):
                affected.add(drift["step_id"])
                affected.update(drift.get("downstream") or [])
            # B2：affected 中的 satisfied 步骤**不**强制 stale——执行器的指纹
            # 精确裁决负责复用（同身份 → step_reused 零重跑；漂移 → 重跑），
            # 与工具描述「指纹对账→只重跑受影响子图」一致。
            # 复裁决：数据补齐解除阻断（fresh 非 blocked/skipped 且旧
            # blocked/skipped/failed → pending）；数据仍缺 → 保持 typed
            # blocked/skipped（P1-1）。
            fresh = new_state(contract, contract_fingerprint=replan.fingerprint)
            apply_plan_verdicts(fresh, replan.step_verdicts)
            for sid, entry in fresh.steps.items():
                cur = state.steps.get(sid)
                if cur is None:
                    continue
                # apply_plan_verdicts 只写非 ready 裁决——fresh 态 pending
                # 即「复裁决不再阻断」；旧 blocked/skipped/failed →
                # 解除为 pending（数据补齐路径）。
                if (entry.state not in ("blocked", "skipped")
                        and cur.state in ("blocked", "skipped", "failed")):
                    cur.state = "pending"
                    cur.reason_code = ""
                    cur.message = ""
                    state.log("step_unblocked", step=sid)
                elif entry.state in ("blocked", "skipped"):
                    cur.state = entry.state
                    cur.reason_code = entry.reason_code
                    cur.message = entry.message
            executor = WorkflowExecutor(
                registry=registry_sync,
                session_data=session_data_manager,
                persist=(lambda st: _persist_state(session_id, st)),
                dispatch=dispatch_fn,
            )
            attempts_before = {sid: e.attempts
                               for sid, e in state.steps.items()}
            state = await executor.execute(
                session_id, contract, state, only_steps=affected or None)
            # P1-1：recomputed_steps 只报真实重新 dispatch 的步骤
            #（attempts 增长），不再报「计划内」集。
            recomputed = sorted(
                sid for sid in affected
                if state.steps.get(sid) is not None
                and state.steps[sid].attempts > attempts_before.get(sid, 0))
        return {
            "success": state.status == "satisfied",
            "command": command,
            "workflow_id": workflow_id,
            "status": state.status,
            "recomputed_steps": recomputed,
            "reused_steps": sorted(
                sid for sid, e in state.steps.items()
                if e.state == "satisfied" and sid not in affected),
            "layers": [
                {"step_id": sid, "ref": env.ref,
                 "feature_count": env.feature_count}
                for sid, env in state.envelopes.items() if env.ref],
        }
