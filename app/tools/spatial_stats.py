"""空间统计与聚类分析工具 — DBSCAN/K-Means聚类、Moran's I、Geary's C、General G、
Getis-Ord Gi*、Ripley's K、样方χ²检验、核密度估计；Foundation V2（A1）：
局部 Geary、Join Count、双变量 Moran、地理探测器、空间回归族（OLS/SAR/
SEM/SLX/GWR）、权重敏感性"""
import logging
from typing import Any, Optional

from app.tools.registry import ToolRegistry, tool
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson
from app.lib.geo_processor.core import extract_declared_crs
from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools._utils import cached_tool, trim_features, std_error_response
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_evidence import Diagnostic, build_evidence
from app.lib.gis.uncertainty import StatisticalSignificance
# Foundation V2（A1）：spatial_analyzer.py 的委托方法尚未覆盖 V2 新算法
# （该文件不在本域改动范围内）。经包命名空间调用实现层，保持
# test_no_direct_lib_bypass_in_spatial_stats_tools 的字面禁令成立；委托
# 方法落地后应迁移到 SpatialAnalyzer。
from app.lib import geo_analysis as _geo_lib

logger = logging.getLogger(__name__)

def _attach_scientific_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    feature_count: Optional[int],
    crs: str = "",
    uncertainty: Optional[list] = None,
    seed: Optional[int] = None,
    diagnostics: Optional[list] = None,
) -> dict:
    """Attach the VNext scientific-evidence block to a tool payload.

    Thin-wrapper duty (ADR-0099 §1): validate → resolve refs → call the
    implementation → attach evidence. The descriptor is the single source of
    assumptions/limitations/references; the implementation supplies the
    uncertainty blocks. ``diagnostics``（Foundation V2 additive）：运行时
    诊断事实（如 backend_selection），逐条进证据块 diagnostics 列表。
    """
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    input_facts = {"feature_count": feature_count} if feature_count is not None else {}
    transformations = []
    if crs:
        input_facts["crs"] = crs
        if classify_crs(crs) == "geographic":
            transformations.append("auto-projected to local UTM for metric spatial statistics")
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts,
        transformations=transformations or None,
        diagnostics=diagnostics,
        uncertainty=uncertainty,
        seed=seed,
    )
    return payload


def _backend_selection_diagnostic(algorithm_id: str, feature_count: Optional[int]) -> Optional[Diagnostic]:
    """select_backend 决策 → 证据块诊断（additive，失败不阻塞主结果）。

    ``BackendDecision.to_diagnostic()`` 的 value 是变体名字符串，而证据块
    Diagnostic.value 只收数值 —— 这里保留其 name/text、把非数值 value 归一
    为 None（文本里已含 variant 信息）。
    """
    try:
        decision = select_backend(algorithm_id, ScaleProfile(feature_count=feature_count))
        raw = decision.to_diagnostic()
        value = raw.get("value")
        return Diagnostic(
            name=str(raw.get("name") or "backend_selection"),
            value=float(value) if isinstance(value, (int, float))
            and not isinstance(value, bool) else None,
            text=str(raw.get("text") or ""),
        )
    except Exception:  # noqa: BLE001 — 诊断是 additive，不阻塞主结果
        return None

