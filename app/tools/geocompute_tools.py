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

logger = logging.getLogger(__name__)


def register_geocompute_tools(registry: ToolRegistry):
    """注册 GeoCompute 执行平面工具。"""

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="validate_execution_plan",
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
                "会话上下文（materialize / durable_job 策略必需）。必须是你自己的"
                "当前会话 id —— 工具运行于调用方会话上下文并原样透传；传入他人"
                "会话 id 的计划会被拒绝，且执行结果与缓存按身份隔离。"
            ),
        },
        cost="heavy",
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
        run = run_plan_sync(plan, session_id=session_id)
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
        description=(
            "查询执行 run 的当前状态与有界证据（不返回载荷）。"
            "\n返回：{status, evidence, summary_lines}"
        ),
        param_descriptions={"run_id": "execute_execution_plan 返回的 run 标识"},
        cost="light",
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
