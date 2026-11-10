"""Analysis Workflow 工具面（goal-02 M4）—— Pi/Agent 可调用的生产入口。

四个按 analysis 域载入的 tier-2 工具：

- ``webgis_analysis_workflow_plan``    场景→契约+typed 裁决（零执行）
- ``webgis_analysis_workflow_run``     执行 pending 步骤（真实 dispatch/幂等/可取消）
- ``webgis_analysis_workflow_status``  状态/产物/lineage/失效 只读投影
- ``webgis_analysis_workflow_recompute`` 输入或参数变更→受影响子图重算

纪律：
- 执行真相 = registry.dispatch（与 Pi 直调工具同缝）；
- 状态单键持久（session store alias）+ per-workflow 分布式锁；
- 返回载荷有界（refs/统计键/receipts 投影——FC 本体绝不进上下文）；
- 结果自标识走 ``tool`` 键（工具名本身）——**不用** ``command``：``command``
  是前端地图命令通道的保留键（dispatcher 只转发 map-command 清单里的名字，
  见 app/contracts/map_command_manifest.json）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from app.services.gis_harness.analysis_workflows.state import WorkflowRunState
from typing import TYPE_CHECKING
from pydantic import JsonValue

if TYPE_CHECKING:
    from app.services.tool_dispatch_service import ToolDispatchResult
import hashlib
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, TypedDict

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_UNKNOWN_FAMILY,
    AnalysisWorkflowContract,
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
from app.services.gis_harness.analysis_workflows.runtime_integrity import (
    local_raster_identity,
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


class BudgetEstimate(TypedDict):
    role: str
    ref: str
    feature_count: int | None
    max_features: int
    over: bool
    reason_code: str


def register_analysis_workflow_tools(registry: ToolRegistry) -> None:
    """注册 4 个分析工作流工具（append-only；不改既有工具）。"""

    # ── 内部助手 ─────────────────────────────────────────────────────────

    async def _load_state(session_id: str, workflow_id: str) -> Optional[WorkflowRunState]:
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

    async def _persist_state(session_id: str, state: WorkflowRunState) -> None:
        ref = await session_data_manager.store(
            session_id, state_to_payload(state), prefix="awf-state")
        await session_data_manager.set_alias(
            session_id, ref, state_alias(state.workflow_id))

    def _registry_sync() -> Any:
        from app.agent_pi_bridge import try_get_tool_registry
        return try_get_tool_registry()

    def _production_dispatch() -> Optional[Callable[[str, dict[str, JsonValue], str], Awaitable[ToolDispatchResult]]]:
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

        async def _dispatch(tool_name: str, args: dict[str, JsonValue],
                            session_id: str) -> ToolDispatchResult:
            service = _get_shared_dispatch_service()
            tc = {"id": f"awf-{_uuid.uuid4().hex[:8]}",
                  "function": {"name": tool_name, "arguments": args}}
            return await service.dispatch(tc, session_id, set())

        return _dispatch

    def _parse_bindings(raw: Optional[str]) -> Dict[str, DataRoleBinding]:
        """role_refs JSON → 绑定（支持 {"role": "ref:..."} 与
        {"role": {"ref": ..., "qualifier": {...}, "role_kind": ...}} 形态）。

        role_kind（ADR-0284 D3）：raster/table 角色必须显式声明
        （raster_zonal_summary 的 subject 是栅格 ref/路径——不声明会被
        orchestrator 的 role_kind 门 typed 拒绝并给出指引）。
        """
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
                    dataset_hint=str(value.get("dataset_hint", "") or ""),
                    role_kind=str(value.get("role_kind", "vector") or "vector"))
            else:
                raise ValueError(f"角色 {role} 的绑定形态非法")
        return bindings

    async def _budget_estimates(session_id: str,
                                contract: AnalysisWorkflowContract) -> List[BudgetEstimate]:
        """plan 期输入预算估计（ADR-0284 断点4）。

        只对 vector 角色的 ``ref:`` 绑定读 descriptor 行数事实（raster/table
        角色无行数语义——栅格工具走窗口读纪律）；descriptor 读不到=unknown
        （不虚报也不拦截——run 期 executor 预算门兜底）。有界：角色数 ≤16。
        """
        from app.services.gis_harness.analysis_workflows.contracts import (
            AWF_BUDGET_EXCEEDED,
        )

        estimates: List[BudgetEstimate] = []
        for role, binding in list(contract.data_contracts.items())[:16]:
            if not isinstance(binding.ref, str) or not binding.ref.startswith(
                    "ref:") or binding.role_kind != "vector":
                continue
            entry: BudgetEstimate = {
                "role": role, "ref": binding.ref,
                "feature_count": None, "max_features": DEFAULT_MAX_FEATURES,
                "over": False, "reason_code": "",
            }
            try:
                descriptor = await session_data_manager.get_ref_descriptor(
                    session_id, binding.ref)
            except Exception:  # noqa: BLE001 — descriptor 是优化面
                descriptor = None
            if isinstance(descriptor, dict):
                try:
                    count = int(descriptor.get("feature_count", 0) or 0)
                except (TypeError, ValueError):
                    count = 0
                entry["feature_count"] = count
                if count > DEFAULT_MAX_FEATURES:
                    entry["over"] = True
                    entry["reason_code"] = AWF_BUDGET_EXCEEDED
            estimates.append(entry)
        return estimates

    # ── 工具 1：plan ─────────────────────────────────────────────────────

    from app.services.gis_harness.analysis_workflows.contracts import (
        ContractValidationError,
    )
    from app.services.gis_harness.analysis_workflows.discovery import (
        next_step_for_blocked,
    )

    @tool(
        registry, name="webgis_analysis_workflow_plan",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "多步 GIS 分析工作流规划：把用户场景装配成带数据角色合同/科学前置/"
            "单位契约的步骤 DAG，并给出 typed 裁决（缺数据→blocked+获取建议；"
            "无路网→不得给等时数值结论；缺分母→只给计数不伪装密度）。**不执行**。"
            "\n七族：admin_facility_distribution（行政区设施分布）/ "
            "service_accessibility（路网可达性）/ hazard_exposure（风险暴露）/"
            " temporal_change（多期变化）/ statistical_zones（统计分区）/ "
            "raster_zonal_summary（栅格指数分区汇总——subject 角色需 "
            "role_kind='raster' 的栅格 ref/路径）/ network_proximity"
            "（最近设施网络距离→行政汇总——subject=需求点、facility=设施）。"
            "plan 附输入行数预算估计（超限 typed 指引分块/降采样）。"
            "\n何时不用：单工具即可回答的问题（直接调该工具）；"
            "LLM 自由组合非常规步骤（用 propose_plan）。"
        ),
        param_descriptions={
            "family": "工作流族 id（七族词表之一）",
            "role_refs": (
                "数据角色绑定 JSON：{role: 'ref:geojson-…'} 或 "
                "{role: {'ref': …, 'qualifier': {'year': 2018}}}；"
                "temporal 族必须带 qualifier.year；栅格角色需 "
                "{'ref': …, 'role_kind': 'raster'}"),
            "params": "步骤参数覆盖 JSON：{step_id: {参数名: 值}}；resample 仅显式 target_resolution 才启用（目标 CRS 单位）",
            "include_optional_steps": "是否启用可选步骤（resample 仍需显式分辨率）",
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
        include_optional_steps: bool = True,
    ) -> dict:
        tool_name = "webgis_analysis_workflow_plan"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        try:
            bindings = _parse_bindings(role_refs)
            overrides = (json.loads(params) if isinstance(params, str)
                         else params) if params else None
        except (ValueError, json.JSONDecodeError) as exc:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_BAD_JSON",
                    "message": f"参数 JSON 非法：{exc}", "alternatives": []}
        # P3-1：内容寻址 id（sha256 前 10 hex——str hash 受 PYTHONHASHSEED
        # 随机化，跨进程不稳定，与工具 deterministic=True 声明冲突）
        digest = hashlib.sha256(
            f"{family}|{role_refs or ''}|{params or ''}|optional={include_optional_steps}".encode(
                "utf-8")).hexdigest()[:10]
        wf_id = workflow_id or f"awf.{family}.{digest}"
        try:
            result = plan_workflow(
                family, workflow_id=wf_id, bindings=bindings,
                params_overrides=overrides, include_optional_steps=include_optional_steps,
                tool_exists=(lambda n: True),  # run 期再真实校验注册面
            )
        except KeyError:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": AWF_UNKNOWN_FAMILY,
                    "message": f"未知族 {family}（词表: {FAMILY_IDS}）",
                    "alternatives": list(FAMILY_IDS)}
        except ContractValidationError as exc:
            # R1 评审（P3）：契约级违例（role_kind 越界/语义合同格式等）→
            # typed declined（含结构化 next_step），不裸 ValueError 冒泡。
            from app.services.gis_harness.analysis_workflows.discovery import (
                next_step_for_blocked as _nsb,
            )
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": exc.code, "message": str(exc),
                    "next_step": _nsb(exc.code)}
        # P1-2/P3-3：单写者协议——plan 的 load/init/persist 全程持锁；
        # 同 id 同契约指纹的重复 plan 幂等保留既有运行态（envelopes/
        # receipts 不被空状态覆写），指纹变化 = 显式重规划才换新状态。
        from app.services.distributed_lock import session_lock_registry

        def _binding_refs(contracts: Any) -> Dict[str, str]:
            """绑定身份 = ref + qualifier + role_kind（R2b P3 + R1 评审 P3：
            qualifier 是 temporal 门的唯一裁决输入，role_kind 是载荷类型
            事实——同 ref 换任一 = 不同绑定解释；dict 分支（快照侧）与
            对象分支（活契约侧）必须同构，否则幂等谓词恒假）。"""
            refs: Dict[str, str] = {}
            for role, binding in (contracts or {}).items():
                if isinstance(binding, dict):
                    ref = str(binding.get("ref", "") or "")
                    qualifier = binding.get("qualifier") or {}
                    role_kind = str(binding.get("role_kind", "") or "")
                else:
                    ref = str(getattr(binding, "ref", "") or "")
                    qualifier = getattr(binding, "qualifier", {}) or {}
                    role_kind = str(getattr(binding, "role_kind", "") or "")
                refs[role] = canonical_json(
                    {"ref": ref, "qualifier": qualifier,
                     "role_kind": role_kind})
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
        # ADR-0284（断点4）：plan 期预算估计——ref descriptor 的行数事实
        # （已知才报；descriptor 缺失=unknown 不虚报）。超限 → typed 不予
        # runnable + 指引（分块/裁剪/换聚合粒度），run 期 executor 仍是硬门。
        budget_estimates = await _budget_estimates(
            session_id, result.contract)
        runnable = result.runnable
        blocked_reasons = list(result.blocked_reasons)
        over_budget = [b for b in budget_estimates if b["over"]]
        if over_budget and runnable:
            runnable = False
            for b in over_budget:
                blocked_reasons.append(
                    f"{b['role']}:{b['reason_code']}")
        return {
            "success": True,
            "tool": tool_name,
            "workflow_id": wf_id,
            "family": family,
            "family_label": family_label(family),
            "runnable": runnable,
            "steps": [
                {"step_id": v.step_id, "verdict": v.verdict,
                 "reason_code": v.reason_code, "tool": next(
                     (s.tool_name for s in result.contract.steps
                      if s.step_id == v.step_id), ""),
                 "message": v.message}
                for v in result.step_verdicts],
            "blocked_reasons": blocked_reasons,
            "alternatives": list(result.alternatives),
            "visualization_hints": list(result.visualization_hints),
            "risk_notes": list(result.contract.risk_notes),
            "budget_estimates": budget_estimates,
            # ADR-0284 B4：blocked/skipped 裁决附结构化 next_step（封闭
            # action 词表 + 可执行提示——LLM 不靠重复空调用碰运气）。
            "blocked_next_steps": [
                {"step_id": v.step_id, "reason_code": v.reason_code,
                 "next_step": next_step_for_blocked(
                     v.reason_code,
                     role=(v.reason_code.split(":", 1)[1].lower()
                           if v.reason_code.startswith((
                               "AWF_DATA_ROLE_MISSING",
                               "AWF_ROLE_KIND_MISMATCH"))
                           else ""))}
                for v in result.step_verdicts
                if v.verdict in ("blocked", "skipped")][:8],
            "next_action": (
                "webgis_analysis_workflow_run" if runnable
                else ("输入行数超预算——先分块（bbox 裁剪/聚合粒度提升）或"
                      "降采样后重绑" if over_budget else
                      "先按 alternatives 补数据角色绑定，再重新 plan")),
        }

    # ── 工具 1b：requirement compile（G02-GIS-EXECUTION-COMPILER）────────
    #
    # 只读编译：session 的 RequirementDocument → 确定性族候选（信号溯源）
    # + 表达候选 + 单位义务。LLM 选族前**必须**先消费此投影——族 id 不再
    # 是自由猜测；显式偏离 top 候选时由调用方在回复中披露理由。

    @tool(
        registry, name="webgis_requirement_compile",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "把本会话的需求文档（RequirementIR）确定性编译为执行候选："
            "族候选（每候选携带 IR 信号溯源：字段路径+值+权重）、表达候选"
            "（用户显式点名 vs 系统推荐分离；数据事实缺席时 suitable=unknown）、"
            "单位义务（率/密度分母裁决——missing_denominator 时只给计数并披露）。"
            "\n何时不用：需求文档尚未建立（先发生 GIS 意图请求）。"
            "\n纪律：选族优先取 top 候选；显式偏离必须在回复中说明理由。"
        ),
        param_descriptions={
            "session_id": "会话 id（需求文档按会话持久）",
            "data_facts": (
                "可选数据事实 JSON：{role: {'feature_count': n, "
                "'geometry_types': [...]}}——表达候选适用性裁决输入，"
                "缺席时 suitable=unknown（不虚构）"),
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("需求编译", "族候选", "执行编译器", "requirement", "compile"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_requirement_compile(
        session_id: Optional[str] = None,
        data_facts: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_requirement_compile"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        from app.services.session_data import session_data_manager
        from app.services.gis_harness.requirement_ir import service as req_svc
        from app.services.gis_harness.requirement_ir.contracts import (
            RequirementDocument,
        )
        from app.services.gis_harness.execution_compiler import (
            compile_requirement_to_tasks,
        )

        state = await req_svc.load_state(session_data_manager, session_id)
        dump = state.document if state is not None else None
        if not isinstance(dump, dict):
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "REQUIREMENT_DOCUMENT_ABSENT",
                    "message": "本会话尚无需求文档（先发起 GIS 请求）"}
        try:
            doc = RequirementDocument.model_validate(dump)
        except Exception:  # noqa: BLE001 — 损坏文档 = 诚实拒绝
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "REQUIREMENT_DOCUMENT_INVALID",
                    "message": "需求文档不可解析"}
        facts = None
        if data_facts:
            try:
                parsed = json.loads(data_facts)
                if isinstance(parsed, dict):
                    facts = {
                        str(k): (v if isinstance(v, dict) else {})
                        for k, v in parsed.items()}
            except (ValueError, json.JSONDecodeError):
                return {"success": False, "tool": tool_name, "declined": True,
                        "reason_code": "AWF_BAD_JSON",
                        "message": "data_facts JSON 非法"}
        compilation = compile_requirement_to_tasks(doc, data_facts=facts)
        payload = compilation.to_bounded_dict()
        payload["success"] = True
        payload["tool"] = tool_name
        return payload

    # ── 工具 2：run ──────────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_run",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
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
        tool_name = "webgis_analysis_workflow_run"
        # P2-1a：预算是安全上限不是可协商参数——服务端钳制到
        # [1, DEFAULT_MAX_FEATURES]（调用方只能调低，不能解锁）。
        try:
            max_features = max(1, min(int(max_features),
                                      DEFAULT_MAX_FEATURES))
        except (TypeError, ValueError):
            max_features = DEFAULT_MAX_FEATURES
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        state = await _load_state(session_id, workflow_id)
        if state is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_WORKFLOW_NOT_FOUND",
                    "message": f"工作流 {workflow_id} 不存在——先 plan",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        registry_sync = _registry_sync()
        dispatch_fn = _production_dispatch()
        if registry_sync is None or dispatch_fn is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_REGISTRY_UNAVAILABLE",
                    "message": "工具注册表/分发服务不可用", "alternatives": []}
        from app.services.gis_harness.analysis_workflows.contracts import (
            AnalysisWorkflowContract,
        )
        try:
            contract = AnalysisWorkflowContract.model_validate(
                state.contract_snapshot)
        except Exception:  # noqa: BLE001 — 快照损坏=诚实失败
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_CONTRACT_SNAPSHOT_CORRUPT",
                    "message": "契约快照不可解析（需重新 plan）",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        only: Optional[set] = None
        if only_steps:
            try:
                parsed_only = json.loads(only_steps)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "tool": tool_name,
                        "reason_code": "AWF_BAD_JSON",
                        "message": "only_steps 必须是步骤 id 的 JSON 数组"}
            # B6b：拒绝裸字符串（set("ab") 拆字符成怪过滤器——静默错行为）
            if not isinstance(parsed_only, list) or not all(
                    isinstance(x, str) for x in parsed_only):
                return {"success": False, "tool": tool_name,
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
                return {"success": False, "tool": tool_name, "declined": True,
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
            "tool": tool_name,
            "workflow_id": workflow_id,
            "status": state.status,
            "steps": [
                {"step_id": sid, "state": e.state,
                 "reason_code": e.reason_code, "ref": e.envelope_ref_hint}
                for sid, e in state.steps.items()],
            "layers": [
                {"step_id": sid, "ref": env.ref,
                 "feature_count": env.feature_count,
                 "network": dict(env.network),
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
        tier=2, domains=["analysis"],
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
        tool_name = "webgis_analysis_workflow_status"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        state = await _load_state(session_id, workflow_id)
        if state is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_WORKFLOW_NOT_FOUND",
                    "message": f"工作流 {workflow_id} 不存在",
                    "alternatives": ["webgis_analysis_workflow_plan"]}
        return {
            "success": True,
            "tool": tool_name,
            "workflow_id": workflow_id,
            "status": state.status,
            # ADR-0284 D5（R1 评审 F-6）：科学终审投影从状态机透出（run 返回
            # 体的 science_gate 是现场重算；这里是最初 run 终态的持久投影）。
            "science_validation": dict(state.science_validation),
            "steps": [
                {"step_id": sid, "state": e.state,
                 "reason_code": e.reason_code, "attempts": e.attempts,
                 "fingerprint": e.fingerprint[:16]}
                for sid, e in state.steps.items()],
            "layers": [
                {"step_id": sid, "ref": env.ref,
                 "feature_count": env.feature_count,
                 "network": dict(env.network),
                 "stats": env.stats, "warnings": list(env.warnings)}
                for sid, env in state.envelopes.items()],
            "receipts": list(state.receipts.keys()),
            "run_log_tail": state.run_log[-8:],
        }

    # ── 工具 4：recompute ────────────────────────────────────────────────

    @tool(
        registry, name="webgis_analysis_workflow_recompute",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
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
        tool_name = "webgis_analysis_workflow_recompute"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        registry_sync = _registry_sync()
        dispatch_fn = _production_dispatch()
        if registry_sync is None or dispatch_fn is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "AWF_REGISTRY_UNAVAILABLE",
                    "message": "工具注册表/分发服务不可用", "alternatives": []}
        from app.services.distributed_lock import session_lock_registry
        # P1-2：recompute 的 load→mutate→persist 全程持锁（与 run 同协议；
        # 锁外快照会被并发 run 的写回覆盖 = 丢失更新）。
        async with session_lock_registry.lock(
                f"awf:{workflow_id}", fail_on_degraded=True):
            state = await _load_state(session_id, workflow_id)
            if state is None:
                return {"success": False, "tool": tool_name, "declined": True,
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
                return {"success": False, "tool": tool_name,
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
                    return {"success": False, "tool": tool_name,
                            "reason_code": "AWF_UNKNOWN_STEP",
                            "message": f"未知步骤 {step_id}"}
                merged_params[step_id].update(changes or {})
            replan = plan_workflow(
                contract.family, workflow_id=workflow_id,
                bindings=merged_bindings, params_overrides=merged_params,
                recipe_refs=contract.recipe_refs,
                include_optional_steps=any(step.optional for step in contract.steps),
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
                revision = (desc or {}).get("content_revision")
                if revision is None and binding.role_kind in ("raster", "table"):
                    revision = await asyncio.to_thread(
                        local_raster_identity, binding.ref)
                ident = ref_identity(binding.ref, revision)
                for step in contract.steps:
                    for param, source in step.role_inputs.items():
                        if source == f"role:{role}":
                            current_identities.setdefault(role, {})[
                                param] = ident
            for drift in collect_stale_steps(
                    contract, state, current_identities=current_identities):
                affected.add(drift["step_id"])
                affected.update(drift.get("downstream") or [])
            # ADR-0284 D5（B2 语义演进）：**指纹实测漂移**的已 satisfied 步骤
            # 显式落 state=stale——状态机 stale 态由此获得赋值路径（此前只在
            # 词表）。只标漂移步骤本身（非闭包）：闭包步骤保持 satisfied 让
            # 执行器按指纹精确裁决（上游未变 → step_reused 零重跑不变）；
            # 显式变更的直接消费者参数/身份已变，指纹天然不匹配照常重跑。
            # 若本轮执行中断，持久态如实显示 stale（待重算）而非服旧产物。
            for drift in collect_stale_steps(
                    contract, state, current_identities=current_identities):
                entry = state.steps.get(drift["step_id"])
                if entry is not None and entry.state == "satisfied":
                    entry.state = "stale"
                    entry.reason_code = "INPUT_IDENTITY_DRIFT"
                    entry.touch()
                    state.log("step_stale", step=drift["step_id"])
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
            "tool": tool_name,
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