def register_spatial_stats_tools(registry: ToolRegistry):

    @tool(registry, name="spatial_cluster",
           description="空间聚类分析（DBSCAN密度聚类或K-Means分割），返回每个要素的聚类标签；value_field为取值维度（已标准化），value_weight为其权重（默认1.0保守等权，非显式单位语义）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "method": "聚类方法: 'dbscan'(密度聚类, 默认) 或 'kmeans'(K均值)",
               "n_clusters": "K-Means聚类数，默认5",
               "eps": "DBSCAN邻域半径（米），默认1000",
               "min_samples": "DBSCAN最小样本数，默认5",
               "value_field": "可选：参与聚类的数值字段名，将作为额外聚类维度（已标准化）",
               "value_weight": "取值维度的显式权重，默认1.0（保守等权）；调大则取值主导，调小则空间主导",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("聚类", "cluster", "dbscan", "kmeans", "分组"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def spatial_cluster(geojson: Any, method: str = "dbscan", n_clusters: int = 5,
                        eps: float = 1000, min_samples: int = 5,
                        value_field: str = "", value_weight: float = 1.0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110 review M4: forward the full FC — cluster eps is metre-typed and
        # needs the declared CRS to survive the tool boundary.
        res = SpatialAnalyzer.cluster(
            data, method=method, n_clusters=n_clusters, eps=eps,
            min_samples=min_samples, value_field=value_field, value_weight=value_weight,
        )
        return res.to_llm_response()

    @tool(registry, name="standard_deviational_ellipse",
           capabilities=["directional_distribution_analysis"],
           description="计算标准离差椭圆（SDE），用于分析地理要素的空间分布趋势和方向性。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("标准离差椭圆", "sde", "方向", "趋势", "directional"),
           output_semantic_type="geojson_fc",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def standard_deviational_ellipse(geojson: Any) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110 review M5: forward the full FC (CRS preservation).
        res = SpatialAnalyzer.statistics(data, spatial_stats=True)
        return res.to_llm_response()

    @tool(registry, name="moran_i",
           description="全局 Moran's I 空间自相关检验，判断空间分布模式（聚集/离散/随机）；"
                       "支持 knn/queen/rook/distance_band 权重方案与 99/199/499/999 次置换（固定种子）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（后两者需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8，范围2-16）",
               "distance_band": "distance_band 权重的距离阈值（米），0=按8近邻平均距离自动（默认）",
               "permutations": "置换次数：99(默认)/199/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("空间自相关", "moran", "聚集", "离散", "p值"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def moran_i(geojson: Any, value_field: str, weights_scheme: str = "knn",
                k: int = 8, distance_band: float = 0, permutations: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("moran_i_analysis", {
            "value_field": value_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        # #1110 review M6: forward the full FC (CRS preservation).
        res = SpatialAnalyzer.moran_i(
            data, params["value_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            # Foundation V2（A7 additive）：backend 选择决策进证据块诊断，
            # 不改变 moran_i 的任何输出键。
            diagnostics = []
            backend_diag = _backend_selection_diagnostic(
                "stats.morans_i", res.data.get("n_features"))
            if backend_diag is not None:
                diagnostics.append(backend_diag)
            _attach_scientific_evidence(
                payload, "stats.morans_i", tool="moran_i",
                parameters_applied={
                    "value_field": params["value_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=[StatisticalSignificance(
                    target="morans_i",
                    statistic_name="Moran's I",
                    statistic_value=res.data.get("moran_i"),
                    p_value=res.data.get("p_value"),
                    method="permutation",
                    permutations=res.data.get("permutations"),
                    alternative="two-sided",
                )],
                seed=42,
                diagnostics=diagnostics or None,
            )
        return payload

    @tool(registry, name="geary_c",
           description="全局 Geary's C 空间自相关检验（成对差版本，对局部差异比 Moran 更敏感）；"
                       "C<1 聚集 / C>1 离散，置换 p 值（固定种子42），可选正态假设解析方差",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（后两者需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8，范围2-16）",
               "distance_band": "distance_band 权重的距离阈值（米），0=按8近邻平均距离自动（默认）",
               "permutations": "置换次数：99(默认)/199/499/999，固定种子42",
               "analytic_variance": "是否附加正态假设下的解析方差/z/p（默认 False）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("空间自相关", "geary", "局部差异", "聚集检验", "p值"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def geary_c(geojson: Any, value_field: str, weights_scheme: str = "knn",
                k: int = 8, distance_band: float = 0, permutations: int = 99,
                analytic_variance: bool = False) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("geary_c_analysis", {
            "value_field": value_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        res = SpatialAnalyzer.geary_c(
            data, params["value_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
            analytic_variance=bool(analytic_variance),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.gearys_c", tool="geary_c",
                parameters_applied={
                    "value_field": params["value_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                    "analytic_variance": bool(analytic_variance),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=[StatisticalSignificance(
                    target="gearys_c",
                    statistic_name="Geary's C",
                    statistic_value=res.data.get("gearys_c"),
                    p_value=res.data.get("p_value"),
                    method="permutation",
                    permutations=res.data.get("permutations"),
                    alternative="two-sided",
                )],
                seed=42,
            )
        return payload

    @tool(registry, name="general_g",
           description="Getis-Ord General G 全局高值聚集检验（值须非负，如计数/强度）；"
                       "G 显著偏高=高值聚集（clustered-high），显著偏低=低值聚集（clustered-low）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "非负数值字段名（计数/强度语义）",
               "distance_band": "二值权重距离阈值（米），0=按8近邻平均距离自动（默认）",
               "permutations": "置换次数：99(默认)/199/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("高值聚集", "general g", "getis-ord", "低值聚集", "全局检验"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def general_g(geojson: Any, value_field: str, distance_band: float = 0,
                  permutations: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("general_g_analysis", {
            "value_field": value_field,
            "permutations": permutations,
        })
        res = SpatialAnalyzer.general_g(
            data, params["value_field"],
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.general_g", tool="general_g",
                parameters_applied={
                    "value_field": params["value_field"],
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=[StatisticalSignificance(
                    target="general_g",
                    statistic_name="General G",
                    statistic_value=res.data.get("general_g"),
                    p_value=res.data.get("p_value"),
                    method="permutation",
                    permutations=res.data.get("permutations"),
                    alternative="two-sided",
                )],
                seed=42,
            )
        return payload

    @tool(registry, name="ripley_k_analysis",
           description="Ripley's K 点格局分析（各向同性边缘校正）：K(r)/L(r)/CSR参考πr²，"
                       "描述性判断聚集/均匀/随机随半径的变化；需米制坐标（自动投影UTM），无显著性p值",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "n_steps": "r 网格步数（4-32，默认10）",
               "max_distance_ratio": "r_max = 比例×min(窗宽,窗高)，0.05-0.5（默认0.25）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("ripley", "k函数", "点格局", "聚集尺度", "point pattern"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def ripley_k_analysis(geojson: Any, n_steps: int = 10,
                          max_distance_ratio: float = 0.25) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("ripley_k_analysis", {
            "n_steps": n_steps,
            "max_distance_ratio": max_distance_ratio,
        })
        result = SpatialAnalyzer.ripley_k(
            data,
            n_steps=int(params["n_steps"]),
            max_distance_ratio=float(params["max_distance_ratio"]),
        )
        payload = {"success": True, "summary": result["summary"], "data": result}
        _attach_scientific_evidence(
            payload, "point_pattern.ripley_k", tool="ripley_k_analysis",
            parameters_applied={
                "n_steps": int(params["n_steps"]),
                "max_distance_ratio": float(params["max_distance_ratio"]),
            },
            feature_count=result.get("n"),
            crs=extract_declared_crs(data) or "EPSG:4326",
        )
        return payload

    @tool(registry, name="quadrat_analysis",
           description="样方 χ² 点格局离散检验（m×n 网格，期望N/(mn)，df=mn-1）+方差均值比VMR；"
                       "双侧 p<0.05 拒绝完全空间随机（VMR>1 聚集 / VMR<1 均匀），需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "grid_rows": "样方行数（2-10，默认4）",
               "grid_cols": "样方列数（2-10，默认4）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("样方", "卡方", "quadrat", "vmr", "均匀", "点格局"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def quadrat_analysis(geojson: Any, grid_rows: int = 4, grid_cols: int = 4) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("quadrat_analysis", {
            "grid_rows": grid_rows,
            "grid_cols": grid_cols,
        })
        result = SpatialAnalyzer.quadrat_test(
            data,
            grid_rows=int(params["grid_rows"]),
            grid_cols=int(params["grid_cols"]),
        )
        # spatial_operator 统一包装成 GeoAnalysisResult：数据体在 .data、
        # 摘要在 .summary（与同文件 moran/geary/general_g 的消费惯例一致）。
        payload = {"success": True, "summary": result.summary, "data": result.data}
        _attach_scientific_evidence(
            payload, "point_pattern.quadrat_test", tool="quadrat_analysis",
            parameters_applied={
                "grid_rows": int(params["grid_rows"]),
                "grid_cols": int(params["grid_cols"]),
            },
            feature_count=result.data.get("n"),
            crs=extract_declared_crs(data) or "EPSG:4326",
            uncertainty=[StatisticalSignificance(
                target="quadrat_chi2",
                statistic_name=f"quadrat chi2 (df={result.data.get('df')})",
                statistic_value=result.data.get("chi2"),
                p_value=result.data.get("p_value"),
            )],
        )
        return payload

    @tool(registry, name="hotspot_analysis",
           description="Getis-Ord Gi* 热点分析，识别统计显著的高值聚集区（热点）和低值聚集区（冷点）；"
                       "significance_method=permutation 改用条件随机化置换 p（固定种子42，n≤5000）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待分析的数值字段名",
               "distance_band": "空间权重距离阈值（米），0表示自动计算（默认）",
               "significance_method": "显著性方法：normal(默认，解析正态 p) / permutation（条件随机化置换 p）",
               "permutations": "置换次数（仅 permutation 路径）：999(默认)/99/199/499，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="large",
           tags=("热点", "冷点", "getis", "gi*", "hotspot", "显著聚集"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def hotspot_analysis(geojson: Any, value_field: str, distance_band: float = 0,
                         significance_method: str = "normal",
                         permutations: int = 999) -> dict:
        # Foundation V3：significance_method 枚举参数。normal（默认）路径
        # 逐位走既有 SpatialAnalyzer.hotspot 委托，行为不变；permutation
        # 路径经包命名空间调实现层（同文件顶部 _geo_lib 注释的理由：
        # spatial_analyzer.py 不在本批次改动范围内）。
        params = apply_contract("gi_star_analysis", {
            "value_field": value_field,
            "significance_method": significance_method,
            "permutations": permutations,
        })
        if str(params["significance_method"]) == "permutation":
            data = safe_parse_geojson(geojson)
            res = _geo_lib.statistics.hotspot_narrated(
                data, params["value_field"], distance_band=distance_band,
                significance_method="permutation",
                permutations=int(params["permutations"]),
            )
            payload = res.to_llm_response()
            if res.success:
                _attach_scientific_evidence(
                    payload, "spatial.hotspot.local", tool="hotspot_analysis",
                    parameters_applied={
                        "value_field": params["value_field"],
                        "distance_band": float(distance_band or 0),
                        "significance_method": "permutation",
                        "permutations": int(params["permutations"]),
                    },
                    feature_count=len(res.data.get("features") or []) or None,
                    crs=extract_declared_crs(geojson) or "EPSG:4326",
                    uncertainty=_coerce_uncertainty_blocks(res.data),
                    seed=42,
                )
            return payload
        payload = SpatialAnalyzer.hotspot(geojson, params["value_field"],
                                          distance_band=distance_band
                                          ).to_llm_response()
        # 审计 F-3：normal（默认）路径同样挂科学证据块（实现层 data_out
        # 已带 StatisticalSignificance uncertainty 块，这里只做通道透传）。
        if payload.get("success"):
            data = payload.get("data") or {}
            _attach_scientific_evidence(
                payload, "spatial.hotspot.local", tool="hotspot_analysis",
                parameters_applied={
                    "value_field": params["value_field"],
                    "distance_band": float(distance_band or 0),
                    "significance_method": "normal",
                },
                feature_count=len(data.get("features") or []) or None,
                crs=extract_declared_crs(geojson) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(data),
            )
        return payload

    @tool(registry, name="kde_surface",
           description=(
               "高斯核密度估计：生成覆盖全域的连续概率密度格网。"
               "✅ 用于：作为后续叠加分析 / 选址建模的输入数据层。"
               "\n❌ 不要用于：首选可视化——该格网铺满分析范围、不做阈值过滤会遮挡底图；"
               "看分布趋势用 heatmap_data，要矢量等值面用 kde_contours。"
               "\nbandwidth_method='adaptive' 启用 Abramson 平方根自适应带宽"
               "（低密度区带宽放大、高密度区收窄；先导带宽与 λ 范围随结果披露）。"
           ),
           tier=2, domains=["statistics"],
           cost="heavy", timeout=300.0,
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "bandwidth": "核函数带宽（米），0=自动（Scott规则，并按最近邻尺度钳制防过平滑）",
               "bandwidth_method": "'fixed'(默认，单一带宽，行为不变) / 'adaptive'（Abramson 1982 平方根先导律，逐点带宽）",
               "cell_size": "网格单元大小（米），默认500",
               "value_field": "可选：作为权重的数值字段",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84），默认数据范围+10%缓冲",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("核密度", "kde", "密度面", "概率密度", "格网"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="wgs84",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def kde_surface(geojson: Any, bandwidth: float = 0, cell_size: float = 500,
                    value_field: str = "", bounds: Optional[list] = None,
                    bandwidth_method: str = "fixed") -> dict:
        if str(bandwidth_method or "fixed").lower() == "adaptive":
            # adaptive 分支：apply_contract 归一化新参数后经包命名空间直达
            # 实现层（SpatialAnalyzer 委托尚未覆盖 bandwidth_method；与
            # V2 工具同一约定）。
            params = apply_contract("kde_surface_analysis", {
                "bandwidth": bandwidth,
                "bandwidth_method": "adaptive",
                "cell_size": cell_size,
            })
            data = safe_parse_geojson(geojson)
            if not isinstance(data, dict):
                raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
            res = _geo_lib.density.kde_surface(
                data, bandwidth=float(params["bandwidth"]),
                cell_size=float(params["cell_size"]),
                value_field=value_field, bounds=bounds,
                bandwidth_method="adaptive",
            )
        else:
            # fixed（默认）：既有路径逐位不变（不引入契约归一化差异）。
            res = SpatialAnalyzer.kde_surface(
                geojson, bandwidth=bandwidth, cell_size=cell_size,
                value_field=value_field, bounds=bounds,
            )
        return res.to_llm_response()

    @tool(registry, name="kde_contours",
           description=(
               "高斯核密度估计（等值线/等值面模式）：生成矢量等值线或等值面带（isoline_contour）。"
               "✅ 用于：制图与导出——平滑的等值线或等值面成果，支持用户显式指定阈值列表（如 [100, 200, 300]）。"
               "\n❌ 不要用于：快速看分布趋势 — 用 heatmap_data。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "levels": "等值面级数（整数，默认 8）或显式数值等级列表（如 [100, 200, 300]）",
               "bandwidth": "搜索半径（米），0表示自动",
               "mode": "几何模式：'lines' (等值线) 或 'filled_bands' (等值面带，默认)",
               "unit": "物理或统计单位，如 'm', 'people/km²'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("等值线", "等值面", "kde", "密度面", "contour"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data", "memory"))
    @cached_tool(ttl=86400)
    def kde_contours(geojson: Any, levels: Any = 8, bandwidth: float = 0, mode: str = "filled_bands", unit: str = "") -> dict:
        res = SpatialAnalyzer.kde_contours(geojson, levels=levels, bandwidth=bandwidth, mode=mode, unit=unit)
        if not res.success:
            return std_error_response(
                res.summary, code="VALIDATION_ERROR",
                error_type=res.error_type or "ValueError",
                correction_hint=res.correction_hint,
            )
        # Return the FC dict directly (not to_llm_response): the dispatch layer
        # matches type=="FeatureCollection" and the cartography converters read
        # legend_spec as a top-level analysis marker on this dict.
        result_dict = res.data
        if isinstance(result_dict, dict) and result_dict.get("type") == "FeatureCollection":
            result_dict = trim_features(result_dict)
        return result_dict

    @tool(registry, name="voronoi_polygons",
           description="生成 Voronoi (泰森多边形/Thiessen多边形)，将空间按最近邻原则划分为势力范围",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "clip_bounds": "可选：裁剪范围 [xmin, ymin, xmax, ymax]（WGS84），默认使用数据范围+10%缓冲",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("泰森多边形", "voronoi", "thiessen", "势力范围", "邻域划分"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def voronoi_polygons(geojson: Any, clip_bounds: list = None) -> dict:
        res = SpatialAnalyzer.voronoi_polygons(geojson, clip_bounds=clip_bounds)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="convex_hull",
           description=(
               "凸包计算：包住整组要素的最小凸多边形，附 area_km2 与 feature_count。可选 group_by 分组。"
               "\n何时用：『XX 类设施的服务范围大致是多大』；做点群空间范围的快速包络；"
               "聚类预处理 (找出几个 group 的大致边界)。"
               "\n何时不用：(1) 要紧贴形状的边界 — 用 alpha shape (需自定义) 或 concave hull (未实现)；"
               "(2) 仅需 bbox — 用 spatial_stats 看 bbox 字段；"
               "(3) 圈出 DBSCAN 聚类的核心 — 用 spatial_cluster 后再 convex_hull 配合 group_by。"
               "\n关键约束：至少 3 个要素；输出始终 Polygon (即使输入是线/面)。"
           ),
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "group_by": "可选属性字段名。若提供，每个唯一值生成一个独立凸包",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("凸包", "convex hull", "包络", "范围", "服务范围"),
           output_semantic_type="geojson_fc",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def convex_hull(geojson: Any, group_by: str = "") -> dict:
        res = SpatialAnalyzer.convex_hull(geojson, group_by=group_by)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="multi_ring_buffer",
           description=(
               "多环缓冲：围绕要素生成多个同心距离环 (含 ring 属性)，适合做距离分级影响圈。"
               "\n何时用：『学校 500/1000/1500m 三档影响圈』『地铁站 300/800m 步行/接驳圈』『加油站 1/3/5km 服务范围分级』；"
               "做距离衰减分析的母图层（每环+spatial_aggregate 统计落入数量）。"
               "\n何时不用：(1) 只要单一距离 — 用 buffer_analysis；"
               "(2) 时间维而非距离维 — 用 isochrone_analysis (按时间路网计算)；"
               "(3) 想要叠加而非环带 — merge_rings=False 拿到独立同心圆。"
               "\n关键约束：distances 升序列表（米）；merge_rings=True 时返回 ring 字段标识第几环。"
           ),
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "distances": "缓冲距离列表（米），升序，例如 [500, 1000, 1500]",
               "merge_rings": "True=同心环带 (默认)；False=独立同心圆（每个完整覆盖到内圈）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("多环缓冲", "同心圆", "距离环", "影响圈", "multi ring buffer"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def multi_ring_buffer(geojson: Any, distances: list = None,
                           merge_rings: bool = True) -> dict:
        res = SpatialAnalyzer.multi_ring_buffer(geojson, distances=distances, merge_rings=merge_rings)
        return res.to_llm_response()

    @tool(registry, name="h3_lisa",
           description="H3网格LISA空间自相关分析：基于H3网格的Local Moran's I热点和冷点聚类分析（如识别显著的高-高或低-低聚集区）。必须传入带有数值字段的H3网格数据（如通过 h3_binning 得到的数据）。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "h3_geojson": "带有属性值的H3网格 GeoJSON 数据或引用(ref:xxx)",
               "value_field": "参与LISA分析的数值字段名",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("lisa", "局部自相关", "h3", "热点", "冷点"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"))
    def h3_lisa(h3_geojson: Any, value_field: str) -> dict:
        # 审计 F-3：此前本工具与 st_dbscan 均不挂科学证据块 —— 补齐
        # _attach_scientific_evidence 通道（seed=42 = esda.Moran_Local 固定
        # 种子；uncertainty 块由实现层 data_out 提供）。
        res = SpatialAnalyzer.lisa(h3_geojson, value_field)
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.h3_lisa", tool="h3_lisa",
                parameters_applied={"value_field": value_field},
                feature_count=len((res.data or {}).get("features") or [])
                or None,
                crs=extract_declared_crs(h3_geojson) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="st_dbscan",
           description="时空聚类分析（ST-DBSCAN）：结合空间距离(eps1_spatial_meters)和时间间隔(eps2_temporal_seconds)识别时空事件的聚类簇与噪声点。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "包含时间戳(ISO-8601或Epoch)的点要素集 GeoJSON 数据或引用(ref:xxx)",
               "eps1_spatial_meters": "空间距离半径（米），默认 1000.0",
               "eps2_temporal_seconds": "时间间隔阈值（秒），默认 3600.0",
               "min_samples": "形成聚类簇所需的最小点数，默认 5",
               "timestamp_field": "包含时间戳信息的属性字段名称，默认 'timestamp'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("时空聚类", "st-dbscan", "时空", "轨迹聚类", "事件聚类"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def st_dbscan(geojson: Any, eps1_spatial_meters: float = 1000.0,
                  eps2_temporal_seconds: float = 3600.0, min_samples: int = 5,
                  timestamp_field: str = "timestamp") -> dict:
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.st_dbscan(
            data,
            eps1_spatial_meters=eps1_spatial_meters,
            eps2_temporal_seconds=eps2_temporal_seconds,
            min_samples=min_samples,
            timestamp_field=timestamp_field,
        )
        payload = res.to_llm_response()
        # 审计 F-3：st_dbscan 此前无科学元数据 —— 补 evidence 通道
        # （descriptor 未声明 uncertainty_outputs → 不透传 uncertainty）。
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.st_dbscan", tool="st_dbscan",
                parameters_applied={
                    "eps1_spatial_meters": float(eps1_spatial_meters),
                    "eps2_temporal_seconds": float(eps2_temporal_seconds),
                    "min_samples": int(min_samples),
                    "timestamp_field": str(timestamp_field),
                },
                feature_count=len((res.data or {}).get("features") or [])
                or None,
                crs=extract_declared_crs(data) or "EPSG:4326",
            )
        return payload

    # ── Foundation V2（A1）工具 ────────────────────────────────────────
    # 模式与上方 VNext 统计工具一致：safe_parse → apply_contract → 实现 →
    # to_llm_response → _attach_scientific_evidence。科学性失败以类型化
    # 错误抛出（ScientificError ⊂ ValueError，dispatch 已有错误映射）。

    def _coerce_uncertainty_blocks(result_data: dict) -> Optional[list]:
        """实现层 data['uncertainty']（单个 evidence dict 或其列表）→ 类型化模型。"""
        raw = (result_data or {}).get("uncertainty")
        if not raw:
            return None
        if isinstance(raw, dict):
            raw = [raw]
        from typing import List as _List

        from pydantic import TypeAdapter as _TypeAdapter

        from app.lib.gis.uncertainty import UncertaintyBlock as _Block

        return _TypeAdapter(_List[_Block]).validate_python(list(raw))

    def _split_explanatory(raw: str) -> list:
        return [f.strip() for f in str(raw or "").split(",") if f.strip()]

    @tool(registry, name="local_geary",
           description="局部 Geary's C_i（Anselin 1995）：逐要素的邻域相似/相异检测；"
                       "C_i 显著低=相似聚集（similar_high/similar_low），显著高=相异过渡带"
                       "（dissimilar）。方向配对请用 h3_lisa。默认 BH-FDR 多重校正",
           tier=2, domains=["statistics"], cost="medium",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8，范围2-16）",
               "distance_band": "distance_band 权重的距离阈值（米），0=按8近邻平均距离自动（默认）",
               "permutations": "置换次数：99(默认)/199/499/999，固定种子42",
               "correction": "逐格 p 的多重校正：bh(默认)/bonferroni/holm/none",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("局部geary", "局部自相关", "相似聚集", "过渡带", "fdr"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def local_geary(geojson: Any, value_field: str, weights_scheme: str = "knn",
                    k: int = 8, distance_band: float = 0, permutations: int = 99,
                    correction: str = "bh") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("local_geary_analysis", {
            "value_field": value_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
            "correction": correction,
        })
        res = _geo_lib.statistics.local_geary_narrated(
            data, params["value_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
            correction=str(params["correction"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.local_geary", tool="local_geary",
                parameters_applied={
                    "value_field": params["value_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                    "correction": str(params["correction"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="join_count",
           description="二元 Join Count（Cliff-Ord 1973）：二值(0/1)场的邻接同/异类连接检验；"
                       "n_BB/n_BW/n_WW + free-sampling 解析 z 检验，可选置换复核。"
                       "非二值字段会被拒绝（用 moran_i / local_geary 处理连续值）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "binary_field": "二值（0/1）字段名；含其他值会被拒绝",
               "weights_scheme": "二值权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "置换复核次数：0(默认)=只用解析检验 / 99/199/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("join count", "二元", "邻接", "类别场", "空间检验"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def join_count(geojson: Any, binary_field: str, weights_scheme: str = "knn",
                   k: int = 8, distance_band: float = 0, permutations: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("join_count_analysis", {
            "binary_field": binary_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.join_count_narrated(
            data, params["binary_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.join_count", tool="join_count",
                parameters_applied={
                    "binary_field": params["binary_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42 if int(params["permutations"]) > 0 else None,
            )
        return payload

    @tool(registry, name="bivariate_join_count",
    side_effect="deterministic_compute",
    tags=('join_count', '空间自相关', '二值场', '类别检验'),
           description="双色 Join Count（two-color join count，Cliff-Ord 1973）："
                       "恰好取两个值的类别字段（按排序映射 B/W）的邻接同/异类连接检验；"
                       "n_BB/n_BW/n_WW + free-sampling 解析 z 检验，可选置换复核。"
                       "与 join_count 不同：接受任意二类数值字段（不要求 0/1）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "binary_field": "恰好取两个值的类别字段名；>2 或 <2 个取值会被拒绝",
               "weights_scheme": "二值权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "置换复核次数：0(默认)=只用解析检验 / 99/199/499/999，固定种子42",
           })
    def bivariate_join_count(geojson: Any, binary_field: str, weights_scheme: str = "knn",
                             k: int = 8, distance_band: float = 0,
                             permutations: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("bivariate_join_count_analysis", {
            "binary_field": binary_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.bivariate_join_count_narrated(
            data, params["binary_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.bivariate_join_count", tool="bivariate_join_count",
                parameters_applied={
                    "binary_field": params["binary_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42 if int(params["permutations"]) > 0 else None,
            )
        return payload

    @tool(registry, name="rate_smoothing",
    side_effect="deterministic_compute",
    tags=('经验贝叶斯', '率平滑', '收缩估计', '疾病制图'),
           description="经验贝叶斯率平滑（Marshall 1991 矩估计先验）：观测计数/风险人口的"
                       "原始率做先验收缩（weights_scheme 缺省=全局先验；给定权重方案=邻居先验）；"
                       "输出平滑率/原始率/先验参数/收缩权重。零人口区不产率值（显式披露）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入面要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "count_field": "分子：观测计数数值字段名",
               "population_field": "分母：风险人口数值字段名（≤0/缺失的区被排除并披露）",
               "weights_scheme": "先验范围：'none'(默认，全局 EB) / 'knn' / 'queen' / 'rook'（需面要素）/ 'distance_band'（局部邻居先验）",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
           })
    def rate_smoothing(geojson: Any, count_field: str, population_field: str,
                       weights_scheme: str = "none", k: int = 8,
                       distance_band: float = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("rate_smoothing_analysis", {
            "count_field": count_field,
            "population_field": population_field,
            "weights_scheme": weights_scheme,
            "k": k,
        })
        res = _geo_lib.statistics.empirical_bayes_rate_smooth(
            data, params["count_field"], params["population_field"],
            weights_scheme=str(params["weights_scheme"]),
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.rate_smoothing", tool="rate_smoothing",
                parameters_applied={
                    "count_field": params["count_field"],
                    "population_field": params["population_field"],
                    "weights_scheme": str(params["weights_scheme"]),
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="bivariate_moran",
           description="双变量 Moran's I（Wartenberg 1985）：x 与 y 的空间滞后 W·y 的共变；"
                       "是共位相关（co-location），不能解释为因果/超前-滞后关系",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "x 的数值字段名",
               "lag_field": "y 的数值字段名（取其空间滞后 W·y）",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "置换次数（只打乱 y）：99(默认)/199/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("双变量moran", "共位", "空间滞后", "共变", "bivariate"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def bivariate_moran(geojson: Any, value_field: str, lag_field: str,
                        weights_scheme: str = "knn", k: int = 8,
                        distance_band: float = 0, permutations: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("bivariate_moran_analysis", {
            "value_field": value_field,
            "lag_field": lag_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.bivariate_moran_narrated(
            data, params["value_field"], params["lag_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.bivariate_moran", tool="bivariate_moran",
                parameters_applied={
                    "value_field": params["value_field"],
                    "lag_field": params["lag_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="geodetector",
           description="地理探测器（Wang 2010）：分层字段对数值字段的解释力 q∈[0,1]"
                       "（F 检验+可选置换）；interaction_field 给出两因子交互分类"
                       "（nonlinear_enhanced/bilinear_enhanced/independent 等）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "被解释的数值字段名",
               "strata_field": "分层字段名（类别，或数值字段+bins 分箱）",
               "interaction_field": "可选：第二分层字段（交互检测 q(X1∩X2)）",
               "bins": "数值分层字段的分位数分箱数（2-20）；0=按原值类别（≤12 唯一值时）",
               "permutations": "分层标签置换次数：99(默认)/199/499/999；0=只用 F 检验",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("地理探测器", "geodetector", "解释力", "因子", "交互检测"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def geodetector(geojson: Any, value_field: str, strata_field: str,
                    interaction_field: str = "", bins: int = 0,
                    permutations: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("geodetector_analysis", {
            "value_field": value_field,
            "strata_field": strata_field,
            "interaction_field": interaction_field,
            "bins": bins,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.geodetector_narrated(
            data, params["value_field"], params["strata_field"],
            interaction_field=str(params["interaction_field"] or ""),
            bins=int(params["bins"]),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.geodetector", tool="geodetector",
                parameters_applied={
                    "value_field": params["value_field"],
                    "strata_field": params["strata_field"],
                    "interaction_field": str(params["interaction_field"] or ""),
                    "bins": int(params["bins"]),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42 if int(params["permutations"]) > 0 else None,
            )
        return payload

    @tool(registry, name="ols_regression",
           description="OLS 回归 + 空间诊断：系数表(se/t/p/VIF)、R²/AIC、JB 正态性、"
                       "BP 异方差、残差 Moran's I、LM-lag/LM-error 及稳健版（Anselin 1988）；"
                       "残差空间依赖显著时给出 SAR/SEM 建议（不自动换模型）。"
                       "cov_type 可选 HC0/HC1/HC3 异方差稳健标准误（默认 classic 不变）",
           tier=2, domains=["statistics"], cost="medium",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔，如 'pop,distance'）",
               "weights_scheme": "诊断用空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "残差 Moran's I 置换次数：99(默认)/199/499/999，固定种子42",
               "cov_type": "系数协方差：'classic'(默认，经典 (X'X)⁻¹σ²) / 'HC0' / 'HC1' / 'HC3'（MacKinnon-White 稳健标准误，附加列不改系数）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("回归", "ols", "最小二乘", "vif", "空间诊断"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def ols_regression(geojson: Any, target_field: str, explanatory_fields: str,
                       weights_scheme: str = "knn", k: int = 8,
                       distance_band: float = 0, permutations: int = 99,
                       cov_type: str = "classic") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("ols_regression_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
            "cov_type": cov_type,
        })
        res = _geo_lib.spatial_regression.ols_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
            cov_type=str(params["cov_type"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.ols_regression", tool="ols_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                    "cov_type": str(params["cov_type"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="sar_ml_regression",
           description="空间滞后模型 ML 估计（SAR：y=ρWy+Xβ+ε，Ord 1975 特征值法）；"
                       "输出 ρ、LR 检验（vs OLS）、伪 R²。n>4000 拒绝（特征值 O(n³)）",
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔）",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("空间滞后模型", "sar", "ml估计", "空间回归", "lag"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def sar_ml_regression(geojson: Any, target_field: str, explanatory_fields: str,
                          weights_scheme: str = "knn", k: int = 8,
                          distance_band: float = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("sar_ml_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "weights_scheme": weights_scheme,
            "k": k,
        })
        res = _geo_lib.spatial_regression.sar_ml_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.sar_ml", tool="sar_ml_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="sem_ml_regression",
           description="空间误差模型 ML 估计（SEM：y=Xβ+u，u=λWu+ε）；"
                       "输出 λ、LR 检验（vs OLS）。n>4000 拒绝（特征值 O(n³)）",
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔）",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("空间误差模型", "sem", "ml估计", "空间回归"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def sem_ml_regression(geojson: Any, target_field: str, explanatory_fields: str,
                          weights_scheme: str = "knn", k: int = 8,
                          distance_band: float = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("sem_ml_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "weights_scheme": weights_scheme,
            "k": k,
        })
        res = _geo_lib.spatial_regression.sem_ml_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.sem_ml", tool="sem_ml_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="slx_regression",
           description="SLX 回归（y ~ X + W·X）：空间滞后解释变量直接进设计阵；"
                       "系数表含 WX 滞后项（邻居溢出效应的直接估计）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔）",
               "weights_scheme": "W·X 滞后的空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("slx", "空间滞后解释变量", "溢出效应", "空间回归"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def slx_regression(geojson: Any, target_field: str, explanatory_fields: str,
                       weights_scheme: str = "knn", k: int = 8,
                       distance_band: float = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("slx_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "weights_scheme": weights_scheme,
            "k": k,
        })
        res = _geo_lib.spatial_regression.slx_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.slx", tool="slx_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="gwr_regression",
           description="地理加权回归 GWR（Brunsdon 1996 / Fotheringham 2002）："
                       "自适应 bisquare 核，带宽=最近邻数（默认30，钳制[5,n/2]）；"
                       "输出局部 R² 摘要、逐系数空间变异、AICc；n≤2000 附系数面",
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔）",
               "bandwidth": "带宽 = 最近邻数（含自身，默认30，运行时钳制到 [5, n/2]）",
               "bandwidth_selection": "带宽选择：fixed(默认，用 bandwidth) / cv（有界网格留一交叉验证）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("gwr", "地理加权回归", "局部r2", "带宽", "空间异质性"),
           output_semantic_type="stats",
           result_size_policy="bounded",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def gwr_regression(geojson: Any, target_field: str, explanatory_fields: str,
                       bandwidth: int = 30, bandwidth_selection: str = "fixed") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("gwr_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "bandwidth": bandwidth,
            "bandwidth_selection": bandwidth_selection,
        })
        res = _geo_lib.spatial_regression.gwr_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            bandwidth=int(params["bandwidth"]),
            bandwidth_selection=str(params["bandwidth_selection"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.gwr", tool="gwr_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "bandwidth": int(params["bandwidth"]),
                    "bandwidth_selection": str(params["bandwidth_selection"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="weights_sensitivity",
           description="权重敏感性分析：在 knn(k)/queen/rook/distance_band(auto) 下重算全局 "
                       "Moran's I，报告逐方案 I/p/判读、ΔI 范围与结论稳定性；"
                       "结论随权重翻转时如实降级（queen/rook 对点输入跳过并披露）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
               "k": "knn 方案的邻居数（默认8）",
               "distance_band": "distance_band 阈值（米），0=按8近邻平均距离自动（默认）",
               "permutations": "逐方案置换次数：99(默认)/199/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("权重敏感性", "moran", "knn", "queen", "rook", "稳健性"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def weights_sensitivity(geojson: Any, value_field: str, k: int = 8,
                            distance_band: float = 0, permutations: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("weights_sensitivity_analysis", {
            "value_field": value_field,
            "k": k,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.weights_sensitivity_narrated(
            data, params["value_field"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.weights_sensitivity", tool="weights_sensitivity",
                parameters_applied={
                    "value_field": params["value_field"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    # ── Foundation V3 工具 ─────────────────────────────────────────────
    # 模式与上方 V2 工具一致：safe_parse → apply_contract → 实现 →
    # to_llm_response → _attach_scientific_evidence。

    @tool(registry, name="mgwr_regression",
    side_effect="deterministic_compute",
    tags=('gwr', 'mgwr', '空间回归', '地理加权'),
           description="多尺度地理加权回归 MGWR（Fotheringham 2017）：每个解释变量"
                       "（含截距项）独立带宽的 bisquare 反向拟合；输出逐项带宽、"
                       "逐观测系数面、逐项 ENP、AICc。n≤2000；反向拟合收敛到"
                       "局部最优（不保证全局最优，已披露）",
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "target_field": "因变量 y 的数值字段名",
               "explanatory_fields": "自变量字段名列表（逗号分隔）",
               "bandwidth": "全局带宽初值 = 最近邻数（含自身，默认30）；逐项带宽"
                            "由反向拟合的 LOO-CV 确定",
           })
    def mgwr_regression(geojson: Any, target_field: str, explanatory_fields: str,
                        bandwidth: int = 30) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("mgwr_analysis", {
            "target_field": target_field,
            "explanatory_fields": explanatory_fields,
            "bandwidth": bandwidth,
        })
        res = _geo_lib.spatial_regression.mgwr_regression_narrated(
            data, params["target_field"],
            _split_explanatory(params["explanatory_fields"]),
            bandwidth=int(params["bandwidth"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "spatial.mgwr", tool="mgwr_regression",
                parameters_applied={
                    "target_field": params["target_field"],
                    "explanatory_fields": params["explanatory_fields"],
                    "bandwidth": int(params["bandwidth"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="geodetector_ecological",
    side_effect="deterministic_compute",
    tags=('地理探测器', '生态探测', '分层比较', '解释力'),
           description="生态探测器（Wang 2010）：比较两个分层字段对同一数值字段的"
                       "解释力（SSW 比较 t 检验）；SSW 显著更小的一侧解释力显著占优"
                       "（df=n-2 保守选择，双侧 p）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "被解释的数值字段名",
               "strata_field_1": "第一分层字段名",
               "strata_field_2": "第二分层字段名",
               "bins": "数值分层字段的分位数分箱数（2-20）；0=按原值类别（≤12 唯一值时）",
           })
    def geodetector_ecological(geojson: Any, value_field: str,
                               strata_field_1: str, strata_field_2: str,
                               bins: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("geodetector_ecological_analysis", {
            "value_field": value_field,
            "strata_field_1": strata_field_1,
            "strata_field_2": strata_field_2,
            "bins": bins,
        })
        res = _geo_lib.statistics.geodetector_ecological_narrated(
            data, params["value_field"],
            params["strata_field_1"], params["strata_field_2"],
            bins=int(params["bins"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.geodetector_ecological",
                tool="geodetector_ecological",
                parameters_applied={
                    "value_field": params["value_field"],
                    "strata_field_1": params["strata_field_1"],
                    "strata_field_2": params["strata_field_2"],
                    "bins": int(params["bins"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
            )
        return payload

    @tool(registry, name="geodetector_risk",
    side_effect="deterministic_compute",
    tags=('地理探测器', '风险探测', '均值差异', '分层'),
           description="风险探测器（Wang 2010）：逐分层对的均值差显著性"
                       "（Welch t + 可选固定种子42的标签置换复核）；"
                       "输出对列表与方向矩阵，p<0.05 才判 higher/lower",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "被解释的数值字段名",
               "strata_field": "分层字段名（类别，或数值字段+bins 分箱）",
               "bins": "数值分层字段的分位数分箱数（2-20）；0=按原值类别（≤12 唯一值时）",
               "permutations": "逐对置换复核次数：0(默认)=只用 Welch t / 99/199/499/999，固定种子42",
           })
    def geodetector_risk(geojson: Any, value_field: str, strata_field: str,
                         bins: int = 0, permutations: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("geodetector_risk_analysis", {
            "value_field": value_field,
            "strata_field": strata_field,
            "bins": bins,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.geodetector_risk_narrated(
            data, params["value_field"], params["strata_field"],
            bins=int(params["bins"]),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.geodetector_risk", tool="geodetector_risk",
                parameters_applied={
                    "value_field": params["value_field"],
                    "strata_field": params["strata_field"],
                    "bins": int(params["bins"]),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42 if int(params["permutations"]) > 0 else None,
            )
        return payload

    @tool(registry, name="local_join_count",
    side_effect="deterministic_compute",
    tags=('join_count', '共位簇', '二值场', '空间自相关'),
           description="局部 Join Count（Anselin & Li 2019）：二值(0/1)场的逐位置"
                       "共位簇检测（LJC_i=邻域同类连接数）；条件置换 p（固定种子42）"
                       "+ BH 多重校正；y=0 位置恒中性。非二值字段会被拒绝",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "binary_field": "二值（0/1）字段名；含其他值会被拒绝",
               "weights_scheme": "二值对称权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "条件置换次数：999(默认)/99/199/499，固定种子42",
               "correction": "多重校正：bh(默认)/bonferroni/holm/none",
           })
    def local_join_count(geojson: Any, binary_field: str,
                         weights_scheme: str = "knn", k: int = 8,
                         distance_band: float = 0, permutations: int = 999,
                         correction: str = "bh") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("local_join_count_analysis", {
            "binary_field": binary_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
            "correction": correction,
        })
        res = _geo_lib.statistics.local_join_count_narrated(
            data, params["binary_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
            correction=str(params["correction"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.local_join_count", tool="local_join_count",
                parameters_applied={
                    "binary_field": params["binary_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                    "correction": str(params["correction"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="bivariate_local_moran",
    side_effect="deterministic_compute",
    tags=('lisa', '双变量', '局部莫兰', '空间自相关'),
           description="双变量局部 Moran（esda 委托，固定种子42）：x 与 y 空间滞后"
                       "的逐位置共位/互斥检测（HH/LH/LL/HL 标签 + BH q 值）；"
                       "共位相关不能解释为因果/超前-滞后关系",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "x 的数值字段名",
               "lag_field": "y 的数值字段名（取其空间滞后 W·y）",
               "weights_scheme": "空间权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重的距离阈值（米），0=自动（默认）",
               "permutations": "条件随机化次数：999(默认)/99/199/499，固定种子42",
           })
    def bivariate_local_moran(geojson: Any, value_field: str, lag_field: str,
                              weights_scheme: str = "knn", k: int = 8,
                              distance_band: float = 0,
                              permutations: int = 999) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("bivariate_local_moran_analysis", {
            "value_field": value_field,
            "lag_field": lag_field,
            "weights_scheme": weights_scheme,
            "k": k,
            "permutations": permutations,
        })
        res = _geo_lib.statistics.bivariate_local_moran_narrated(
            data, params["value_field"], params["lag_field"],
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
            permutations=int(params["permutations"]),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.bivariate_local_moran",
                tool="bivariate_local_moran",
                parameters_applied={
                    "value_field": params["value_field"],
                    "lag_field": params["lag_field"],
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                    "permutations": int(params["permutations"]),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
                uncertainty=_coerce_uncertainty_blocks(res.data),
                seed=42,
            )
        return payload

    @tool(registry, name="weights_diagnostics",
    side_effect="deterministic_compute",
    tags=('空间权重', '诊断', '邻接结构', '孤岛'),
           description="空间权重诊断：给定权重方案（knn/queen/rook/distance_band）"
                       "的结构体检——稀疏度/对称性/行标准化/邻居分布/孤岛/连通分量，"
                       "并给结构警告（孤岛、不对称、多分量）。确定性、零随机",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "weights_scheme": "待诊断的权重方案：'knn'(默认) / 'queen' / 'rook'（需面要素）/ 'distance_band'",
               "k": "kNN 邻居数（仅 knn 方案，默认8）",
               "distance_band": "distance_band 权重阈值（米），0=按8近邻平均距离自动（默认）",
           })
    def weights_diagnostics(geojson: Any, weights_scheme: str = "knn",
                            k: int = 8, distance_band: float = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("weights_diagnostics_analysis", {
            "weights_scheme": weights_scheme,
            "k": k,
        })
        res = _geo_lib.statistics.weights_diagnostics_narrated(
            data,
            weights_scheme=params["weights_scheme"],
            k=int(params["k"]),
            distance_band=float(distance_band or 0),
        )
        payload = res.to_llm_response()
        if res.success:
            _attach_scientific_evidence(
                payload, "stats.weights_diagnostics", tool="weights_diagnostics",
                parameters_applied={
                    "weights_scheme": params["weights_scheme"],
                    "k": int(params["k"]),
                    "distance_band": float(distance_band or 0),
                },
                feature_count=res.data.get("n_features"),
                crs=extract_declared_crs(data) or "EPSG:4326",
            )
        return payload
