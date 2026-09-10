"""GIS Knowledge Tools —— 方法知识只读工具面（Epic 11 §5.J/§27）。

6 个只读工具，全部以 ``KnowledgeService`` 为界（零副作用、确定性、
bounded 投影）：LLM 可问「这是什么类型的 GIS 问题 / 有哪些方法、
哪个可行、为什么拒绝、怎么组合模板、用什么组件」，但**看不到也不
能改**知识图内部结构与 authoritative 知识表。

注册：``app/tools/__init__.py::_TOOL_MODULES`` 一行（R1-F10）；
tier=2（按需调用，知识查询廉价且无副作用）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool


# ── args models（typed schema；public contract）─────────────────────────

class ClassifyArgs(BaseModel):
    query: str = Field(..., description="用户自然语言 GIS 请求（≤200 字）")


class QualifyArgs(BaseModel):
    method_id: str = Field(..., description="方法 id（如 interp.ordinary_kriging）")
    profile: Optional[Dict[str, Any]] = Field(
        None, description="数据画像事实（resolver camelCase：featureCount/geometryTypes/crs/fields）")
    measure_kind: str = Field(
        "unknown", description="量测字段语义：categorical/continuous/unknown")


class RankArgs(BaseModel):
    query: str = Field(..., description="用户自然语言 GIS 请求")
    category_id: str = Field("", description="已知类目（可选；缺省自动分类）")
    profile: Optional[Dict[str, Any]] = Field(None, description="数据画像事实")
    require_uncertainty: bool = Field(
        False, description="输出是否必须携带不确定性")


class ExplainArgs(BaseModel):
    method_id: str = Field(..., description="方法 id")


class TemplatePlanArgs(BaseModel):
    method_id: str = Field(..., description="已选定方法 id")
    category_id: str = Field(..., description="任务类目 id")
    output_target: str = Field(
        "interactive", description="输出目标：interactive/png/pdf")


class ComponentsArgs(BaseModel):
    semantic_role: str = Field("", description="语义角色（legend/disclosure/statistics/…）")
    compatible_artifact: str = Field("", description="按产物类型过滤")


def register_knowledge_tools(registry: ToolRegistry):
    """注册方法知识只读工具（tier 2：查询廉价、无副作用）。"""

    @tool(
        registry,
        tier=1, name="gis_task_classify",
        description=(
            "GIS 任务分类器（确定性，无副作用）。输入自然语言请求，返回任务类目"
            "（20 类分类学）、本体任务与专业方法族路由证据。"
            "\n何时用：需要先判断『这是哪一类 GIS 问题』再选方法/工具时；"
            "零证据（乱码/无关请求）会显式 abstain。"
        ),
        args_model=ClassifyArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "分类", "任务", "方法", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_task_classify(query: str) -> dict:
        from app.lib.gis.methodology.service import get_knowledge_service
        return get_knowledge_service().classify(query[:200])

    @tool(
        registry,
        tier=1, name="gis_method_qualify",
        description=(
            "方法资格裁决（确定性）。给定方法 id 与数据画像事实，返回四态维度报告"
            "（几何/样本/CRS/量测语义/时间/空值/角色/科学前提）、拒绝理由码、"
            "缺失需求与建议预处理。unknown ≠ 不满足。"
            "\n何时用：执行前确认某专业方法在当前数据事实上是否成立。"
        ),
        args_model=QualifyArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "资格", "方法", "数据画像", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_method_qualify(method_id: str,
                           profile: Optional[Dict[str, Any]] = None,
                           measure_kind: str = "unknown") -> dict:
        from app.lib.gis.methodology.qualification import QualificationFacts
        from app.lib.gis.methodology.service import get_knowledge_service
        facts = QualificationFacts.from_profile(
            profile or {}, measure_kind=measure_kind)
        return get_knowledge_service().qualify_method(method_id, facts) \
            .to_bounded_dict()

    @tool(
        registry,
        tier=1, name="gis_method_rank",
        description=(
            "方法候选检索与排序（确定性混合排序）。返回类目池内候选的有序评分"
            "（分量：类目匹配/资格/图兼容/词汇/先验/成本/约束）、弃权语义与解释。"
            "\n何时用：确定类目后询问『有哪些可行方法、哪个最优、哪些被拒及原因』。"
        ),
        args_model=RankArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "方法", "排序", "推荐", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_method_rank(query: str, category_id: str = "",
                        profile: Optional[Dict[str, Any]] = None,
                        require_uncertainty: bool = False) -> dict:
        from app.lib.gis.methodology.qualification import QualificationFacts
        from app.lib.gis.methodology.service import get_knowledge_service
        facts = QualificationFacts.from_profile(profile or {})
        constraints = {"require_uncertainty": True} if require_uncertainty else None
        return get_knowledge_service().retrieve_methods(
            query[:200], facts, category_id=category_id,
            constraints=constraints)

    @tool(
        registry,
        tier=1, name="gis_method_explain",
        description=(
            "方法解释器（确定性）。返回方法的 problem class、假设、参数、"
            "失效条件、不确定性支持与出处（provenance）。"
            "\n何时用：需要向用户解释『这个方法是什么、何时不可用』时。"
        ),
        args_model=ExplainArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "解释", "方法", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_method_explain(method_id: str) -> dict:
        from app.lib.gis.methodology.service import get_knowledge_service
        return get_knowledge_service().explain_method(method_id)

    @tool(
        registry,
        tier=1, name="gis_template_plan",
        description=(
            "模板组合规划（确定性规则驱动）。给定方法与类目，返回基底组合模板、"
            "组件槽位填充、数据绑定与义务组件（不确定性/方法论披露）。"
            "\n何时用：方法选定后询问『这张图该由哪些组件组成、数据怎么绑定』。"
        ),
        args_model=TemplatePlanArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "模板", "组合", "组件", "knowledge"),
        capabilities=["thematic_cartography"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_template_plan(method_id: str, category_id: str,
                          output_target: str = "interactive") -> dict:
        from app.lib.gis.methodology.service import get_knowledge_service
        return get_knowledge_service().plan_template(
            method_id, category_id, output_target=output_target[:16])

    @tool(
        registry,
        tier=1, name="gis_component_query",
        description=(
            "组件目录查询（确定性）。按语义角色（legend/disclosure/statistics/"
            "orientation/measure/…）或产物兼容性过滤地图组件。"
            "\n何时用：需要确认某类组件是否存在、其渲染/导出支持与放置约束时。"
        ),
        args_model=ComponentsArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "组件", "目录", "knowledge"),
        capabilities=["thematic_cartography"],
        output_semantic_type="list", result_size_policy="bounded",
    )
    def gis_component_query(semantic_role: str = "",
                            compatible_artifact: str = "") -> dict:
        from app.lib.gis.methodology.service import get_knowledge_service
        return get_knowledge_service().query_components(
            semantic_role=semantic_role[:32],
            compatible_artifact=compatible_artifact[:40])


__all__ = [
    "register_knowledge_tools",
]
