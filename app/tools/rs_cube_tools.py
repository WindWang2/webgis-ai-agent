"""RS Temporal Cube 工具面 —— 时序立方体/对齐/融合/样本的 Agent 入口。

独立工具模块（避免与 remote_sensing.py / science_temporal_tools.py 的
并发冲突面）；经 ``app/tools/remote_sensing.py::register_rs_tools`` 尾部
接线注册（``app/tools/__init__.py`` 冻结——#1336 热区，不碰）。

实现层（唯一事实源）位于 ``app/lib/geo_analysis/rs_{cube_descriptor,
alignment,gaps,features,fusion,samples,cube_pipeline}.py``——本模块只做
参数校验 + 薄包装 + 有界输出。

refs-only 纪律：描述符走 JSON 通道；内联数组仅用于 tiny 样例
（≤ ``_TOOL_ARRAY_MAX_VALUES`` 值/数组），完整栅格走 artifact/ref 通道。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

#: 内联数组规模闸（与 remote_sensing._TOOL_ARRAY_MAX_VALUES 同口径）。
_TOOL_ARRAY_MAX_VALUES = 4_000_000


def _as_ndarray(x: Any, name: str, *, max_values: int = _TOOL_ARRAY_MAX_VALUES):
    import numpy as np

    if x is None:
        raise ValueError(f"{name} 不能为空")
    try:
        arr = np.asarray(x, dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} 无法解析为数值数组: {e}") from e
    if arr.size > max_values:
        from app.lib.gis.scientific_errors import ResourceScaleMismatch

        raise ResourceScaleMismatch(
            f"{name} 像元数 {arr.size} 超过内联数组工具上界 {max_values}",
            estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB)",
            limit=f"≤{max_values} values",
            correction_hint="完整栅格走 artifact/ref 通道（refs-only 纪律）",
        )
    return arr


def _bounded_features(x: Any, name: str) -> Dict[str, "np.ndarray"]:
    import numpy as np

    if not isinstance(x, dict) or not x:
        raise ValueError(f"{name} 必须是非空 {{name: 2D array}} 字典")
    out: Dict[str, "np.ndarray"] = {}
    shape = None
    for k, v in x.items():
        arr = _as_ndarray(v, f"{name}[{k!r}]")
        if arr.ndim != 2:
            raise ValueError(f"{name}[{k!r}] 必须是 2D 数组")
        if shape is None:
            shape = arr.shape
        elif arr.shape != shape:
            raise ValueError(f"{name} 各特征形状不一致")
        out[str(k)] = arr
    return out


def _feature_summaries(features: Dict[str, "np.ndarray"]) -> dict:
    import numpy as np

    out: dict = {}
    for name, arr in features.items():
        finite = arr[np.isfinite(arr)]
        out[name] = {
            "valid_pixels": int(finite.size),
            "quantiles": (
                [round(float(np.quantile(finite, q)), 6)
                 for q in (0.05, 0.5, 0.95)] if finite.size
                else [None, None, None]),
        }
    return out


def register_rs_cube_tools(registry: ToolRegistry):
    """注册 RS Temporal Cube 工具面（由 register_rs_tools 尾部调用）。"""

    @tool(registry, name="rs_cube_describe",
          description=(
              "时序立方体描述符盘点：refs-only 资产/时间/波段/极化/网格/"
              "质量/缺口账的机器可读清单 → 有界上下文摘要（覆盖摘要 + "
              "资产样本），绝不加载栅格 payload。"
              "\n何时用：时序分析前的数据体检与 planner 兼容性裁决。"
              "\n失败语义：资产表为空/时间不可解析/缺口码词表外 → 结构化拒绝。"),
          tier=2, domains=["remote_sensing", "temporal"], cost="light",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("时序立方体", "cube", "SAR", "光学", "rs-cube"),
          failure_modes=("invalid_args", "missing_data"))
    def rs_cube_describe(descriptor: dict) -> dict:
        from app.lib.geo_analysis import rs_cube_descriptor as rcd

        d = rcd.TemporalRasterCubeDescriptor(**descriptor)
        return {"summary": d.to_context_summary(),
                "coverage": d.coverage_summary()}

    @tool(registry, name="rs_cube_align",
          description=(
              "光学×SAR 获取对齐：跨模态配对计划（容差内最近邻、一景 SAR "
              "至多服务一期光学）+ 类型化缺口账 + 覆盖卡。"
              "\n何时用：SAR 与光学时序联合分析前的时间轴配对。"
              "\n失败语义：网格恒等（crs/宽高/transform）不一致 → typed "
              "拒绝（绝不静默重采样）；配不上的获取记 missing_acquisition，"
              "不伪造资产 ref。"),
          tier=2, domains=["remote_sensing", "temporal"], cost="light",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("对齐", "配对", "SAR", "光学", "缺口", "rs-cube"),
          failure_modes=("invalid_args", "grid_mismatch", "missing_data"))
    def rs_cube_align(optical_descriptor: dict,
                      sar_descriptor: dict,
                      tolerance_days: int = 3) -> dict:
        from app.lib.geo_analysis import rs_alignment, rs_cube_descriptor as rcd
        from app.lib.geo_analysis import rs_gaps

        optical = rcd.TemporalRasterCubeDescriptor(**optical_descriptor)
        sar = rcd.TemporalRasterCubeDescriptor(**sar_descriptor)
        plan = rs_alignment.align_acquisitions(
            optical, sar, tolerance_days=int(tolerance_days))
        return {
            "coverage": plan.coverage,
            "slot_table": [dict(s) for s in rs_gaps.joint_slot_table(plan)],
            "coverage_card": rs_gaps.build_coverage_card(plan),
            "aligned_descriptor": plan.aligned_descriptor.to_context_summary(),
            "disclosures": list(plan.disclosures),
        }

    @tool(registry, name="rs_temporal_feature_pack",
          description=(
              "cube 级时序特征包：分位数（p10/p50/p90）+ Sen 稳健斜率 + "
              "CUSUM 变点 + 基础统计/harmonic（复用 rs_v3）。"
              "\n何时用：年度/季节动态、趋势方向、变点定位的特征提取。"
              "\n诚实边界：NaN 传播（无效切片不充当 0）；T>24 斜率诚实跳过；"
              "完整特征面走 artifact/ref 通道——本工具返回逐特征有界摘要。"),
          tier=2, domains=["remote_sensing", "temporal"], cost="medium",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="medium", memory_class="heavy", scale_class="medium",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("时序特征", "Sen 斜率", "变点", "分位数", "rs-cube"),
          failure_modes=("invalid_args", "missing_data", "memory"))
    def rs_temporal_feature_pack(stack: Any, times: Any,
                                 nodata: Optional[float] = None) -> dict:
        from app.lib.geo_analysis import rs_features

        arr = _as_ndarray(stack, "stack")
        t = _as_ndarray(times, "times")
        pack = rs_features.temporal_feature_pack(arr, t, nodata=nodata)
        return {
            "meta": pack["meta"],
            "feature_summaries": _feature_summaries(pack["features"]),
        }

    @tool(registry, name="rs_joint_fusion_stack",
          description=(
              "SAR×光学联合特征栈：optical::/sar:: 命名空间合成 + 逐像元"
              "类型化覆盖码（none/optical-only/sar-only/both）+ 晚期证据"
              "融合（描述性加权与符号一致性；可选）。"
              "\n何时用：跨模态联合分析（如 NDVI 动态 × VV 后向散射）。"
              "\n诚实边界：单模态缺失是类型化覆盖语义，不是 0；conflict "
              "像元需人工复核。"),
          tier=2, domains=["remote_sensing"], cost="medium",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="medium", memory_class="medium", scale_class="medium",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("融合", "SAR", "光学", "覆盖", "rs-cube"),
          failure_modes=("invalid_args", "shape_mismatch", "missing_data"))
    def rs_joint_fusion_stack(
        optical_features: dict,
        sar_features: dict,
        evidence_optical: Optional[Any] = None,
        evidence_sar: Optional[Any] = None,
    ) -> dict:
        from app.lib.geo_analysis import rs_fusion

        opt = _bounded_features(optical_features, "optical_features")
        sar = _bounded_features(sar_features, "sar_features")
        out = rs_fusion.build_joint_feature_stack(opt, sar)
        result: dict = {
            "feature_names": out["feature_names"],
            "meta": out["meta"],
            "feature_summaries": _feature_summaries(out["features"]),
        }
        if evidence_optical is not None and evidence_sar is not None:
            late = rs_fusion.late_evidence_fusion(
                _as_ndarray(evidence_optical, "evidence_optical"),
                _as_ndarray(evidence_sar, "evidence_sar"))
            result["late_fusion"] = {
                "agreement_counts": late["meta"]["agreement_counts"],
                "agreement_fractions": late["meta"]["agreement_fractions"],
                "fused_summary": _feature_summaries(
                    {"fused": late["fused"]})["fused"],
                "disclosures": late["meta"]["disclosures"],
            }
        return result

    @tool(registry, name="rs_cube_sample_split",
          description=(
              "样本挂接与防泄漏分割：多边形样本特征 → (X,y) 矩阵 → 地理"
              "分块折（同 block 必同 fold）/ 时间前向链（严格无 future "
              "leakage）。"
              "\n何时用：样本分类/回归前的空间或时间外推验证设计。"
              "\n诚实边界：全 NaN 样本按 min_valid_features 排除并计数；"
              "分块限制（非消除）空间泄漏——不变量随表披露。"),
          tier=2, domains=["remote_sensing", "sampling"], cost="light",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("样本", "分割", "泄漏", "交叉验证", "rs-cube"),
          failure_modes=("invalid_args", "missing_data"))
    def rs_cube_sample_split(
        records: list,
        feature_names: list,
        *,
        min_valid_features: int = 1,
        spatial_folds: int = 4,
    ) -> dict:
        from app.lib.geo_analysis import rs_samples

        matrix = rs_samples.build_sample_matrix(
            records, feature_names,
            min_valid_features=int(min_valid_features))
        result: dict = {
            "matrix_meta": matrix["meta"],
            "excluded_ids": matrix["excluded_ids"],
        }
        if matrix["xy"].size and bool(
                (matrix["xy"] == matrix["xy"]).all().all()):
            split = rs_samples.geographic_block_split(
                matrix["xy"], folds=int(spatial_folds))
            result["spatial_split"] = split["meta"]
            result["fold_assignment_bounded"] = {
                str(i): int(f) for i, f in enumerate(split["fold"][:256])}
        return result
