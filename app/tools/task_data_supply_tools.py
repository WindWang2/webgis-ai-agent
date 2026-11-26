"""任务数据供给工具 —— Agent 的任务级覆盖/缺口/供给面（GOAL-20261126-02 WP4）。

与 REST（``/api/v1/task-data-supply``）共用同一 service 层
（``app/services/task_data_supply``）。五个有界只读/受控写工具：

- ``discover_task_data``：会话联邦目录 → 任务范围下的候选证据包；
- ``compare_task_coverage``：候选 × 需求 → 覆盖矩阵（四层分离 +
  科学适用性 + 缺口原因码）；
- ``plan_task_supply``：覆盖结论 → 受预算供给计划（替代方案显式列出，
  绝不静默降级）；用户选择经 ``choices`` 追溯记录；
- ``execute_supply_plan``：执行**已批准**步骤（显式 ``approved=True``；
  走既有 MaterializationService 生产数据面 → ref 铸造）；
- ``get_supply_plan_status``：计划状态/步骤结果回写查询。

红线：输出只给候选、覆盖/缺口摘要、receipt、拒绝码与产物引用——
绝不返回海量 GeoJSON；展示级来源不进数值统计；未批准不执行。
"""
import logging
from typing import Optional, Tuple

from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

_DATA_ROLES = (
    "subject", "measure", "boundary", "denominator", "network",
    "elevation", "hazard", "receptor", "population", "facility",
    "criteria", "constraint", "reference", "baseline", "target_time",
    "comparison_time",
)


