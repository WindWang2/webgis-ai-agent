"""
Spatial Decision Intelligence V2 Tool Registration & Catalog Seam.
Registers spatial_decision_v2 and scenario_compare tools into ToolRegistry and ToolCatalog.
"""
import logging
from typing import Dict, List, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
from app.services.spatial_decision.mapspec_integration import (
    apply_decision_to_mapspec,
    apply_comparison_to_mapspec,
)
from app.services.spatial_decision.report_integration import (
    generate_decision_report_markdown,
    generate_comparison_report_markdown,
)

logger = logging.getLogger(__name__)


# --- Pydantic Tool Input Args ---

class SpatialDecisionV2Args(BaseModel):
    scenario: str = Field(..., description="空间情景或决策目标描述（如'新建地铁站'、'新建实验小学'、'人口增长预测'）")
    target_area: str = Field(..., description="目标区域名称、行政区划、GeoJSON、BBOX '[w,s,e,n]' 或 SessionStore ref ID")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="额外模拟参数，如 growth_pct, radii")
    baseline_data_ref: str = Field(default="", description="基线空间数据集 SessionStore ref ID")


class ScenarioItem(BaseModel):
    scenario: str = Field(..., description="情景描述")
    target_area: str = Field(..., description="目标区域")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="参数")
    baseline_data_ref: str = Field(default="", description="基线引用 ID")


class ScenarioCompareArgs(BaseModel):
    scenarios: List[ScenarioItem] = Field(..., description="待对比情景列表（至少 2 个）")
    optimization_goals: Dict[str, str] = Field(
        default_factory=dict,
        description="指标优化方向映射，如 {'housing_price': 'maximize', 'commute_time': 'minimize'}",
    )


