"""Science Temporal Tools（science-v5 W8）—— 物候/时空立方体工具面。

独立工具模块（避免与 remote_sensing.py / temporal_tools.py 的并发
冲突面）；实现位于 app/lib/geo_analysis/temporal_cube.py 与
phenology.py（唯一事实源，本模块只做参数校验 + 薄包装 + 有界输出）。

输入契约：栅格时序栈以 ``stack``（嵌套列表或 JSON 数组 (T,H,W)）+
``times``（epoch/相对秒，与 T 等长）传入；``nodata``/``cloud_mask``
可选。输出有界：特征统计摘要 + 元数据（完整特征面建议走 artifact/ref
通道——本工具返回统计表 + 每特征 (H,W) 的分位数摘要，不搬运完整栅格）。

诚实语义：与实现层一致——长缺口不外推、排除像元计数披露、单位一致
性由调用方保证。
"""
from __future__ import annotations

from typing import Any, Optional

from app.tools.registry import ToolRegistry, tool


def _as_array(x: Any, name: str):
    import numpy as np

    if x is None:
        raise ValueError(f"{name} 不能为空")
    try:
        return np.asarray(x, dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} 无法解析为数值数组: {e}") from e


def _feature_summary(arr, quantiles=(0.05, 0.5, 0.95)) -> dict:
    """(H,W) 特征面的有界摘要（分位数 + 有限像元数）。"""
    import numpy as np

    a = np.asarray(arr, dtype=float)
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return {"finite_pixels": 0, "quantiles": [None, None, None]}
    return {
        "finite_pixels": int(finite.size),
        "quantiles": [round(float(np.quantile(finite, q)), 6)
                      for q in quantiles],
    }


