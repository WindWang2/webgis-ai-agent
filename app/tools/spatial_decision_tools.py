"""
Spatial Decision Intelligence V2 Tool Registration & Catalog Seam.
Registers spatial_decision_v2 and scenario_compare tools into ToolRegistry and ToolCatalog.
"""
import asyncio
import logging
import uuid
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
from app.services.spatial_decision.decision_engine_v3 import DecisionEngineV3
from app.services.spatial_decision.models_v3 import (
    DecisionProblem,
    Alternative,
    Criterion,
    Constraint,
    CriterionDirection,
    ConstraintType,
    ConstraintCategory,
    SpatialPredicate,
    TargetAreaSpec,
    WeightSource,
)
from app.services.spatial_decision.mapspec_integration import (
    apply_decision_to_mapspec,
    apply_comparison_to_mapspec,
    apply_v3_decision_to_mapspec,
)
from app.services.spatial_decision.report_integration import (
    generate_decision_report_markdown,
    generate_comparison_report_markdown,
    generate_v3_decision_report_markdown,
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


class SpatialDecisionV3Args(BaseModel):
    goal: str = Field(..., description="空间决策目标或选址问题描述（如'新建区级综合医院选址评估'）")
    target_area: str = Field(..., description="目标区域名称、行政区划或坐标范围")
    alternatives: List[Dict[str, Any]] = Field(..., description="候选方案/备选选址列表（每项包含 id, name, geometry, attributes）")
    criteria: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="决策准则列表（含 id, name, direction, weight, unit）")
    constraints: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="约束条件列表（含 id, name, constraint_type, category, threshold）")
    mcda_method: str = Field(default="wsm", description="多准则决策评估方法：'wsm' 或 'topsis'")


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
            # P-4（#877）：各方案相互独立（独立 decision_id、geocode/RAG 网络往返），
            # gather 并行后 wall-clock ≈ max(单方案)；Semaphore(4) 防外部 API 突并发。
            # 单方案异常降级为该方案的 error 项（比旧的"任一异常吞掉全部结果"更鲁棒）。
            _sem = asyncio.Semaphore(4)

            async def _evaluate_one(item):
                scen_str, target_str, params, b_ref = item
                async with _sem:
                    return await engine.evaluate_decision(
                        scenario_text=scen_str,
                        target_area_text=target_str,
                        parameters=params,
                        baseline_data_ref=b_ref,
                        session_id=session_id,
                    )

            pending: list = []
            for item in scenarios:
                if isinstance(item, dict):
                    pending.append((
                        item.get("scenario", ""),
                        item.get("target_area", ""),
                        item.get("parameters", {}),
                        item.get("baseline_data_ref", ""),
                    ))
                elif isinstance(item, ScenarioItem):
                    pending.append((item.scenario, item.target_area, item.parameters, item.baseline_data_ref))
                # 其他形状跳过（与旧串行版一致）

            evaluated_results = []
            for item, res in zip(
                pending,
                await asyncio.gather(
                    *[_evaluate_one(p) for p in pending],
                    return_exceptions=True,
                ),
            ):
                if isinstance(res, BaseException):
                    logger.error(
                        "[scenario_compare] 方案评估失败（已降级为 error 项）: %s", res
                    )
                    evaluated_results.append({
                        "type": "error",
                        "message": f"方案评估失败: {res}",
                    })
                else:
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

    engine_v3 = DecisionEngineV3()

    @tool(
        registry,
        name="spatial_decision_v3",
        description="Evidence-Grounded Spatial Decision Intelligence V3: 具备刚性/弹性空间约束检查、无量纲标准化、MCDA (WSM/TOPSIS)、Pareto 优越面、蒙特卡洛不确定性传播与权重敏感性分析的专业决策引擎。",
        tier=2,
        domains=["what_if"],
        args_model=SpatialDecisionV3Args,
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def spatial_decision_v3(
        goal: str,
        target_area: str,
        alternatives: List[Dict[str, Any]],
        criteria: Optional[List[Dict[str, Any]]] = None,
        constraints: Optional[List[Dict[str, Any]]] = None,
        mcda_method: str = "wsm",
        session_id: str = "",
    ) -> dict:
        if not alternatives:
            return {"type": "error", "message": "至少需要提供一个候选方案进行决策推演。"}

        try:
            target_spec = TargetAreaSpec(
                query=target_area,
                resolved_name=target_area,
                source="user_request",
                confidence=1.0,
            )

            parsed_alts: List[Alternative] = []
            for alt_dict in alternatives:
                alt_id = str(alt_dict.get("id", f"alt_{len(parsed_alts)+1}"))
                alt_name = str(alt_dict.get("name", alt_id))
                alt_desc = str(alt_dict.get("description", ""))
                alt_geom = alt_dict.get("geometry")
                alt_attrs = dict(alt_dict.get("attributes", {}))
                for k, v in alt_dict.items():
                    if k not in {"id", "name", "description", "geometry", "attributes"} and isinstance(v, (int, float, str, bool)):
                        alt_attrs.setdefault(k, v)
                parsed_alts.append(
                    Alternative(
                        id=alt_id,
                        name=alt_name,
                        description=alt_desc,
                        geometry=alt_geom,
                        attributes=alt_attrs,
                    )
                )

            parsed_criteria: List[Criterion] = []
            if criteria:
                for c_dict in criteria:
                    cid = str(c_dict.get("id", f"crit_{len(parsed_criteria)+1}"))
                    cname = str(c_dict.get("name", cid))
                    cunit = str(c_dict.get("unit", ""))
                    cdir_str = str(c_dict.get("direction", "maximize")).lower()
                    cdir = CriterionDirection.MINIMIZE if cdir_str == "minimize" else CriterionDirection.MAXIMIZE
                    cw = float(c_dict.get("weight", 1.0))
                    parsed_criteria.append(
                        Criterion(
                            id=cid,
                            name=cname,
                            unit=cunit,
                            direction=cdir,
                            weight=cw,
                            weight_source=WeightSource.USER_DECLARED if "weight" in c_dict else WeightSource.EQUAL_DEFAULT,
                        )
                    )
            else:
                numeric_keys = set()
                for alt in parsed_alts:
                    for k, v in alt.attributes.items():
                        if isinstance(v, (int, float)):
                            numeric_keys.add(k)
                cost_keywords = {"cost", "price", "time", "distance", "saturation", "noise", "slope", "congestion"}
                for k in sorted(numeric_keys):
                    is_cost = any(kw in k.lower() for kw in cost_keywords)
                    parsed_criteria.append(
                        Criterion(
                            id=k,
                            name=k.replace("_", " ").title(),
                            direction=CriterionDirection.MINIMIZE if is_cost else CriterionDirection.MAXIMIZE,
                            weight=1.0,
                            weight_source=WeightSource.EQUAL_DEFAULT,
                        )
                    )

            if not parsed_criteria:
                parsed_criteria.append(
                    Criterion(
                        id="default_score",
                        name="General Feasibility Score",
                        direction=CriterionDirection.MAXIMIZE,
                        weight=1.0,
                    )
                )

            parsed_constraints: List[Constraint] = []
            if constraints:
                for c_dict in constraints:
                    cid = str(c_dict.get("id", f"const_{len(parsed_constraints)+1}"))
                    cname = str(c_dict.get("name", cid))
                    ctype_str = str(c_dict.get("constraint_type", "hard")).lower()
                    ctype = ConstraintType.SOFT if ctype_str == "soft" else ConstraintType.HARD
                    ccat_str = str(c_dict.get("category", "numeric")).lower()
                    ccat = ConstraintCategory.SPATIAL if ccat_str == "spatial" else ConstraintCategory.NUMERIC
                    spred = None
                    if ccat == ConstraintCategory.SPATIAL:
                        spred_str = str(c_dict.get("spatial_predicate", "outside")).lower()
                        pred_map = {
                            "outside": SpatialPredicate.OUTSIDE,
                            "within": SpatialPredicate.WITHIN,
                            "min_distance": SpatialPredicate.MIN_DISTANCE,
                            "max_distance": SpatialPredicate.MAX_DISTANCE,
                            "intersects": SpatialPredicate.INTERSECTS,
                            "disjoint": SpatialPredicate.DISJOINT,
                            "buffer_exclusion": SpatialPredicate.BUFFER_EXCLUSION,
                            "service_coverage": SpatialPredicate.SERVICE_COVERAGE,
                            "overlap_ratio": SpatialPredicate.OVERLAP_RATIO,
                        }
                        spred = pred_map.get(spred_str, SpatialPredicate.OUTSIDE)
                    parsed_constraints.append(
                        Constraint(
                            id=cid,
                            name=cname,
                            constraint_type=ctype,
                            category=ccat,
                            spatial_predicate=spred,
                            metric_key=c_dict.get("metric_key"),
                            operator=c_dict.get("operator", "<="),
                            threshold=c_dict.get("threshold"),
                            reference_geometry=c_dict.get("reference_geometry"),
                            description=c_dict.get("description", ""),
                        )
                    )

            problem = DecisionProblem(
                problem_id=f"dec_{uuid.uuid4().hex[:10]}",
                goal=goal,
                target_area=target_spec,
                alternatives=parsed_alts,
                criteria=parsed_criteria,
                constraints=parsed_constraints,
                mcda_method=mcda_method,
            )

            v3_result = await engine_v3.solve_problem(problem, session_id=session_id)

            mapspec_state = {}
            if session_id:
                mapspec_state = await apply_v3_decision_to_mapspec(session_id, v3_result)

            res_dict = v3_result.model_dump()
            res_dict["mapspec_applied"] = bool(mapspec_state.get("success"))
            if mapspec_state.get("cartographic_review") is not None:
                res_dict["cartographic_review"] = mapspec_state["cartographic_review"]

            if "comparison_geojson" in res_dict:
                fc = res_dict["comparison_geojson"]
                from app.tools._utils import _feature_collection_bbox
                res_dict["comparison_geojson"] = {
                    "type": "FeatureCollection",
                    "features_count": len(fc.get("features", [])),
                    "bbox": _feature_collection_bbox(fc),
                    "ref_id": v3_result.comparison_ref_id,
                    "note": "Full GeoJSON stored in SessionStore cursor.",
                }

            return res_dict

        except Exception as e:
            logger.error(f"[spatial_decision_v3] Failed: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"空间决策 V3 推演失败: {str(e)}",
            }