def register_spatial_decision_tools(registry: ToolRegistry):
    """Register spatial_decision_v2 and scenario_compare tools."""
    engine = DecisionEngine()
    cmp_engine = ScenarioComparisonEngine()

    @tool(
        registry,
        name="spatial_decision_v2",
        description="Data-Grounded Spatial Decision Intelligence V2: 基于真实空间数据、RAG 证据链与工程规则，进行情景推演、指标计算、MapSpec 地图生成与报告产出。",
        tier=3,
        domains=["what_if"],
        args_model=SpatialDecisionV2Args,
        # 审计修复：async def 工具必须用 ASYNC 策略——THREAD 会在线程里同步调用
        # coroutine 函数并返回未 await 的 coroutine，dispatch 后续 JSON 序列化必挂。
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def spatial_decision_v2(
        scenario: str,
        target_area: str,
        parameters: Dict[str, Any] = None,
        baseline_data_ref: str = "",
        session_id: str = "",
    ) -> dict:
        if parameters is None:
            parameters = {}

        try:
            result = await engine.evaluate_decision(
                scenario_text=scenario,
                target_area_text=target_area,
                parameters=parameters,
                baseline_data_ref=baseline_data_ref,
                session_id=session_id,
            )

            # Ingest layer into MapSpec if session_id is active
            mapspec_state = {}
            validation_passed = False
            if session_id:
                mapspec_state = await apply_decision_to_mapspec(session_id, result)
                try:
                    # The validator's API is the async `validate_runtime`, which
                    # returns a dict keyed by `valid` on the evaluation path (it
                    # drives a headless browser); failure short-circuits return
                    # `success` instead. Absence or exception is not validation
                    # success — default stays False so L1 analysis cannot imply
                    # cartographic/runtime PASS.
                    from app.services.runtime_validator import runtime_validator
                    val_res = await runtime_validator.validate_runtime(session_id)
                    validation_passed = bool(val_res.get("valid", val_res.get("success", False)))
                except Exception as ve:
                    logger.debug(f"[spatial_decision_v2] Runtime validation warning: {ve}")

            report_md = generate_decision_report_markdown(result)
            res_dict = result.model_dump()
            res_dict["report_markdown"] = report_md
            res_dict["mapspec_applied"] = bool(mapspec_state.get("success"))
            res_dict["runtime_validated"] = validation_passed
            if mapspec_state.get("cartographic_review") is not None:
                res_dict["cartographic_review"] = mapspec_state["cartographic_review"]
            if mapspec_state.get("mapspec_fingerprint") is not None:
                res_dict["mapspec_fingerprint"] = mapspec_state["mapspec_fingerprint"]
            
            # Fetch-on-Demand payload trimming for LLM context optimization.
            # Replace the full simulation GeoJSON with a metadata descriptor
            # (feature_count + bbox + ref_id) so the tool_result stays well under
            # the 50k DB/LLM-context cap; the full payload lives in SessionStore.
            if "simulation_geojson" in res_dict:
                from app.tools._utils import _feature_collection_bbox
                full_fc = res_dict["simulation_geojson"]
                res_dict["simulation_geojson"] = {
                    "type": "FeatureCollection",
                    "feature_count": len(full_fc.get("features", [])),
                    "bbox": _feature_collection_bbox(full_fc),
                    "ref_id": result.simulation_ref_id,
                    "note": "Full GeoJSON stored in SessionStore cursor.",
                }
            return res_dict

        except Exception as e:
            logger.error(f"[spatial_decision_v2] Failed: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"空间决策评估失败: {str(e)}",
            }

    @tool(
        registry,
        name="scenario_compare",
        description="多方案情景对比决策分析：评估与对比多个空间方案（如方案 A vs 方案 B vs 方案 C），输出指标矩阵、Pareto 优越面、地图图层与推荐方案。",
        tier=3,
        domains=["what_if"],
        args_model=ScenarioCompareArgs,
        # 审计修复：async def 工具必须用 ASYNC 策略（同 spatial_decision_v2）。
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def scenario_compare(
        scenarios: List[dict],
        optimization_goals: Dict[str, str] = None,
        session_id: str = "",
    ) -> dict:
        if not scenarios:
            return {"type": "error", "message": "至少需要提供一个方案进行对比。"}

        try:
            evaluated_results = []
            for item in scenarios:
                if isinstance(item, dict):
                    scen_str = item.get("scenario", "")
                    target_str = item.get("target_area", "")
                    params = item.get("parameters", {})
                    b_ref = item.get("baseline_data_ref", "")
                elif isinstance(item, ScenarioItem):
                    scen_str = item.scenario
                    target_str = item.target_area
                    params = item.parameters
                    b_ref = item.baseline_data_ref
                else:
                    continue

                res = await engine.evaluate_decision(
                    scenario_text=scen_str,
                    target_area_text=target_str,
                    parameters=params,
                    baseline_data_ref=b_ref,
                    session_id=session_id,
                )
                evaluated_results.append(res)

            cmp_res = await cmp_engine.compare_scenarios(
                results=evaluated_results,
                session_id=session_id,
                optimization_goals=optimization_goals,
            )

            # Ingest comparison layer into MapSpec
            mapspec_state = {}
            if session_id:
                mapspec_state = await apply_comparison_to_mapspec(session_id, cmp_res)

            report_md = generate_comparison_report_markdown(cmp_res)
            res_dict = cmp_res.model_dump()
            res_dict["report_markdown"] = report_md
            res_dict["mapspec_applied"] = bool(mapspec_state.get("success"))
            if mapspec_state.get("cartographic_review") is not None:
                res_dict["cartographic_review"] = mapspec_state["cartographic_review"]
            if mapspec_state.get("mapspec_fingerprint") is not None:
                res_dict["mapspec_fingerprint"] = mapspec_state["mapspec_fingerprint"]

            # Fetch-on-Demand payload trimming for LLM context optimization
            if "comparison_geojson" in res_dict:
                features_cnt = len(res_dict["comparison_geojson"].get("features", []))
                res_dict["comparison_geojson"] = {
                    "type": "FeatureCollection",
                    "features_count": features_cnt,
                    "ref_id": cmp_res.comparison_ref_id,
                    "note": "Full comparison GeoJSON stored in SessionStore cursor.",
                }
            for scen in res_dict.get("scenarios", []):
                if isinstance(scen, dict) and "simulation_geojson" in scen:
                    f_cnt = len(scen["simulation_geojson"].get("features", []))
                    scen["simulation_geojson"] = {
                        "type": "FeatureCollection",
                        "features_count": f_cnt,
                        "ref_id": scen.get("simulation_ref_id", ""),
                    }
            return res_dict

        except Exception as e:
            logger.error(f"[scenario_compare] Failed: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"多方案对比失败: {str(e)}",
            }