def register_science_temporal_tools(registry: ToolRegistry):

    @tool(registry, name="temporal_cube_stats",
          description=(
              "时空立方体统计：统一时间轴 (T,H,W) 栈的逐像元气候态（均值/"
              "标准差/有效数）、时间片间距与缺失切片报告、逐像元最长缺口。"
              "SAR/光学共用容器；缺口诚实计数披露，不静默插值。"
              "\n何时用：时序建模前的数据体检——时间轴规整性、缺口分布、"
              "气候态基线。"
              "\n失败语义：时间片 >512 / 时间轴乱序 → 结构化拒绝。"),
          tier=2, domains=["temporal", "statistics"], cost="medium",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="medium", memory_class="heavy", scale_class="medium",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("时序", "立方体", "物候", "climate", "cube", "science-v5"),
          failure_modes=("invalid_args", "missing_data", "memory"))
    def temporal_cube_stats(
        stack: Any,
        times: Any,
        nodata: Optional[float] = None,
        cloud_mask: Optional[Any] = None,
        source_type: str = "optical",
    ) -> dict:

        from app.lib.geo_analysis import temporal_cube as tc

        arr = _as_array(stack, "stack")
        t = _as_array(times, "times")
        q = None
        if cloud_mask is not None:
            q = _as_array(cloud_mask, "cloud_mask")
        cube = (tc.from_optical_stack(arr, t, nodata=nodata, cloud_mask=q)
                if str(source_type).lower() == "optical"
                else tc.from_sar_stack(arr, t, nodata=nodata, quality=q))
        clim = cube.climatology()
        return {
            "summary": (
                f"时空立方体（{cube.label}）：{cube.n_slices} 切片 × "
                f"{cube.shape[1]}×{cube.shape[2]} 格网；"
                f"疑似缺失切片 {cube.slice_spacing_report()['missing_slices']}。"),
            "n_slices": cube.n_slices,
            "grid": [int(cube.shape[1]), int(cube.shape[2])],
            "slice_spacing": cube.slice_spacing_report(),
            "gap_length_range": [
                int(cube.gap_lengths().min()), int(cube.gap_lengths().max())],
            "climatology_summary": {
                "mean": _feature_summary(clim["mean"]),
                "std": _feature_summary(clim["std"]),
                "n_valid": _feature_summary(clim["n_valid"]),
            },
            "disclosures": cube.disclosures,
        }

    @tool(registry, name="phenology_features",
          description=(
              "物候特征引擎：逐像元 Savitzky-Golay 平滑 + 双谐波拟合并输出 "
              "生长季起止（SOS/EOS，切片索引制）、季长 LOS、峰值与振幅。"
              "短缺口（≤max_gap 切片）线性插值并计数；长缺口保持 NaN 不外推"
              "（被排除像元计数披露）。"
              "\n何时用：植被/水体/物的季节动态刻画——需要≥8 个时间切片。"
              "\n何时不用：时间片过少或只求趋势斜率——用 temporal_trend。"
              "\n诚实边界：索引制非真实日期反演；无项目专属类别。"),
          tier=2, domains=["temporal", "statistics"], cost="heavy",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="slow", memory_class="heavy", scale_class="medium",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("物候", "phenology", "SOS", "EOS", "时序", "science-v5"),
          failure_modes=("invalid_args", "missing_data", "memory"))
    def phenology_features(
        stack: Any,
        times: Any,
        nodata: Optional[float] = None,
        cloud_mask: Optional[Any] = None,
        window: int = 5,
        polyorder: int = 2,
        max_gap: int = 2,
        threshold_frac: float = 0.5,
    ) -> dict:
        from app.lib.geo_analysis import temporal_cube as tc
        from app.lib.geo_analysis.phenology import phenology_features as _pheno

        arr = _as_array(stack, "stack")
        t = _as_array(times, "times")
        q = (_as_array(cloud_mask, "cloud_mask")
             if cloud_mask is not None else None)
        cube = tc.from_optical_stack(arr, t, nodata=nodata, cloud_mask=q)
        res = _pheno(
            cube, window=int(window), polyorder=int(polyorder),
            max_gap=int(max_gap), threshold_frac=float(threshold_frac))
        return {
            "summary": (
                f"物候特征完成：{res['meta']['n_pixels']} 像元 × "
                f"{res['meta']['n_slices']} 切片；完整序列 "
                f"{res['meta']['n_pixels'] - res['meta']['n_excluded_pixels']}，"
                f"缺口填补 {res['meta']['n_gap_filled_cells']} 格-切片。"),
            "features_summary": {
                k: _feature_summary(v)
                for k, v in res["features"].items()
            },
            "meta": res["meta"],
        }

    @tool(registry, name="temporal_anomaly",
          description=(
              "时间异常/变化检测：逐像元最后切片 z-score（全期气候态）+ "
              "前后半段均值变化及 Welch 近似效应量。质量掩膜感知；分母退化"
              "像元诚实 NaN。"
              "\n何时用：时序异常定位（最后时刻相对气候态的偏离）与两期"
              "均值变化的方向/量级评估。"
              "\n诚实边界：change_z 是效应量近似，非正式显著性检验。"),
          tier=2, domains=["temporal", "statistics"], cost="medium",
          side_effect="deterministic_compute",
          network=False, deterministic=True,
          latency_class="medium", memory_class="heavy", scale_class="medium",
          output_semantic_type="stats",
          result_size_policy="inline_small",
          tags=("异常检测", "变化检测", "z-score", "anomaly", "science-v5"),
          failure_modes=("invalid_args", "missing_data", "memory"))
    def temporal_anomaly(
        stack: Any,
        times: Any,
        nodata: Optional[float] = None,
        cloud_mask: Optional[Any] = None,
        baseline_slices: int = 0,
    ) -> dict:
        from app.lib.geo_analysis import temporal_cube as tc
        from app.lib.geo_analysis.phenology import temporal_anomaly as _anom

        arr = _as_array(stack, "stack")
        t = _as_array(times, "times")
        q = (_as_array(cloud_mask, "cloud_mask")
             if cloud_mask is not None else None)
        cube = tc.from_optical_stack(arr, t, nodata=nodata, cloud_mask=q)
        res = _anom(
            cube,
            baseline_slices=(int(baseline_slices) if baseline_slices else None))
        return {
            "summary": (
                f"时间异常完成：{int(res['features']['anomaly_last'].shape[0])}×"
                f"{int(res['features']['anomaly_last'].shape[1])} 格网，"
                f"基线分割于切片 {res['meta']['baseline_split']}。"),
            "features_summary": {
                k: _feature_summary(v)
                for k, v in res["features"].items()
            },
            "meta": res["meta"],
        }
