"""Raster Resource & CRS Guard.

Intercepts pathological raster reprojection/resampling requests (e.g. degree-to-meter unit
confusion creating multi-billion pixel outputs) before memory allocation or file I/O.
"""
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RasterResourceExceededError(ValueError):
    """Raised when a raster operation exceeds defined resolution, pixel, or memory limits."""

    def __init__(
        self,
        message: str,
        requested_width: int,
        requested_height: int,
        total_pixels: int,
        estimated_bytes: int,
        suggested_resolutions: List[float],
        error_code: str = "RASTER_RESOURCE_EXCEEDED",
    ):
        super().__init__(message)
        self.message = message
        self.requested_width = requested_width
        self.requested_height = requested_height
        self.total_pixels = total_pixels
        self.estimated_bytes = estimated_bytes
        self.suggested_resolutions = suggested_resolutions
        self.error_code = error_code

    def to_dict(self) -> Dict[str, Any]:
        estimated_gib = round(self.estimated_bytes / (1024 ** 3), 2)
        return {
            "success": False,
            "error": "RasterResourceExceeded",
            "error_type": "RasterResourceExceededError",
            "code": self.error_code,
            "message": self.message,
            "details": {
                "requested_width": self.requested_width,
                "requested_height": self.requested_height,
                "total_pixels": self.total_pixels,
                "estimated_gib": estimated_gib,
                "suggested_target_resolutions": self.suggested_resolutions,
            },
            "correction_hint": (
                f"Raster operation rejected: output grid would be {self.requested_width}x{self.requested_height} "
                f"({self.total_pixels:,} pixels, ~{estimated_gib} GiB). "
                f"This usually indicates target_resolution is in the wrong unit (e.g. 1.0 m on a degree-based CRS). "
                f"Suggested target_resolution values to retry: {self.suggested_resolutions}"
            ),
        }


class RasterResourceGuard:
    """Configurable limits and pre-execution shape/size validator for raster ops."""

    MAX_RASTER_PIXELS: int = 250_000_000           # 250M pixels (~1 GiB single-band float32)
    MAX_RASTER_WIDTH: int = 100_000                # 100k px width ceiling
    MAX_RASTER_HEIGHT: int = 100_000               # 100k px height ceiling
    MAX_ESTIMATED_OUTPUT_BYTES: int = 1_073_741_824 # 1 GiB memory footprint limit
    MAX_OUTPUT_UPSCALE_RATIO: int = 10_000         # 10,000x upscale limit vs input grid

    @classmethod
    def suggest_safe_resolutions(
        cls,
        bounds: Tuple[float, float, float, float],
        max_pixels: Optional[int] = None,
        max_dim: Optional[int] = None,
    ) -> List[float]:
        """Calculates 3 safe target_resolution values that yield acceptable pixel counts."""
        max_px = max_pixels or cls.MAX_RASTER_PIXELS
        max_d = max_dim or cls.MAX_RASTER_WIDTH

        min_x, min_y, max_x, max_y = bounds
        width = abs(max_x - min_x)
        height = abs(max_y - min_y)

        if width <= 0 or height <= 0:
            return [10.0, 50.0, 100.0]

        area = width * height
        # Min resolution to satisfy pixel count limit
        min_res_area = math.sqrt(area / max_px)
        # Min resolution to satisfy max dimension limit
        min_res_dim = max(width / max_d, height / max_d)

        base_res = max(min_res_area, min_res_dim)

        def _round_nice(val: float) -> float:
            if val <= 0:
                return 1.0
            order = 10 ** math.floor(math.log10(val))
            normalized = val / order
            if normalized <= 1.0:
                nice = 1.0
            elif normalized <= 2.0:
                nice = 2.0
            elif normalized <= 5.0:
                nice = 5.0
            else:
                nice = 10.0
            result = nice * order
            return round(result, 6) if result < 1 else round(result, 2)

        r1 = _round_nice(base_res)
        r2 = _round_nice(base_res * 2.5)
        r3 = _round_nice(base_res * 5.0)

        # Ensure distinct ascending values
        suggestions = sorted(list({r1, r2, r3}))
        if len(suggestions) < 3:
            suggestions = [r1, r1 * 2, r1 * 5]

        return suggestions

    @classmethod
    def check_grid(
        cls,
        width: int,
        height: int,
        bounds: Optional[Tuple[float, float, float, float]] = None,
        bytes_per_pixel: int = 4,
        num_bands: int = 1,
        input_pixels: Optional[int] = None,
    ) -> None:
        """Validates raster output width/height against security and performance limits.

        Raises:
            ValueError: for non-positive dimensions
            RasterResourceExceededError: if dimensions/pixels/bytes exceed limits
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"Target raster dimensions must be positive integers, got {width}x{height}")

        total_pixels = width * height
        estimated_bytes = total_pixels * bytes_per_pixel * num_bands

        if (
            total_pixels > cls.MAX_RASTER_PIXELS
            or width > cls.MAX_RASTER_WIDTH
            or height > cls.MAX_RASTER_HEIGHT
            or estimated_bytes > cls.MAX_ESTIMATED_OUTPUT_BYTES
            or (input_pixels and total_pixels > input_pixels * cls.MAX_OUTPUT_UPSCALE_RATIO)
        ):
            suggested = (
                cls.suggest_safe_resolutions(bounds)
                if bounds
                else [50.0, 100.0, 500.0]
            )

            msg = (
                f"Requested raster operation would create a {width}x{height} grid "
                f"({total_pixels:,} pixels, ~{round(estimated_bytes / (1024**3), 2)} GiB), "
                f"exceeding maximum limit of {cls.MAX_RASTER_PIXELS:,} pixels or {cls.MAX_ESTIMATED_OUTPUT_BYTES / (1024**3):.1f} GiB. "
                f"This usually indicates target_resolution is in the wrong unit (e.g. 1.0 m on a degree-based CRS). "
                f"Suggested target_resolution values: {suggested}"
            )
            logger.warning(f"[RasterResourceGuard] {msg}")

            raise RasterResourceExceededError(
                message=msg,
                requested_width=width,
                requested_height=height,
                total_pixels=total_pixels,
                estimated_bytes=estimated_bytes,
                suggested_resolutions=suggested,
            )
