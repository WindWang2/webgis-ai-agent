"""栅格分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 raster 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="remote.zonal_stats", name="分区统计", category="raster_analysis",
            capabilities=["zonal_statistics"],
            input_artifact_types=["raster_surface", "polygon_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["zonal_stats"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="raster_zonal",
            assumptions=["统计量在面掩膜内计算（nan-aware）；栅格与面 CRS 一致由上层保证",
                         "rasterstats/zonal 统计实现（all_touched=False 惯例）"],
            limitations=["面跨界像元按像元中心归属（惯例披露）"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_zonal_stats_crs_682.py::test_zonal_stats_tool_3857_zone_on_3857_raster",
            ]
        ),

        AlgorithmDescriptor(
            id="raster.algebra", name="栅格计算器（窗口化）", category="raster_analysis",
            capabilities=["band_math"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_calculator"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="raster_algebra",
            assumptions=["窗口化逐像元表达式求值（numexpr）；nodata 传播为 nodata"],
            limitations=["表达式在声明波段角色上求值，不做隐式重采样/对齐",
                         "除零按表达式语义产 inf/NaN（不静默钳制）"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_raster_tools.py::test_raster_calculator_two_rasters",
            ],
            version="3.0",
        ),

        AlgorithmDescriptor(
            id="raster.reclassify.rule", name="规则重分类", category="raster_analysis",
            capabilities=["raster_reclassify"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_reclassify"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="raster_reclassify",
            assumptions=["规则表逐段左闭右开映射；未命中段 → nodata（披露）"],
            limitations=["浮点边界比较语义（无容差）——由规则表作者负责"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_raster_tools.py::test_raster_reclassify_basic",
            ]
        ),

        AlgorithmDescriptor(
            id="raster.resample.grid", name="网格重采样/重投影", category="raster_analysis",
            capabilities=["raster_resample"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_resample"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="raster_resample",
            assumptions=["重采样方法（邻近/双线性/平均）显式声明",
                         "目标网格由对齐参数决定（WarpedVRT）"],
            limitations=["重投影经 GDAL/PROJ；极区/跨子午线由 Warp 处理（披露）"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_raster_runtime_v3.py::test_unaligned_b_resamples_onto_a_grid",
            ]
        ),

        AlgorithmDescriptor(
            id="raster.cog.convert", name="Cloud Optimized GeoTIFF 转换",
            capabilities=["raster_cog_conversion"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["convert_raster_to_cog"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="CELERY",
            algorithm_family="raster_io",
            assumptions=["概视图金字塔重采样（nearest），footer 索引按 COG 规范"],
            limitations=["单文件 GeoTIFF 输入；已有 COG 结构则直接通过"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            priority=10,
        ),
    ]
