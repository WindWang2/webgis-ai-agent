"""点格局 V2 工具（Spatial Algorithms Foundation V2 · Domain A3）。

G/F/J 距离函数、成对相关函数 g(r)、双变量交叉 K（随机标记）、
Knox 时空交互检验、Ripley K + CSR 模拟包络。实现层在
app/lib/geo_analysis/point_pattern.py；本模块只做工具层职责
（ADR-0099 §1）：validate（契约）→ 调实现 → 挂科学证据块。

与 app/tools/spatial_stats.py 的 ripley_k_analysis / quadrat_analysis
同款模式：@tool 注册、apply_contract 收敛参数、
build_evidence 挂 descriptor 驱动的证据块 + 类型化不确定性块
（MonteCarloSummary / StatisticalSignificance）。确定性：所有模拟
固定种子 42（与 descriptor random_seed_policy="fixed_seed" 一致）。
"""
import logging
from typing import Any, List, Optional

from app.tools.registry import ToolRegistry, tool
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson
from app.lib.geo_processor.core import extract_declared_crs, to_utm_gdf
from app.lib.geo_analysis._vector import extract_centroids
from app.lib.geo_analysis.point_pattern import (
    cross_k,
    g_f_j_functions,
    knox_test,
    pcf,
    ripley_k,
)
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_errors import MissingRequiredField, NoValidObservations
from app.lib.gis.scientific_evidence import Diagnostic, build_evidence
from app.lib.gis.uncertainty import MonteCarloSummary, StatisticalSignificance

logger = logging.getLogger(__name__)

# 与 st_dbscan 同款时间字段回退序（ST-DBSCAN 约定）。
_TIME_FIELD_FALLBACKS = ["timestamp", "time", "datetime", "t", "date", "created_at"]


