"""空间抽样工具面（Goal 07 Science V6）— register_sampling_tools。

薄包装职责（ADR-0099 §1）：validate → apply_contract → 调
app/lib/geo_analysis/spatial_sampling.py 纯函数 → 挂 scientific_evidence
→ 返回有界结果。抽样是生成型工具：输出 Point FeatureCollection，属性
带 polygon_index / stratum / sample_id。

独立模块（与 science_temporal_tools 同理）：防与其他工具模块的并行
分支冲突。
"""
import logging
from typing import Any, Optional

from app.lib.geo_processor.core import extract_declared_crs
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_evidence import build_evidence
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _attach_scientific_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    feature_count: Optional[int],
    crs: str = "",
    transformations: Optional[list] = None,
    seed: Optional[int] = None,
) -> dict:
    """descriptor 驱动的证据块（假设/局限/出处唯一事实源）。"""
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning(
            "scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    input_facts = {"feature_count": feature_count}
    if crs:
        input_facts["crs"] = crs
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts,
        transformations=transformations,
        seed=seed,
    )
    return payload


def register_sampling_tools(registry: ToolRegistry):

    @tool(registry, name="sample_random_points",
           description="简单随机空间抽样：在面要素抽样框内逐多边形生成均匀随机"
                       "样本点（投影后度量空间拒绝采样，均匀性按面积定义）。"
                       "野外核查点/精度评估/地统计布点输入；种子可控可复现",
           tier=2, domains=["statistics"], cost="light",
           param_descriptions={
               "geojson": "抽样框面要素 GeoJSON 或数据引用(ref:xxx)",
               "n_per_polygon": "每个多边形内生成的随机样本数（默认 10）",
               "seed": "随机种子（numpy PCG64，默认 42；同种子逐位可复现）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="light",
           scale_class="medium",
           tags=("抽样", "随机采样", "样地布点", "核查点", "精度评估"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def sample_random_points(geojson: Any, n_per_polygon: int = 10,
                             seed: int = 42) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("random_sampling_analysis", {
            "n_per_polygon": n_per_polygon,
            "seed": seed,
        })
        from app.lib.geo_analysis.spatial_sampling import (
            random_points_in_polygons,
        )

        fc, meta = random_points_in_polygons(
            data, n=int(params["n_per_polygon"]), seed=int(params["seed"]))
        payload = {**fc, "success": True, "summary": (
            f"简单随机抽样：{meta['sample_count']} 个样本点"
            f"（{meta['polygon_count']} 个多边形 × 每面 {meta['n_per_polygon']}）"
            f"，seed={meta['seed']}。"
            + (f"零面积多边形跳过 {meta['zero_area_skipped']} 个。"
               if meta["zero_area_skipped"] else "")),
            "sampling_metadata": meta,
        }
        return _attach_scientific_evidence(
            payload, "sampling.random_points", tool="sample_random_points",
            parameters_applied={
                "n_per_polygon": int(params["n_per_polygon"]),
                "seed": int(params["seed"]),
            },
            feature_count=meta["sample_count"],
            crs=extract_declared_crs(data) or "EPSG:4326",
            transformations=[
                "auto-projected sampling frame to local UTM; uniformity "
                "defined in projected metric space",
            ],
            seed=int(params["seed"]),
        )

    @tool(registry, name="sample_systematic_grid",
           description="系统网格空间抽样：在抽样框上铺规则格网（spacing 米），"
                       "随机起点偏移避免周期性对齐偏差，仅保留面内格点。"
                       "注意：系统抽样无设计无偏方差",
           tier=2, domains=["statistics"], cost="light",
           param_descriptions={
               "geojson": "抽样框面要素 GeoJSON 或数据引用(ref:xxx)",
               "spacing": "网格间距（米，投影后度量空间，默认 1000）",
               "seed": "随机起点偏移种子（默认 42）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="light",
           scale_class="medium",
           tags=("抽样", "系统抽样", "网格采样", "样方"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def sample_systematic_grid(geojson: Any, spacing: float = 1000,
                               seed: int = 42) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("systematic_sampling_analysis", {
            "spacing": spacing,
            "seed": seed,
        })
        from app.lib.geo_analysis.spatial_sampling import (
            systematic_grid_points,
        )

        fc, meta = systematic_grid_points(
            data, spacing=float(params["spacing"]), seed=int(params["seed"]))
        payload = {**fc, "success": True, "summary": (
            f"系统网格抽样：{meta['sample_count']} 个格点"
            f"（间距 {meta['spacing']:g} m，{meta['n_rows']}×{meta['n_cols']} 网格，"
            f"随机起点偏移 seed={meta['seed']}）。"),
            "sampling_metadata": meta,
        }
        return _attach_scientific_evidence(
            payload, "sampling.systematic_grid", tool="sample_systematic_grid",
            parameters_applied={
                "spacing": float(params["spacing"]),
                "seed": int(params["seed"]),
            },
            feature_count=meta["sample_count"],
            crs=extract_declared_crs(data) or "EPSG:4326",
            transformations=[
                "auto-projected sampling frame to local UTM; spacing in meters",
            ],
            seed=int(params["seed"]),
        )

    @tool(registry, name="sample_stratified_points",
           description="分层空间抽样：按层别字段把抽样框分层，层内按多边形面积"
                       "再分摊生成均匀随机样本。allocation=equal 每层等额 / "
                       "proportional 按面积权重；分配表随结果披露",
           tier=2, domains=["statistics"], cost="light",
           param_descriptions={
               "geojson": "抽样框面要素 GeoJSON 或数据引用(ref:xxx)",
               "stratum_field": "层别字段名（每要素一个层别值）",
               "n_per_stratum": "每层样本数（默认 30；proportional 时为层预算）",
               "allocation": "层间分配：equal(默认) / proportional",
               "seed": "随机种子（默认 42）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="light",
           scale_class="medium",
           tags=("抽样", "分层抽样", "stratified", "样地布点"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def sample_stratified_points(geojson: Any, stratum_field: str,
                                 n_per_stratum: int = 30,
                                 allocation: str = "equal",
                                 seed: int = 42) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("stratified_sampling_analysis", {
            "stratum_field": stratum_field,
            "n_per_stratum": n_per_stratum,
            "allocation": allocation,
            "seed": seed,
        })
        from app.lib.geo_analysis.spatial_sampling import (
            stratified_points_in_polygons,
        )

        fc, meta = stratified_points_in_polygons(
            data, stratum_field=params["stratum_field"],
            n_per_stratum=int(params["n_per_stratum"]),
            allocation=str(params["allocation"]), seed=int(params["seed"]))
        payload = {**fc, "success": True, "summary": (
            f"分层抽样：{meta['sample_count']} 个样本点，{meta['n_strata']} 层"
            f"（{meta['allocation']} 分配；层预算 {meta['allocation_table']}），"
            f"seed={meta['seed']}。"),
            "sampling_metadata": meta,
        }
        return _attach_scientific_evidence(
            payload, "sampling.stratified_points",
            tool="sample_stratified_points",
            parameters_applied={
                "stratum_field": str(params["stratum_field"]),
                "n_per_stratum": int(params["n_per_stratum"]),
                "allocation": str(params["allocation"]),
                "seed": int(params["seed"]),
            },
            feature_count=meta["sample_count"],
            crs=extract_declared_crs(data) or "EPSG:4326",
            transformations=[
                "auto-projected sampling frame to local UTM; allocation by "
                "projected area",
            ],
            seed=int(params["seed"]),
        )