def _parse_roles(roles_csv: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """"subject,boundary" → ((role, (methods...)), ...)；坏角色类型化拒绝。"""
    out: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    for raw in (roles_csv or "").split(","):
        role = raw.strip()
        if not role:
            continue
        if role not in _DATA_ROLES:
            raise ValueError(
                f"REQUIREMENT_ROLE_UNKNOWN: {role!r} ∉ DATA_ROLES（合法值："
                f"{','.join(_DATA_ROLES)}）")
        out = (*out, (role, ("numeric_stats",)))
    return out


def register_task_data_supply_tools(registry: ToolRegistry) -> None:
    """注册任务数据供给工具集。"""

    @tool(
        registry, name="discover_task_data",
        tier=2, domains=["dataset"],
        description=(
            "按任务范围（地点/bbox/时间窗）在当前会话联邦目录中发现候选"
            "数据（会话资产/上传/数据织网目录），返回有界候选证据包"
            "（范围/角色/类型/规模/许可缺口），不做覆盖判定。"
            "\n何时用：任务需要某类数据（如『成都的小学 POI』）时先看"
            "手头与已配置来源有什么候选。"
            "\n负例：不开放任意外部 URL；候选不含未治理来源；"
            "跨日期线 bbox 不走目录粗筛（全量枚举后精确评估）。"
        ),
        param_descriptions={
            "place": "任务地点名（如「成都」；与 bbox 二选一或并用）",
            "bbox": "任务范围 bbox：'minx,miny,maxx,maxy'（跨日期线允许"
                    "minx>maxx）",
            "time_range": "任务时间窗：'2024-01-01,2024-12-31'",
            "roles_csv": "数据角色（逗号分隔，⊆ DATA_ROLES）",
            "keyword": "候选名关键词过滤",
            "session_id": "调用会话（缺省=当前上下文会话）",
        },
        side_effect="pure", deterministic=True, network=False,
        idempotent=True,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=(),
        capabilities=["workspace_state_inspection"],
        tags=("任务数据", "候选发现", "目录", "只读"),
        failure_modes=("invalid_args", "not_found"),
        required_context=(),
    )
    async def discover_task_data(
        place: str = "",
        bbox: str = "",
        time_range: str = "",
        roles_csv: str = "",
        keyword: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.task_data_supply.coverage import (
            evaluate_task_coverage)
        from app.services.task_data_supply.discovery import (
            build_requirement_from_args, discover_task_candidates)

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False,
                    "code": "TASK_SUPPLY_SESSION_REQUIRED",
                    "message": "无法解析会话——任务数据发现需要会话上下文"}
        try:
            requirement = build_requirement_from_args(
                place=place, bbox=bbox, time_range=time_range,
                roles=_parse_roles(roles_csv), provenance="agent_tool")
        except ValueError as e:
            return {"success": False, "code": "TASK_SUPPLY_INVALID_ARGS",
                    "message": str(e)[:300]}
        try:
            candidates, meta = await discover_task_candidates(
                session_id=sid, task_scope=requirement.task_scope,
                roles=tuple(it.role for it in requirement.items),
                keyword=keyword, limit=24)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "code": "TASK_SUPPLY_DISCOVERY_FAILED",
                    "message": f"候选发现失败（如实披露，不伪装空结果）: "
                    f"{str(e)[:300]}"}
        matrix = evaluate_task_coverage(requirement, candidates)
        return {
            "success": True,
            "requirement": requirement.to_bounded_dict(),
            "candidates": [c.to_bounded_dict() if hasattr(
                c, "to_bounded_dict") else _candidate_summary(c)
                for c in candidates[:12]],
            "discovery_meta": meta,
            "coverage_preview": matrix.to_bounded_dict(),
        }

    @tool(
        registry, name="compare_task_coverage",
        tier=2, domains=["dataset"],
        description=(
            "对任务需求 × 候选数据做覆盖矩阵评估：声明范围/元数据相交/"
            "实际可核验覆盖/要素完备性四层分离，输出缺口维度（空间/时间/"
            "属性/粒度/许可/分母）与原因码。bbox 相交绝不宣称全覆盖；"
            "面积比不推断记录完备度；未知 ≠ 不存在。"
            "\n何时用：发现候选后判断哪个候选真的覆盖任务、缺在哪。"
            "\n负例：展示级来源（WMS/tiles）不得进入数值统计资格；"
            "率需求无分母 → 前置缺口。"
        ),
        param_descriptions={
            "place": "任务地点名",
            "bbox": "任务范围 bbox：'minx,miny,maxx,maxy'",
            "time_range": "任务时间窗：'2024-01-01,2024-12-31'",
            "roles_csv": "数据角色（逗号分隔，⊆ DATA_ROLES）",
            "session_id": "调用会话（缺省=当前上下文会话）",
        },
        side_effect="pure", deterministic=True, network=False,
        idempotent=True,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=(),
        capabilities=["workspace_state_inspection"],
        tags=("任务数据", "覆盖矩阵", "缺口", "只读"),
        failure_modes=("invalid_args", "not_found"),
        required_context=(),
    )
    async def compare_task_coverage(
        place: str = "",
        bbox: str = "",
        time_range: str = "",
        roles_csv: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.task_data_supply.coverage import (
            evaluate_task_coverage)
        from app.services.task_data_supply.discovery import (
            build_requirement_from_args, discover_task_candidates)

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False,
                    "code": "TASK_SUPPLY_SESSION_REQUIRED",
                    "message": "无法解析会话——覆盖对比需要会话上下文"}
        try:
            requirement = build_requirement_from_args(
                place=place, bbox=bbox, time_range=time_range,
                roles=_parse_roles(roles_csv), provenance="agent_tool")
        except ValueError as e:
            return {"success": False, "code": "TASK_SUPPLY_INVALID_ARGS",
                    "message": str(e)[:300]}
        try:
            candidates, meta = await discover_task_candidates(
                session_id=sid, task_scope=requirement.task_scope,
                roles=tuple(it.role for it in requirement.items),
                limit=24)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "code": "TASK_SUPPLY_DISCOVERY_FAILED",
                    "message": f"候选发现失败（如实披露）: {str(e)[:300]}"}
        matrix = evaluate_task_coverage(requirement, candidates)
        return {
            "success": True,
            "requirement_id": requirement.requirement_id,
            "missing_obligations": requirement.missing_obligations(),
            "coverage": matrix.to_bounded_dict(),
            "discovery_meta": meta,
        }

    @tool(
        registry, name="plan_task_supply",
        tier=2, domains=["dataset"],
        description=(
            "把覆盖结论组合成受预算的供给计划：每角色从哪条通道取数、"
            "预算估算、覆盖缺口/超支的显式替代方案（缩小范围/保留缺口/"
            "换数据/上传/接受超支）。绝不静默采样、填补或合并异质数据；"
            "计划需用户确认后才可执行。"
            "\n何时用：覆盖矩阵显示候选可用/有缺口时，生成可执行计划。"
            "\n参数 choices：{'角色': 'keep_gap'|'shrink_scope'|"
            "'switch:<candidate_id>'|'upload'|'accept_exceed'}。"
        ),
        param_descriptions={
            "place": "任务地点名",
            "bbox": "任务范围 bbox",
            "time_range": "任务时间窗",
            "roles_csv": "数据角色（逗号分隔）",
            "budget_max_bytes": "预算上限（字节；0/缺省 = 不设上限）",
            "choices_json": "用户决策 JSON（可选）：{\"subject\":\"upload\"}",
            "session_id": "调用会话",
        },
        side_effect="state_mutation", deterministic=True, network=False,
        idempotent=True,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=(),
        capabilities=["dataset_ingest"],
        tags=("任务数据", "供给计划", "预算"),
        failure_modes=("invalid_args", "not_found"),
        required_context=(),
    )
    async def plan_task_supply(
        place: str = "",
        bbox: str = "",
        time_range: str = "",
        roles_csv: str = "",
        budget_max_bytes: int = 0,
        choices_json: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        import json as _json

        from app.services.task_data_supply.coverage import (
            evaluate_task_coverage)
        from app.services.task_data_supply.discovery import (
            build_requirement_from_args, discover_task_candidates,
            get_task_supply_store)
        from app.services.task_data_supply.supply_plan import (
            build_supply_plan)

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False,
                    "code": "TASK_SUPPLY_SESSION_REQUIRED",
                    "message": "无法解析会话——供给计划需要会话上下文"}
        try:
            requirement = build_requirement_from_args(
                place=place, bbox=bbox, time_range=time_range,
                roles=_parse_roles(roles_csv), provenance="agent_tool")
            choices = (_json.loads(choices_json)
                       if choices_json.strip() else {})
            if not isinstance(choices, dict):
                raise ValueError("choices_json 需为 JSON object")
        except ValueError as e:
            return {"success": False, "code": "TASK_SUPPLY_INVALID_ARGS",
                    "message": str(e)[:300]}
        try:
            candidates, meta = await discover_task_candidates(
                session_id=sid, task_scope=requirement.task_scope,
                roles=tuple(it.role for it in requirement.items),
                limit=24)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "code": "TASK_SUPPLY_DISCOVERY_FAILED",
                    "message": f"候选发现失败（如实披露）: {str(e)[:300]}"}
        coverage = evaluate_task_coverage(requirement, candidates)
        try:
            plan = build_supply_plan(
                requirement, coverage, candidates,
                budget_max_bytes=(int(budget_max_bytes) or None),
                choices=choices)
        except ValueError as e:
            return {"success": False, "code": "TASK_SUPPLY_PLAN_REJECTED",
                    "message": str(e)[:300]}
        get_task_supply_store().save(sid, plan)
        return {"success": True, "plan": plan.to_bounded_dict(),
                "discovery_meta": meta}

    @tool(
        registry, name="execute_supply_plan",
        tier=2, domains=["dataset"],
        description=(
            "执行供给计划中已通过状态门（ready）的步骤：acquire 步骤按计划"
            "投影（bbox/时间/列，有界行数帽）走既有 MaterializationService"
            " 生产数据面物化 → 会话 ref；use_bound/local 零获取绑定。存在"
            "未决策事项（覆盖缺口/期次缺口/超支/不可数值资格）的计划处于"
            " requires_confirmation 状态，会被类型化拒绝并列出待决事项——"
            "替代方案决策经 plan_task_supply 的 choices 记录后计划才 ready。"
            "\n信任边界（如实声明）：approved 由调用方断言，本工具不做"
            "服务端用户身份核验（与既有 materialize_dataset 同信任级）；"
            "对最终用户的确认应经 UI/用户决策通道完成后由其侧调用。"
            "\n负例：requires_confirmation 不执行；跨会话计划不可见；"
            "重复执行幂等（已完成步骤跳过不重复物化）；部分失败如实回写"
            " partial 并保留合法产物。"
        ),
        param_descriptions={
            "plan_id": "供给计划 id（plan_task_supply 返回）",
            "approved": "用户已批准（必须显式 true）",
            "roles_csv": "只执行这些角色（缺省=全部 acquire/use_bound 步骤）",
            "session_id": "调用会话",
        },
        side_effect="artifact_creation", deterministic=False, network=True,
        idempotent=True,
        latency_class="slow", memory_class="medium", scale_class="medium",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("artifact_write",),
        capabilities=["dataset_ingest", "data_source_pipeline"],
        tags=("任务数据", "供给执行", "物化"),
        failure_modes=("invalid_args", "not_found", "plan_needs_confirmation",
                       "materialization_failed", "source_unreachable"),
        required_context=(),
    )
    async def execute_supply_plan(
        plan_id: str = "",
        approved: bool = False,
        roles_csv: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.task_data_supply.discovery import (
            get_task_supply_store)

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False,
                    "code": "TASK_SUPPLY_SESSION_REQUIRED",
                    "message": "无法解析会话"}
        if not approved:
            return {"success": False, "code": "TASK_SUPPLY_NOT_APPROVED",
                    "message": "未获用户批准——供给执行需要显式 approved=true"
                               "（替代方案决策先经 plan_task_supply choices 记录）"}
        plan = get_task_supply_store().get(sid, plan_id)
        if plan is None:
            return {"success": False, "code": "TASK_SUPPLY_PLAN_NOT_FOUND",
                    "message": (f"计划 {plan_id!r} 不存在或不属于当前会话"
                                "（跨会话不可见）")}
        if plan.state == "requires_confirmation":
            pending = [
                s.role for s in plan.steps
                if s.alternatives and not s.user_choice]
            return {
                "success": False, "code": "TASK_SUPPLY_PLAN_NEEDS_CONFIRMATION",
                "message": "计划存在未决策事项——先由用户选择替代方案",
                "pending_roles": pending[:8],
            }
        wanted = {r.strip() for r in (roles_csv or "").split(",") if r.strip()}
        from app.services.task_data_supply.execution import (
            execute_plan_steps)
        results, skipped = await execute_plan_steps(sid, plan, roles=wanted)
        return {"success": True, "plan_id": plan.plan_id,
                "plan_state": plan.state, "results": results[:16],
                "skipped": skipped[:16],
                "plan": plan.to_bounded_dict()}

    @tool(
        registry, name="get_supply_plan_status",
        tier=2, domains=["dataset"],
        description=(
            "查询供给计划状态：步骤结果（completed/partial/failed/cancelled）、"
            "产物 ref、预算消耗与未决事项。partial/失败如实可见，"
            "不把部分完成伪装成全部完成。"
        ),
        param_descriptions={
            "plan_id": "供给计划 id",
            "session_id": "调用会话",
        },
        side_effect="pure", deterministic=True, network=False,
        idempotent=True,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=(),
        capabilities=["workspace_state_inspection"],
        tags=("任务数据", "状态查询", "只读"),
        failure_modes=("not_found",),
        required_context=(),
    )
    async def get_supply_plan_status(
        plan_id: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.task_data_supply.discovery import (
            get_task_supply_store)

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False,
                    "code": "TASK_SUPPLY_SESSION_REQUIRED",
                    "message": "无法解析会话"}
        plan = get_task_supply_store().get(sid, plan_id)
        if plan is None:
            return {"success": False, "code": "TASK_SUPPLY_PLAN_NOT_FOUND",
                    "message": (f"计划 {plan_id!r} 不存在或不属于当前会话"
                                "（跨会话不可见）")}
        return {"success": True, "plan": plan.to_bounded_dict()}


def _candidate_summary(c) -> dict:
    """CoverageCandidate → 有界摘要。"""
    return {
        "candidate_id": c.candidate_id[:96],
        "channel": c.channel[:32],
        "serves_roles": list(c.serves_roles)[:8],
        "role_class": c.role_class[:24],
        "declared_bbox": c.declared_bbox,
        "feature_count": c.feature_count,
    }


__all__ = ["register_task_data_supply_tools"]