def _attach_scientific_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    feature_count: Optional[int],
    crs: str = "",
    uncertainty: Optional[list] = None,
    diagnostics: Optional[List[Diagnostic]] = None,
    seed: Optional[int] = None,
) -> dict:
    """Attach the VNext scientific-evidence block to a tool payload.

    Descriptor is the single source of assumptions/limitations/references;
    the implementation supplies the uncertainty blocks; ``diagnostics``
    carries the backend-selection trace (Foundation V2 A7).
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


def _backend_diagnostic(algorithm_id: str, feature_count: int) -> List[Diagnostic]:
    """Honest backend trace for descriptors without declared variants
    (records the default tool path — no variant fiction)."""
    try:
        decision = select_backend(algorithm_id, ScaleProfile(feature_count=feature_count))
        # Diagnostic.value is numeric — the variant id goes in text only.
        return [Diagnostic(
            name="backend_selection",
            text=f"variant={decision.variant_id or '(default)'}; {decision.rationale}",
        )]
    except Exception as exc:  # noqa: BLE001 — 诊断是 best-effort，不阻塞分析
        logger.warning("backend_selection diagnostic failed for %s: %s", algorithm_id, exc)
        return []


def _metric_xy(data: dict):
    """Parse → auto-UTM project → (n, 2) metric centroid array + gdf."""
    res = to_utm_gdf(data)
    if res is None or res[0] is None:
        raise NoValidObservations("输入无有效点要素")
    gdf, _ = res
    return extract_centroids(gdf), gdf


def register_point_pattern_tools(registry: ToolRegistry):

    @tool(registry, name="g_f_j_analysis",
           description="G/F/J 距离函数点格局分析：G(最近邻距离CDF)/F(空空间函数)/J=(1-G)/(1-F)，"
                       "对比 CSR 参考；可选固定种子 CSR 模拟包络（G/F 秩双侧 p 值）。"
                       "原始估计（无边缘校正，如实披露）；需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "n_steps": "r 网格步数（4-32，默认10）",
               "max_distance_ratio": "r_max = 比例×min(窗宽,窗高)，0.05-0.5（默认0.25）",
               "envelopes": "CSR 模拟包络次数（0-499，固定种子42）；0=关（默认，仅描述性输出）",
           },
           side_effect="deterministic_compute",
           deterministic=True,
           network=False,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           tags=("点格局", "g函数", "f函数", "最近邻", "csr", "聚集分析"),
           failure_modes=("invalid_args", "empty_result"),
           )
    def g_f_j_analysis(geojson: Any, n_steps: int = 10,
                       max_distance_ratio: float = 0.25, envelopes: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("g_f_j_analysis", {
            "n_steps": n_steps,
            "max_distance_ratio": max_distance_ratio,
            "envelopes": envelopes,
        })
        declared_crs = extract_declared_crs(data) or "EPSG:4326"
        xy, _ = _metric_xy(data)
        result = g_f_j_functions(
            xy,
            n_steps=int(params["n_steps"]),
            max_distance_ratio=float(params["max_distance_ratio"]),
            envelopes=int(params["envelopes"]),
        )
        payload = {"success": True, "summary": result["summary"], "data": result}
        uncertainty = []
        seed = None
        if int(params["envelopes"]) > 0:
            n_draws = int(params["envelopes"])
            seed = 42
            last = -1  # r_max（最后一步）处的包络分位数
            uncertainty.append(MonteCarloSummary(
                target="g_function_csr",
                draws=n_draws, seed=42,
                quantiles={
                    "p5": result["envelope_G_low"][last],
                    "p50": result["envelope_G_median"][last],
                    "p95": result["envelope_G_high"][last],
                },
                probability_statements=[
                    f"CSR 零假设下 G(r_max) 的固定种子包络（{n_draws} 次模拟，seed=42）",
                    f"双侧秩 p（G at r_max, +1 校正）= {result.get('G_p_value')}",
                    f"双侧秩 p（F at r_max, +1 校正）= {result.get('F_p_value')}",
                ],
            ))
            uncertainty.append(StatisticalSignificance(
                target="g_function",
                statistic_name="G(r_max)",
                statistic_value=result["G"][-1],
                p_value=result.get("G_p_value"),
                method="permutation",
                permutations=n_draws,
                alternative="two-sided",
            ))
            uncertainty.append(StatisticalSignificance(
                target="f_function",
                statistic_name="F(r_max)",
                statistic_value=result["F"][-1],
                p_value=result.get("F_p_value"),
                method="permutation",
                permutations=n_draws,
                alternative="two-sided",
            ))
        _attach_scientific_evidence(
            payload, "point_pattern.g_f_j", tool="g_f_j_analysis",
            parameters_applied={
                "n_steps": int(params["n_steps"]),
                "max_distance_ratio": float(params["max_distance_ratio"]),
                "envelopes": int(params["envelopes"]),
            },
            feature_count=result.get("n"),
            crs=declared_crs,
            uncertainty=uncertainty,
            diagnostics=_backend_diagnostic("point_pattern.g_f_j", int(result.get("n", 0))),
            seed=seed,
        )
        return payload

    @tool(registry, name="pcf_analysis",
           description="成对相关函数 g(r)=K'(r)/(2πr)（Epanechnikov 平滑）：g>1 聚集 / g<1 规则 / g≈1 随机；"
                       "可选固定种子 CSR 模拟包络（sup|g-1| 秩检验）。需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "n_steps": "r 网格步数（4-32，默认10）",
               "max_distance_ratio": "r_max = 比例×min(窗宽,窗高)，0.05-0.5（默认0.25）",
               "bandwidth": "Epanechnikov 平滑带宽（米，r 单位）；0=自动（一个 r 步宽，默认）",
               "envelopes": "CSR 模拟包络次数（0-499，固定种子42）；0=关（默认）",
           },
           side_effect="deterministic_compute",
           deterministic=True,
           network=False,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           tags=("点格局", "成对相关函数", "pcf", "聚集", "空间模式", "平滑"),
           failure_modes=("invalid_args", "empty_result"),
           )
    def pcf_analysis(geojson: Any, n_steps: int = 10, max_distance_ratio: float = 0.25,
                     bandwidth: float = 0, envelopes: int = 0) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("pcf_analysis", {
            "n_steps": n_steps,
            "max_distance_ratio": max_distance_ratio,
            "bandwidth": bandwidth,
            "envelopes": envelopes,
        })
        declared_crs = extract_declared_crs(data) or "EPSG:4326"
        xy, _ = _metric_xy(data)
        result = pcf(
            xy,
            n_steps=int(params["n_steps"]),
            max_distance_ratio=float(params["max_distance_ratio"]),
            bandwidth=float(params["bandwidth"]),
            envelopes=int(params["envelopes"]),
        )
        payload = {"success": True, "summary": result["summary"], "data": result}
        uncertainty = []
        seed = None
        if int(params["envelopes"]) > 0:
            n_draws = int(params["envelopes"])
            seed = 42
            uncertainty.append(MonteCarloSummary(
                target="pcf_csr",
                draws=n_draws, seed=42,
                quantiles={
                    "p5": result["envelope_g_low"][-1],
                    "p50": result["envelope_g_median"][-1],
                    "p95": result["envelope_g_high"][-1],
                },
                probability_statements=[
                    f"CSR 零假设下 g(r) 的固定种子包络（{n_draws} 次模拟，seed=42）",
                    f"sup|g-1| 秩检验 p（+1 校正）= {result.get('p_value')}",
                ],
            ))
            uncertainty.append(StatisticalSignificance(
                target="pcf",
                statistic_name="sup|g(r)-1|",
                statistic_value=result.get("sup_abs_g_minus_1"),
                p_value=result.get("p_value"),
                method="permutation",
                permutations=n_draws,
                alternative="greater",
            ))
        _attach_scientific_evidence(
            payload, "point_pattern.pcf", tool="pcf_analysis",
            parameters_applied={
                "n_steps": int(params["n_steps"]),
                "max_distance_ratio": float(params["max_distance_ratio"]),
                "bandwidth": float(params["bandwidth"]),
                "envelopes": int(params["envelopes"]),
            },
            feature_count=result.get("n"),
            crs=declared_crs,
            uncertainty=uncertainty,
            diagnostics=_backend_diagnostic("point_pattern.pcf", int(result.get("n", 0))),
            seed=seed,
        )
        return payload

    @tool(registry, name="cross_k_analysis",
           description="双变量交叉 K 函数（随机标记检验）：K12(r) 对比 πr²，判断两类点空间吸引/分离；"
                       "类型标签置换包络（固定种子42）。type_field 必须恰有 2 个取值；需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "type_field": "类型字段名（必须恰有 2 个不同取值，每类至少 5 点）",
               "n_steps": "r 网格步数（4-32，默认10）",
               "max_distance_ratio": "r_max = 比例×min(窗宽,窗高)，0.05-0.5（默认0.25）",
               "permutations": "随机标记置换次数：99/199(默认)/499，固定种子42",
           },
           side_effect="deterministic_compute",
           deterministic=True,
           network=False,
           latency_class="slow",
           memory_class="medium",
           scale_class="medium",
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           tags=("点格局", "交叉k函数", "双变量", "随机标记", "空间吸引", "两类点"),
           failure_modes=("invalid_args", "missing_data", "empty_result"),
           )
    def cross_k_analysis(geojson: Any, type_field: str, n_steps: int = 10,
                         max_distance_ratio: float = 0.25,
                         permutations: str = "199") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("cross_k_analysis", {
            "type_field": type_field,
            "n_steps": n_steps,
            "max_distance_ratio": max_distance_ratio,
            "permutations": permutations,
        })
        declared_crs = extract_declared_crs(data) or "EPSG:4326"
        xy, gdf = _metric_xy(data)
        field = str(params["type_field"])
        if field not in gdf.columns:
            raise MissingRequiredField(
                f"type field '{field}' not found in feature properties",
                correction_hint="pass an existing categorical property with exactly 2 distinct values",
            )
        values = gdf[field]
        valid = values.notna().to_numpy()
        n_null = int((~valid).sum())
        result = cross_k(
            xy[valid], values[valid].tolist(),
            n_steps=int(params["n_steps"]),
            max_distance_ratio=float(params["max_distance_ratio"]),
            permutations=int(params["permutations"]),
        )
        if n_null:
            result["n_type_null_dropped"] = n_null
            result["type_null_note"] = f"{n_null} 行类型字段为空，已剔除后检验"
        payload = {"success": True, "summary": result["summary"], "data": result}
        uncertainty = []
        seed = None
        if int(params["permutations"]) > 0:
            n_perm = int(params["permutations"])
            seed = 42
            uncertainty.append(MonteCarloSummary(
                target="cross_k_random_labelling",
                draws=n_perm, seed=42,
                quantiles={
                    "p5": result["envelope_K12_low"][-1],
                    "p50": result["envelope_K12_median"][-1],
                    "p95": result["envelope_K12_high"][-1],
                },
                probability_statements=[
                    f"随机标记零假设下 K12(r_max) 包络（{n_perm} 次置换，seed=42）",
                    f"max|K12−πr²| 秩检验 p（+1 校正）= {result.get('p_value')}",
                ],
            ))
            uncertainty.append(StatisticalSignificance(
                target="cross_k",
                statistic_name="max|K12(r)-pi*r^2|",
                statistic_value=result.get("sup_abs_dev"),
                p_value=result.get("p_value"),
                method="permutation",
                permutations=n_perm,
                alternative="greater",
            ))
        _attach_scientific_evidence(
            payload, "point_pattern.cross_k", tool="cross_k_analysis",
            parameters_applied={
                "type_field": field,
                "n_steps": int(params["n_steps"]),
                "max_distance_ratio": float(params["max_distance_ratio"]),
                "permutations": int(params["permutations"]),
            },
            feature_count=result.get("n"),
            crs=declared_crs,
            uncertainty=uncertainty,
            diagnostics=_backend_diagnostic("point_pattern.cross_k", int(result.get("n", 0))),
            seed=seed,
        )
        return payload

    @tool(registry, name="knox_analysis",
           description="Knox 时空交互检验：同时落在空间阈值（米）与时间阈值（秒）内的事件对数对比独立期望；"
                       "时间置换 p 值（固定种子42）。时间戳解析与 ST-DBSCAN 同约定（ISO-8601/Epoch）；"
                       "critical_distance=0 自动取中位最近邻距离（披露）。需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "含时间戳的点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "time_field": "时间戳字段名（ISO-8601 字符串或 Epoch 数值；NaT 行剔除并披露）",
               "critical_distance": "空间阈值（米）；0=自动取中位最近邻距离（默认，输出披露）",
               "critical_time": "时间阈值（秒，必须为正）",
               "permutations": "时间置换次数：99/199(默认)/499/999，固定种子42",
           },
           side_effect="deterministic_compute",
           deterministic=True,
           network=False,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           tags=("knox", "时空交互", "时空检验", "事件对", "near-repeat", "时空聚集"),
           failure_modes=("invalid_args", "missing_data", "empty_result"),
           )
    def knox_analysis(geojson: Any, time_field: str, critical_time: float,
                      critical_distance: float = 0,
                      permutations: str = "199") -> dict:
        import pandas as pd

        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("knox_analysis", {
            "time_field": time_field,
            "critical_distance": critical_distance,
            "critical_time": critical_time,
            "permutations": permutations,
        })
        declared_crs = extract_declared_crs(data) or "EPSG:4326"
        xy, gdf = _metric_xy(data)

        # 时间字段解析（与 st_dbscan 同约定：声明字段优先，缺省回退常见命名）。
        field = str(params["time_field"])
        if field not in gdf.columns:
            fallback = [f for f in _TIME_FIELD_FALLBACKS if f in gdf.columns]
            if not fallback:
                raise MissingRequiredField(
                    f"time field '{field}' not found in feature properties",
                    correction_hint="pass the property holding ISO-8601/epoch timestamps",
                )
            field = fallback[0]
        parsed = pd.to_datetime(gdf[field], errors="coerce", utc=True)
        valid = parsed.notna().to_numpy()
        n_dropped = int((~valid).sum())
        t_seconds = parsed[valid].astype("int64").to_numpy() / 1e9

        result = knox_test(
            xy[valid], t_seconds,
            critical_distance=float(params["critical_distance"]),
            critical_time=float(params["critical_time"]),
            permutations=int(params["permutations"]),
        )
        result["time_field"] = field
        if n_dropped:
            result["time_dropped_note"] = (
                f"时间字段 '{field}' 有 {n_dropped} 行不可解析（NaT），已剔除后检验"
            )
        payload = {"success": True, "summary": result["summary"], "data": result}
        uncertainty: list = []
        seed = None
        if int(params["permutations"]) > 0:
            n_perm = int(params["permutations"])
            seed = 42
            uncertainty.append(MonteCarloSummary(
                target="knox_time_permutation",
                draws=n_perm, seed=42,
                quantiles=dict(result.get("perm_quantiles", {})),
                probability_statements=[
                    f"时间置换零假设下联合对数包络（{n_perm} 次置换，seed=42）",
                    f"单侧 greater 秩 p（+1 校正）= {result.get('p_value')}",
                ],
            ))
            uncertainty.append(StatisticalSignificance(
                target="knox",
                statistic_name="space-time joint pairs",
                statistic_value=float(result.get("observed", 0)),
                p_value=result.get("p_value"),
                method="permutation",
                permutations=n_perm,
                alternative="greater",
            ))
        _attach_scientific_evidence(
            payload, "spatiotemporal.knox", tool="knox_analysis",
            parameters_applied={
                "time_field": field,
                "critical_distance": float(params["critical_distance"]),
                "critical_time": float(params["critical_time"]),
                "permutations": int(params["permutations"]),
            },
            feature_count=result.get("n"),
            crs=declared_crs,
            uncertainty=uncertainty,
            diagnostics=_backend_diagnostic("spatiotemporal.knox", int(result.get("n", 0))),
            seed=seed,
        )
        return payload

    @tool(registry, name="ripley_k_envelope_analysis",
           description="Ripley's K + 固定种子 CSR 模拟包络：K(r)/L(r) 与逐半径 p5/p50/p95 包络 + 秩双侧 p 值"
                       "（各向同性边缘校正）。与 ripley_k_analysis 同一估计器；需米制坐标（自动投影UTM）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "n_steps": "r 网格步数（4-32，默认10）",
               "max_distance_ratio": "r_max = 比例×min(窗宽,窗高)，0.05-0.5（默认0.25）",
               "envelopes": "CSR 模拟包络次数（1-499，默认99，固定种子42）",
           },
           side_effect="deterministic_compute",
           deterministic=True,
           network=False,
           latency_class="slow",
           memory_class="medium",
           scale_class="medium",
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           unit_semantics="meters",
           tags=("ripley", "k函数", "点格局", "包络", "csr检验", "空间聚集"),
           failure_modes=("invalid_args", "empty_result"),
           )
    def ripley_k_envelope_analysis(geojson: Any, n_steps: int = 10,
                                   max_distance_ratio: float = 0.25,
                                   envelopes: int = 99) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        params = apply_contract("ripley_k_envelope_analysis", {
            "n_steps": n_steps,
            "max_distance_ratio": max_distance_ratio,
            "envelopes": envelopes,
        })
        declared_crs = extract_declared_crs(data) or "EPSG:4326"
        xy, _ = _metric_xy(data)
        n_draws = int(params["envelopes"])
        result = ripley_k(
            xy,
            n_steps=int(params["n_steps"]),
            max_distance_ratio=float(params["max_distance_ratio"]),
            envelopes=n_draws,
        )
        payload = {"success": True, "summary": result["summary"], "data": result}
        p_at_rmax = (result.get("K_p_values") or [None])[-1]
        uncertainty = [
            MonteCarloSummary(
                target="ripley_k_csr",
                draws=n_draws, seed=42,
                quantiles={
                    "p5": result["envelope_K_low"][-1],
                    "p50": result["envelope_K_median"][-1],
                    "p95": result["envelope_K_high"][-1],
                },
                probability_statements=[
                    f"CSR 零假设下 K(r_max) 的固定种子包络（{n_draws} 次模拟，seed=42）",
                    f"双侧秩 p（K at r_max, +1 校正）= {p_at_rmax}",
                ],
            ),
            StatisticalSignificance(
                target="ripley_k",
                statistic_name="K(r_max)",
                statistic_value=result["K"][-1],
                p_value=p_at_rmax,
                method="permutation",
                permutations=n_draws,
                alternative="two-sided",
            ),
        ]
        _attach_scientific_evidence(
            payload, "point_pattern.ripley_k_env", tool="ripley_k_envelope_analysis",
            parameters_applied={
                "n_steps": int(params["n_steps"]),
                "max_distance_ratio": float(params["max_distance_ratio"]),
                "envelopes": n_draws,
            },
            feature_count=result.get("n"),
            crs=declared_crs,
            uncertainty=uncertainty,
            diagnostics=_backend_diagnostic("point_pattern.ripley_k_env", int(result.get("n", 0))),
            seed=42,
        )
        return payload
