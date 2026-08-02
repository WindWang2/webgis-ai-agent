"""Remote Sensing & Raster Processing Package.

暴露 deep domain engine `SpectralRasterEngine` 与统一 Domain 值对象 `RasterAnalysisResult`。
"""
from app.services.rs.band_math import (
    RasterAnalysisResult,
    INDEX_FORMULAS,
    compute_index_array,
    compute_slope,
    compute_aspect,
    compute_hillshade,
    compute_raster_stats,
)
from app.services.rs.spectral_engine import SpectralRasterEngine, spectral_engine

__all__ = [
    "SpectralRasterEngine",
    "spectral_engine",
    "RasterAnalysisResult",
    "INDEX_FORMULAS",
    "compute_index_array",
    "compute_slope",
    "compute_aspect",
    "compute_hillshade",
    "compute_raster_stats",
]
