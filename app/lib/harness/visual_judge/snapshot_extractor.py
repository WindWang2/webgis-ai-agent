"""快照提取管线（ADR-0185 D4）：解码 → 校验 → 哈希 → 灰度直方图初筛。

输入三源统一：前端 Canvas 上报的 base64/data URL、headless Playwright 快照
字节、``runtime_dir/map.png`` 文件字节。

初筛 hints 只作诊断证据随报告披露，**永不替代/预判 VLM 结论**（确定性
信号越权视觉判断会重新打开「规则冒充裁判」的后门，ADR-0185 D4）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union

from PIL import Image, UnidentifiedImageError

#: 魔数嗅探表（顺序敏感：PNG/JPEG/WebP）。
_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_WEBP_MAGIC = b"WEBP"
_RIFF_MAGIC = b"RIFF"

_DEFAULT_MAX_BYTES = 4 * 1024 * 1024
_DEFAULT_MIN_EDGE = 64
_DEFAULT_MAX_EDGE = 8192
#: 灰度直方图计算前的降采样长边（初筛是廉价确定性预检，不是评审）。
_HISTOGRAM_EDGE = 512

_PRE_SCREEN_BINS = 32
_LOW_CONTRAST_SPREAD = 24.0
_NEAR_BLANK_STD = 2.0
_EXTREME_DARK_MEAN = 24.0
_EXTREME_BRIGHT_MEAN = 232.0


class SnapshotError(Exception):
    """快照不可用（机器可读 ``reason``，ADR-0185 D3 fail-closed 矩阵）。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class MapSnapshot:
    """一次成功提取的截图快照（哈希是记忆化键第三元）。"""

    image_sha256: str
    width: int
    height: int
    size_bytes: int
    mime: str
    data_url: str
    pre_screen: Dict[str, Any] = field(default_factory=dict)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _sniff_mime(data: bytes) -> Optional[str]:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == _RIFF_MAGIC and data[8:12] == _WEBP_MAGIC:
        return "image/webp"
    return None


def _decode_input(image: Union[bytes, str]) -> bytes:
    """bytes / base64 / data URL 三态归一为原始字节。"""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    if not isinstance(image, str):
        raise SnapshotError("image_corrupt", "unsupported image payload type")
    text = image.strip()
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        text = payload
    if not text:
        raise SnapshotError("image_empty")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SnapshotError("image_corrupt", f"invalid base64: {exc}") from exc


def _grayscale_pre_screen(img: Image.Image) -> Dict[str, Any]:
    """灰度直方图初筛（确定性，无随机源，ADR-0185 D4）。

    输入是已打开的原图；内部降采样后统计亮度分布。hints 只描述像素统计
    事实，不做任何「合格/不合格」裁决。
    """
    gray = img.convert("L")
    if gray.width > _HISTOGRAM_EDGE or gray.height > _HISTOGRAM_EDGE:
        gray.thumbnail((_HISTOGRAM_EDGE, _HISTOGRAM_EDGE), Image.LANCZOS)
    hist = gray.histogram()
    total = sum(hist)
    if total <= 0:
        return {"mean_luma": 0.0, "luma_std": 0.0, "p5_p95_spread": 0.0,
                "entropy": 0.0, "hints": ["near_blank"]}
    mean = sum(i * c for i, c in enumerate(hist)) / total
    variance = sum(c * (i - mean) ** 2 for i, c in enumerate(hist)) / total

    def _percentile(p: float) -> int:
        acc = 0
        threshold = total * p
        for i, c in enumerate(hist):
            acc += c
            if acc >= threshold:
                return i
        return 255

    p5, p95 = _percentile(0.05), _percentile(0.95)

    # 32-bin 熵：像素分布越散，图面信息越丰富。
    bin_size = 256 // _PRE_SCREEN_BINS
    entropy = 0.0
    for b in range(_PRE_SCREEN_BINS):
        count = sum(hist[b * bin_size:(b + 1) * bin_size])
        if count:
            share = count / total
            entropy -= share * math.log2(share)

    hints = []
    spread = float(p95 - p5)
    std = math.sqrt(variance)
    if std < _NEAR_BLANK_STD:
        hints.append("near_blank")
    if spread < _LOW_CONTRAST_SPREAD:
        hints.append("low_contrast")
    if mean < _EXTREME_DARK_MEAN:
        hints.append("extreme_dark")
    if mean > _EXTREME_BRIGHT_MEAN:
        hints.append("extreme_bright")
    return {
        "mean_luma": round(mean, 2),
        "luma_std": round(std, 2),
        "p5_p95_spread": spread,
        "entropy": round(entropy, 4),
        "hints": hints,
    }


class SnapshotExtractor:
    """截图快照提取器（尺寸护栏可经构造参数或 env 覆盖）。"""

    def __init__(
        self,
        *,
        max_bytes: Optional[int] = None,
        min_edge: Optional[int] = None,
        max_edge: Optional[int] = None,
    ):
        self.max_bytes = max_bytes if max_bytes is not None else _env_int(
            "CARTO_VISUAL_CRITIC_MAX_IMAGE_BYTES", _DEFAULT_MAX_BYTES)
        self.min_edge = min_edge if min_edge is not None else _env_int(
            "CARTO_VISUAL_CRITIC_MIN_EDGE_PX", _DEFAULT_MIN_EDGE)
        self.max_edge = max_edge if max_edge is not None else _DEFAULT_MAX_EDGE

    def extract(self, image: Union[bytes, str]) -> MapSnapshot:
        data = _decode_input(image)
        if not data:
            raise SnapshotError("image_empty")
        if len(data) > self.max_bytes:
            raise SnapshotError(
                "image_oversized", f"{len(data)} bytes > limit {self.max_bytes}")
        mime = _sniff_mime(data)
        if mime is None:
            raise SnapshotError("image_corrupt", "unrecognized image magic")
        try:
            with Image.open(io.BytesIO(data)) as probe:
                width, height = probe.size
                probe.verify()  # 结构校验（截断/损坏在此暴露）
            img = Image.open(io.BytesIO(data))
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            # Pillow 对截断/损坏图抛 OSError 或 SyntaxError（含 UnidentifiedImageError）。
            raise SnapshotError("image_corrupt", f"unverifiable image: {exc}") from exc
        try:
            pre_screen = _grayscale_pre_screen(img)
        finally:
            img.close()
        if min(width, height) < self.min_edge:
            raise SnapshotError(
                "image_too_small", f"{width}x{height} < min edge {self.min_edge}")
        if max(width, height) > self.max_edge:
            raise SnapshotError(
                "image_oversized", f"{width}x{height} > max edge {self.max_edge}")
        return MapSnapshot(
            image_sha256=hashlib.sha256(data).hexdigest(),
            width=width,
            height=height,
            size_bytes=len(data),
            mime=mime,
            data_url=f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
            pre_screen=pre_screen,
        )


__all__ = ["MapSnapshot", "SnapshotError", "SnapshotExtractor"]
