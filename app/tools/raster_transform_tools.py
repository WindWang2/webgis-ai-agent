"""Raster transformation schemas and tools used by the advanced-spatial catalog."""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools.registry import ToolRegistry, tool


class RasterReclassifyArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    scheme: List[dict] = Field(..., description="重分类方案，每项含 min/max/value/label，如 [{\"min\":0,\"max\":0.2,\"value\":1,\"label\":\"低\"},...]")
    nodata: Optional[float] = Field(None, description="输出 NoData 值（默认继承输入栅格）")

class RasterCalculatorArgs(BaseModel):
    raster_a: str = Field(..., description="主栅格文件路径（data/ 内）")
    raster_b: Optional[str] = Field(None, description="副栅格文件路径（data/ 内）；留空则用 constant")
    expression: str = Field("A + B", description="运算表达式，用 A/B 指代栅格，如 A+B, (A-B)/(A+B), where(A>0,A,0)")
    constant: Optional[float] = Field(None, description="当 raster_b 留空时的常数")
    nodata: Optional[float] = Field(None, description="输出 NoData 值")
    resampling: Optional[str] = Field(None, description="B 对齐到 A 的重采样方法（bilinear 默认；分类栅格必须 nearest）")

class RasterResampleArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    target_resolution: float = Field(..., description="目标像元大小（米或度，取决于 CRS）")
    target_crs: Optional[str] = Field(None, description="目标 CRS（如 EPSG:3857），留空则保持原 CRS")
    resampling: str = Field("bilinear", description="重采样方法: bilinear(默认), nearest, cubic, mode, average")


def register_raster_transform_tools(registry: ToolRegistry) -> None:
    @tool(registry, name="raster_reclassify",
    anti_examples=("改变像元分辨率（用 raster_resample）",),
           description=(
               "栅格重分类：将连续栅格值按方案映射为离散类别。"
               "\n何时用：『把 NDVI 连续值分成低/中/高植被覆盖等级』；"
               "把高程分成平原/丘陵/山地；把坡度分成安全/中等/危险等级。"
               "\n何时不用：(1) 只是查看统计值 — 用 zonal_stats；"
               "(2) 两个栅格做运算 — 用 raster_calculator。"
               "\n关键约束：scheme 按 min→max 排序，首个匹配 wins；未匹配像素变 nodata。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterReclassifyArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("重分类", "栅格", "reclassify", "分级", "ndvi", "dem"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_reclassify(raster_path: str, scheme: List[dict], nodata: Optional[float] = None) -> dict:
        res = SpatialAnalyzer.raster_reclassify(raster_path, scheme, nodata)
        return res.to_llm_response()

    @tool(registry, name="raster_calculator",
           description=(
               "栅格计算器：对两个栅格做像素级数学运算（A+B, A-B, (A-B)/(A+B) 等）。"
               "\n何时用：『NDVI = (NIR-Red)/(NIR+Red)』『DEM 差值 = A-B』『比值指数』；"
               "任意两个栅格的逐像素运算。"
               "\n何时不用：(1) 单栅格重分类 — 用 raster_reclassify；"
               "(2) 双时相变化检测（差值+阈值分类一步到位）— 用 detect_raster_change；"
               "(3) 需要地理加权（如 focal）— 未实现，先用 focal_stats。"
               "\n关键约束：expression 用 A/B 指代栅格；支持 numexpr 语法；自动 nodata 掩膜。"
               "A 是基准网格：分辨率/CRS 不一致的 B 会自动虚拟重投影对齐到 A（连续量 bilinear），"
               "对齐事实进 quality_evidence。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterCalculatorArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("栅格计算", "raster calculator", "ndvi", "像元运算", "差值"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_calculator(raster_a: str, raster_b: Optional[str] = None, expression: str = "A + B", constant: Optional[float] = None, nodata: Optional[float] = None, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_calculator(raster_a, raster_b, expression, constant, nodata, resampling)
        return res.to_llm_response()

    @tool(registry, name="detect_raster_change",
           description=(
               "双时相栅格变化检测：对本地两个栅格工件（如两期 NDVI/分类/DEM 产物）"
               "做像元级变化检测，输出变化栅格 + 统计 + 质量证据。"
               "\n何时用：『对比这两个时期的 NDVI 栅格哪里变了』『两期分类图差异』；"
               "已上传/已生成两个 .tif 工件的变化分析。"
               "\n何时不用：(1) 无本地栅格、只有 bbox+日期 — 用 detect_vegetation_change（在线 STAC）；"
               "(2) 只要差值统计不要栅格 — temporal_raster；"
               "(3) 两个栅格做任意表达式运算 — raster_calculator。"
               "\n关键约束：raster_a（T1）是基准网格；B 分辨率/CRS 不一致时自动虚拟对齐"
               "（对齐/重采样/裁剪事实进 quality_evidence）；method 可选 "
               "difference/absolute_difference/normalized_difference；threshold 给定时"
               "输出二分类变化栅格（1=变化，0=稳定，255=nodata）；分类图输入时"
               "传 resampling=nearest（默认 bilinear 仅适用连续量）。"
           ),
           tier=2, domains=["raster"],
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("变化检测", "双时相", "ndvi变化", "raster change", "差异"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def detect_raster_change(raster_a: str, raster_b: str, method: str = "difference", threshold: Optional[float] = None, band: int = 1, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_change(raster_a, raster_b, method=method, threshold=threshold, band=band, resampling=resampling)
        return res.to_llm_response()

    @tool(registry, name="raster_resample",
    anti_examples=("重分类分级（用 raster_reclassify）",),
           description=(
               "栅格重采样：改变像元大小和/或 CRS。"
               "\n何时用：『把 30m DEM 重采样到 90m 做概览』『把 WGS84 栅格转到 UTM 做面积计算』；"
               "不同分辨率/投影的栅格对齐前预处理。"
               "\n何时不用：(1) 只改元数据 — 用 gdal_translate 更快；"
               "(2) 已经同分辨率同 CRS — 不需要重采样。"
               "\n关键约束：resampling 可选 bilinear/nearest/cubic/mode/average。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterResampleArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("重采样", "resample", "分辨率", "投影转换", "栅格"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_resample(raster_path: str, target_resolution: float, target_crs: Optional[str] = None, resampling: str = "bilinear") -> dict:
        res = SpatialAnalyzer.raster_resample(raster_path, target_resolution, target_crs, resampling)
        return res.to_llm_response()
