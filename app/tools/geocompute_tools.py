"""GeoCompute 执行平面 Agent 工具（ADR-0096 D1：Agent/Product Plane 经
稳定契约消费 Data Plane —— 本模块是唯一工具面入口，不触碰执行器内部）。

红线：工具只返回**有界摘要**（run 证据 / summary_lines / 指纹），节点
载荷永远经 session ref（MATERIALIZE）按需取用 —— 绝不把大结果灌进 LLM
上下文。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.tools.registry import ToolRegistry, tool
from app.utils.best_effort import best_effort

logger = logging.getLogger(__name__)


def register_geocompute_tools(registry: ToolRegistry):
    """注册 GeoCompute 执行平面工具。"""

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="validate_execution_plan",
        capabilities=['plan_workflow_orchestration'],
        description=(
            "校验统一 Geo 执行计划（ExecutionPlan DAG）并返回确定性指纹与波次。"
            "不执行。类别需为已接线执行器（query/filter/aggregate/spatial_join/"
            "attribute_join/vector_operation/raster_window_operation/interpolation/"
            "materialize/artifact_register/source_scan），其余类别执行期会诚实报 "
            "OPERATION_UNSUPPORTED。"
            "\n返回：{plan_id, graph_fingerprint, node_fingerprints, waves, wired_categories}"
        ),
        param_descriptions={
            "plan_id": "计划的稳定标识（如 'basemap-join-v1'）",
            "nodes": "节点列表，每项 {node_id, category, operation?, inputs?, parameters?, estimate?}",
            "budget": "可选预算 {max_rows, max_bytes, deadline_s, max_nodes}",
        },
        cost="light",
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["执行计划", "校验", "dag", "指纹", "波次", "plan validate"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["invalid_args"],
    )
    def validate_execution_plan(
        plan_id: str,
        nodes: list[dict],
        budget: Optional[dict] = None,
    ) -> dict:
        from app.services.geocompute import graph
        from app.services.geocompute.api import build_plan_from_json

        plan = build_plan_from_json({"plan_id": plan_id, "nodes": nodes,
                                     "budget": budget or {}})
        graph.validate_plan(plan)
        return {
            "plan_id": plan.plan_id,
            "graph_fingerprint": plan.graph_fingerprint(),
            "node_fingerprints": {n.node_id: n.semantic_fingerprint() for n in plan.nodes},
            "waves": graph.topo_wave_order(plan),
        }

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="execute_execution_plan",
        capabilities=['plan_workflow_orchestration'],
        description=(
            "执行统一 Geo 执行计划（波次并行、预算准入、取消/deadline、节点结果复用）。"
            "大输出必须以 materialize 节点显式落存为 session ref —— 工具应答只含"
            "有界证据（行数/引用/状态），绝不内联大数据。"
            "\n返回：{status, run_id, evidence, summary_lines}"
        ),
        param_descriptions={
            "plan_id": "计划的稳定标识",
            "nodes": "节点列表（同 validate_execution_plan）",
            "budget": "可选预算 {max_rows, max_bytes, deadline_s, max_nodes}",
            "session_id": (
                "会话上下文（materialize / durable_job 策略必需）。省略时取调用方"
                "执行上下文（ToolExecutionContext）的当前会话 id；显式传入时必须"
                "与调用方会话 id 一致 —— 不一致以 AUTHORIZATION_DENIED 拒绝"
                "（fail-closed，先于任何节点执行），执行结果与缓存按身份隔离。"
            ),
        },
        cost="heavy",
        side_effect="artifact_creation",
        data_mutations=["cache_write", "artifact_write"],
        deterministic=False,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=["执行计划", "运行", "dag", "波次并行", "预算", "物化", "execute"],
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=["timeout", "invalid_args", "partial_coverage"],
    )
    def execute_execution_plan(
        plan_id: str,
        nodes: list[dict],
        budget: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.geocompute.api import build_plan_from_json, run_plan_sync

        plan = build_plan_from_json({"plan_id": plan_id, "nodes": nodes,
                                     "budget": budget or {}})
        # 评审 MINOR（安全）：从既有 ToolExecutionContext 身份真相取
        # caller/project —— tenant/project 并发作用域与 owner 域由此
        # 生效（无上下文时按匿名隔离，fail-closed）。
        caller: Optional[dict] = None
        project_id: Optional[str] = None
        context_session_id: Optional[str] = None
        # #1549：身份增强失败按匿名隔离（fail-closed）；经 best_effort 可见降级。
        with best_effort(
            "caller-identity-resolve",
            "tools-geocompute-caller-identity-best-effort",
        ):
            from app.services.provenance.context import get_tool_execution_context

            tc = get_tool_execution_context()
            if tc is not None:
                if getattr(tc, "user_id", None):
                    caller = {"user_id": str(tc.user_id),
                              "org_id": getattr(tc, "org_id", None)}
                project_id = getattr(tc, "project_id", None) or None
                context_session_id = getattr(tc, "session_id", None) or None
        # #1708（SEC）：会话隔离承诺落地 —— 显式 session_id 必须与调用方
        # 执行上下文的会话身份一致，否则 typed 拒绝（fail-closed，先于
        # 任何节点执行）：materialize 会把载荷写进该 session 的数据存储
        # （session_data_manager.store），原样透传等于允许跨会话写入。
        # 省略 session_id 时从上下文取（缺上下文的进程内直跑按 None 走，
        # 与既有行为一致）。
        if session_id and context_session_id and session_id != context_session_id:
            from app.services.geocompute.errors import AuthorizationError

            raise AuthorizationError(
                "session_id does not match the caller's execution context",
                details={"code": "SESSION_MISMATCH"},
            )
        if not session_id:
            session_id = context_session_id
        run = run_plan_sync(plan, session_id=session_id, caller=caller,
                            project_id=project_id)
        return {
            "status": run.status.value,
            "run_id": run.run_id,
            "plan_fingerprint": run.plan_fingerprint,
            "evidence": {nid: ev.model_dump() for nid, ev in run.evidence.items()},
            "summary_lines": run.summary_lines(),
        }

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="get_execution_run",
        capabilities=['plan_workflow_orchestration'],
        description=(
            "查询执行 run 的当前状态与有界证据（不返回载荷）。"
            "\n返回：{status, evidence, summary_lines}"
        ),
        param_descriptions={"run_id": "execute_execution_plan 返回的 run 标识"},
        cost="light",
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["执行", "run", "状态查询", "evidence", "运行记录"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["missing_data"],
    )
    def get_execution_run(run_id: str) -> dict:
        from app.services.geocompute.executor import engine

        run = engine.get_run(run_id)
        if run is None:
            return {"status": "not_found", "run_id": run_id}
        return {
            "status": run.status.value,
            "run_id": run.run_id,
            "evidence": {nid: ev.model_dump() for nid, ev in run.evidence.items()},
            "summary_lines": run.summary_lines(),
        }

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="cancel_execution_run",
        capabilities=['plan_workflow_orchestration'],
        description=(
            "请求取消一个正在执行的 run（V5）。未启动节点立即收敛，在飞节点经"
            "协作 checkpoint 收敛；durable 节点级联取消其后台 job。幂等：已终态"
            "的 run 返回 cancelled=false 与当前状态。只允许取消你自己的 run —— "
            "他人/不存在的 run 一律 not_found（不泄漏存在性）。"
            "\n返回：{status, run_id, cancelled, summary_lines?}"
        ),
        param_descriptions={"run_id": "execute_execution_plan 返回的 run 标识"},
        cost="light",
        side_effect="state_mutation",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["执行", "run", "取消", "cancel", "运行控制"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["missing_data"],
    )
    def cancel_execution_run(run_id: str) -> dict:
        from app.services.geocompute.executor import engine, owner_scope_for

        # 与 execute_execution_plan 同一身份真相：ToolExecutionContext 的
        # caller 派生 owner 域；无上下文按匿名隔离（fail-closed）—— 匿名域
        # 与真实用户的 run 互不可见。
        # #1549：身份解析失败按匿名隔离（fail-closed）；经 best_effort 可见降级。
        caller: Optional[dict] = None
        with best_effort(
            "caller-identity-resolve",
            "tools-geocompute-caller-identity-best-effort",
        ):
            from app.services.provenance.context import get_tool_execution_context

            tc = get_tool_execution_context()
            if tc is not None and getattr(tc, "user_id", None):
                caller = {"user_id": str(tc.user_id),
                          "org_id": getattr(tc, "org_id", None)}
        run = engine.get_run(run_id, owner_scope=owner_scope_for(caller))
        if run is None:
            return {"status": "not_found", "run_id": run_id, "cancelled": False}
        cancelled = engine.cancel_run(run_id, reason="cancelled via agent tool")
        return {
            "status": "cancel_requested" if cancelled else run.status.value,
            "run_id": run_id,
            "cancelled": bool(cancelled),
            "summary_lines": run.summary_lines(),
        }
