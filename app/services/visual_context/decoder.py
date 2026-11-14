"""参考图/采集图的有界真实解码与送模派生（G36 / ADR-0291）。

红线：
- **真实解码**：只信解码后的像素，不信扩展名 / Content-Type / 客户端
  width/height / 魔数前几字节。PNG/JPEG 之外的格式（SVG/PDF/动画/多页）
  v1 明确拒绝（V15）—— 不执行脚本、不加载远端资源、不静默取首帧。
- **解压炸弹护栏**：解码像素 ≤ ``MAX_DECODE_PIXELS``、长边 ≤ 8192；
  I/O 与重编码走工作线程，不阻塞事件循环。
- **EXIF**：方向校正后送模/展示（V13 显示与送模一致）；派生重编码
  剥离全部元数据（含 GPS）。
- **诚实观察**：透明/几乎空白图给出观察标记（V14），不据此拒绝 ——
  上层把观察披露给用户与回执，不声称识别了地图内容。
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from app.services.visual_context.evidence import EvidenceRejected

logger = logging.getLogger(__name__)

try:  # Pillow 是生产依赖（requirements.lock 锁版）；缺席时 intake 拒绝
    from PIL import Image, ImageOps
    _PIL_OK = True
except Exception:  # noqa: BLE001
    _PIL_OK = False

#: intake 原始字节上限（multipart 读取边界）。
MAX_INTAKE_BYTES = 8 * 1024 * 1024
#: 解码像素上限（解压炸弹护栏）。
MAX_DECODE_PIXELS = 30_000_000
MAX_DECODE_SIDE = 8192
#: 送模派生图上限（与 profile 投影的 inputLimits 一致）。
MAX_DERIVED_SIDE = 1568
MAX_DERIVED_BYTES = 1536 * 1024          # 派生字节（base64 前）
#: 地图采集（canvas PNG）解码上限 —— 与既有 critic 截图链同量级。
MAX_CAPTURE_SIDE = 8192

_SUPPORTED_FORMATS = ("PNG", "JPEG")
_JPEG_QUALITY_STEPS = (85, 80, 75, 70, 62, 55, 45)
#: 近空白判定：中心裁块均值极差 < 4（8bit）视为几乎空白（观察标记）。
_BLANK_THRESHOLD = 4.0


@dataclass
class DecodedImage:
    """解码结果（方向校正后；未重编码）。"""

    image: "Image.Image"
    format: str                 # PIL 判定的真实格式（大写）
    width: int
    height: int
    observations: Tuple[str, ...] = ()


def _open_bounded(data: bytes, *, max_side: int) -> "Image.Image":
    """真实打开 + 解码护栏（像素/长边）；违限拒绝。"""
    from PIL import Image as _Image

    _Image.MAX_IMAGE_PIXELS = max(MAX_DECODE_PIXELS, _Image.MAX_IMAGE_PIXELS or 0)
    try:
        img = _Image.open(io.BytesIO(data))
        # 动画/多页明确拒绝（V15）：不取首帧。
        n_frames = getattr(img, "n_frames", 1)
        if n_frames and int(n_frames) > 1:
            raise EvidenceRejected(
                "animated_not_supported",
                "动画/多帧图片暂不支持，请上传静态 PNG 或 JPEG。",
            )
        fmt = str(getattr(img, "format", "") or "").upper()
        if fmt not in _SUPPORTED_FORMATS:
            raise EvidenceRejected(
                "unsupported_format",
                "仅支持静态 PNG / JPEG 图片。",
            )
        # 头部尺寸预算先行（PIL open 已解析 IHDR/SOF）—— 解压炸弹在
        # 物化像素之前拒绝。
        hw, hh = img.size
        if hw <= 0 or hh <= 0:
            raise EvidenceRejected("decode_failed", "图片尺寸无效。")
        if hw * hh > MAX_DECODE_PIXELS or hw > max_side or hh > max_side:
            raise EvidenceRejected(
                "pixel_budget_exceeded",
                f"图片像素超限（≤{max_side}px 长边、≤{MAX_DECODE_PIXELS // 1_000_000}MP）。",
            )
        img.load()   # 真实解码（不是只读头）
    except EvidenceRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — 解码失败 = 坏文件（V12）
        raise EvidenceRejected(
            "decode_failed", f"图片解码失败：{type(exc).__name__}") from exc
    w, h = img.size
    if w <= 0 or h <= 0:
        raise EvidenceRejected("decode_failed", "图片尺寸无效。")
    if w * h > MAX_DECODE_PIXELS or w > max_side or h > max_side:
        raise EvidenceRejected(
            "pixel_budget_exceeded",
            f"图片像素超限（≤{max_side}px 长边、≤{MAX_DECODE_PIXELS // 1_000_000}MP）。",
        )
    return img


def _observe_content(img: "Image.Image") -> Tuple[str, ...]:
    """有界内容观察（透明/近空白；只标记，不拒绝）。"""
    obs = []
    if img.mode in ("RGBA", "LA", "PA"):
        alpha = img.getchannel("A")
        mn, mx = alpha.getextrema()
        if mn is not None and int(mn) < 8:
            obs.append("transparent_regions")
    try:
        small = img.convert("L").resize((32, 32))
        lo, hi = small.getextrema()
        if hi is not None and lo is not None and (int(hi) - int(lo)) < _BLANK_THRESHOLD:
            obs.append("near_blank")
    except Exception:  # noqa: BLE001 — 观察失败不阻断
        pass
    return tuple(obs)


def decode_image(data: bytes, *, max_side: int = MAX_CAPTURE_SIDE) -> DecodedImage:
    """同步解码（方向校正 + 观察标记）。调用方负责线程外移。"""
    if not _PIL_OK:
        raise EvidenceRejected("decoder_unavailable", "服务端图像解码依赖不可用。")
    if not data:
        raise EvidenceRejected("empty_input", "图片内容为空。")
    if len(data) > MAX_INTAKE_BYTES:
        raise EvidenceRejected(
            "intake_oversized",
            f"图片超过 {MAX_INTAKE_BYTES // (1024 * 1024)}MiB 上传上限。",
        )
    img = _open_bounded(data, max_side=max_side)
    fmt = str(getattr(img, "format", "") or "").upper()
    # EXIF 方向校正（V13：显示与送模同一方向）。
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001 — 方向失败按原图（不伪造）
        logger.debug("[VisualContextDecode] exif transpose failed", exc_info=True)
    obs = _observe_content(img)
    return DecodedImage(image=img, format=fmt, width=img.size[0], height=img.size[1],
                        observations=obs)


def derive_model_image(
    decoded: DecodedImage,
    *,
    crop: Optional[dict] = None,
) -> Tuple[bytes, str, int, int]:
    """送模派生：crop（原图像素系）→ 长边 ≤1568 → 重编码（剥元数据）。

    返回 ``(derived_bytes, mime, width, height)``；派生字节 ≤
    ``MAX_DERIVED_BYTES``（JPEG 逐级降质仍超限 → PNG 压缩通道最后尝试，
    再超限拒绝——地图线要素极端情况诚实拒绝，不静默缩到不可读）。
    """
    img = decoded.image
    if crop:
        w, h = img.size
        x = int(crop.get("x", 0))
        y = int(crop.get("y", 0))
        cw = int(crop.get("w", 0))
        ch = int(crop.get("h", 0))
        # 越界拒绝（不静默裁剪到别的区域 —— V03：crop 语义必须精确）。
        if x < 0 or y < 0 or cw <= 0 or ch <= 0 \
                or x + cw > w or y + ch > h:
            raise EvidenceRejected("crop_out_of_bounds", "裁剪矩形超出原图范围。")
        img = img.crop((x, y, x + cw, y + ch))
    w, h = img.size
    scale = min(1.0, MAX_DERIVED_SIDE / max(w, h)) if max(w, h) > 0 else 1.0
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         getattr(Image, "Resampling", Image).LANCZOS)
    has_alpha = bool(img.mode in ("RGBA", "LA", "PA") or
                     (img.mode == "P" and "transparency" in (img.info or {})))
    if has_alpha:
        # 保留透明语义（参考图可能带透明底）；PNG 无损通道。
        out = img.convert("RGBA")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_DERIVED_BYTES:
            return data, "image/png", out.size[0], out.size[1]
        raise EvidenceRejected(
            "derived_oversized",
            "透明图片派生体积超限，请裁剪或压缩后重试。",
        )
    out = img.convert("RGB")
    for q in _JPEG_QUALITY_STEPS:
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_DERIVED_BYTES:
            return data, "image/jpeg", out.size[0], out.size[1]
    raise EvidenceRejected(
        "derived_oversized",
        "图片派生体积超限（已尝试有界压缩），请缩小范围后重试。",
    )


async def decode_image_async(data: bytes, *, max_side: int = MAX_CAPTURE_SIDE) -> DecodedImage:
    """事件循环友好入口（解码/观察在工作线程）。"""
    return await asyncio.to_thread(decode_image, data, max_side=max_side)


async def derive_model_image_async(
    decoded: DecodedImage, *, crop: Optional[dict] = None,
) -> Tuple[bytes, str, int, int]:
    return await asyncio.to_thread(derive_model_image, decoded, crop=crop)


def make_thumbnail(decoded_or_bytes, *, max_side: int = 320) -> bytes:
    """缩略图（授权读取端点用；JPEG 编码，元数据天然不存在）。"""
    img = decoded_or_bytes
    if isinstance(img, (bytes, bytearray)):
        img = _open_bounded(bytes(img), max_side=MAX_CAPTURE_SIDE)
    elif hasattr(img, "image"):  # DecodedImage
        img = img.image
    w, h = img.size
    scale = min(1.0, max_side / max(w, h)) if max(w, h) > 0 else 1.0
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         getattr(Image, "Resampling", Image).LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return buf.getvalue()
