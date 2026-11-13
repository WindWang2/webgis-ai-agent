"""WP06 — 有界真实动画编码器。

- **APNG（核心）**：自写 streaming 容器 writer（acTL/fcTL/fdAT + 逐帧 zlib），
  任意时刻只常驻一张已解码帧；不把全片帧列表塞进内存，也不因传入 iterator
  就宣称常量内存。Pillow 已是生产依赖，颜色无损 RGBA。
- **GIF（兼容）**：整片一张共用的全局调色板（不逐帧换色造成假变化），
  Pillow 编码；披露 256 色量化色差与 10ms 时长量化。
- **MP4/WebM**：只做真实 codec probe（``capabilities.probe_video_codecs``），
  部署依赖未落实时 typed 拒绝，绝不按扩展名猜编码成功。

编码参数、文件名、工作目录由服务端控制；不接受 shell 字符串/外部 URL。
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from .contracts import ENCODE_TIMEOUT_S, TemporalMediaError

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_MAX_APNG_DELAY_MS = 60_000
_ANCillary = (b"PLTE", b"tRNS", b"gAMA", b"sRGB", b"pHYs", b"cHRM")


def _chunk(out, ctype: bytes, data: bytes) -> None:
    out.write(struct.pack(">I", len(data)))
    raw = ctype + data
    out.write(raw)
    out.write(struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF))


def _read_chunks(data: bytes):
    if data[:8] != _PNG_SIG:
        raise TemporalMediaError("PNG_UNDECODABLE", "frame is not a PNG",
                                 status_code=502)
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if pos > len(data):
            raise TemporalMediaError("PNG_UNDECODABLE", "truncated PNG chunk",
                                     status_code=502)
        yield ctype, payload
        if ctype == b"IEND":
            break


def _normalized_png(path: Path) -> bytes:
    """统一 IHDR（8-bit RGBA / non-interlaced），保证各帧 fcTL 同尺寸同色彩类型。"""
    with Image.open(path) as im:
        im.load()
        rgba = im.convert("RGBA")
    buf = io.BytesIO()
    rgba.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def build_encoded_frames(plan: Dict[str, Any],
                         digital_by_segment: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """segment → 编码帧：停留可拆多张相同画面；相邻相同画面可合并但保留说明。"""
    entries: List[Dict[str, Any]] = []
    for segment in plan["display"]["segments"]:
        evidence = digital_by_segment[int(segment["index"])]
        for duration in segment["encoded_durations_ms"]:
            entries.append({
                "digital_frame_id": evidence["digital_frame_id"],
                "png_path": evidence["png_path"],
                "png_sha256": evidence["png_sha256"],
                "duration_ms": int(duration),
                "segment_indices": [int(segment["index"])],
                "observation_ids": [segment["observation_id"]],
                "identities": [segment["identity"]],
                "segment_durations_ms": {str(int(segment["index"])): int(duration)},
                "equivalent_digital_frames": [],
            })
    merged: List[Dict[str, Any]] = []
    for entry in entries:
        # 只有**画面字节完全相同**才允许合并；不同观测身份仍留在 segment
        # 映射里（画面相同而标签不同在渲染层就不可能出现：标签在像素里）。
        if (merged
                and merged[-1]["png_sha256"] == entry["png_sha256"]
                # 显式 fps 拆出的同一停留多帧保持不合并（容器帧映射可解释）；
                # 只合并**跨显示段**的相同画面，观测身份仍留在 segment 映射里。
                and merged[-1]["segment_indices"][-1] != entry["segment_indices"][0]
                and merged[-1]["duration_ms"] + entry["duration_ms"]
                <= _MAX_APNG_DELAY_MS):
            last = merged[-1]
            last["duration_ms"] += entry["duration_ms"]
            for key, value in (entry.get("segment_durations_ms") or {}).items():
                last["segment_durations_ms"][key] = (
                    last["segment_durations_ms"].get(key, 0) + int(value))
            for idx in entry["segment_indices"]:
                if idx not in last["segment_indices"]:
                    last["segment_indices"].append(idx)
            for oid in entry["observation_ids"]:
                if oid is not None and oid not in last["observation_ids"]:
                    last["observation_ids"].append(oid)
            for ident in entry["identities"]:
                if ident not in last["identities"]:
                    last["identities"].append(ident)
            if entry["digital_frame_id"] != last["digital_frame_id"]:
                last["equivalent_digital_frames"].append(entry["digital_frame_id"])
            last["merged"] = True
        else:
            entry["merged"] = False
            merged.append(entry)
    return merged


def write_apng(path: Path, encoded: List[Dict[str, Any]], frames_dir: Path,
               *, loop_count: int = 0, deadline: Optional[float] = None,
               cancel: Optional[Callable[[], bool]] = None) -> None:
    """streaming APNG：单帧常驻，逐帧写 fdAT；无 shell、无全片缓冲。"""
    if not encoded:
        raise TemporalMediaError("ENCODE_FAILED", "no verified frames to encode",
                                 status_code=502)
    first = _normalized_png(frames_dir / encoded[0]["png_path"])
    chunks = list(_read_chunks(first))
    ihdr = next((d for t, d in chunks if t == b"IHDR"), None)
    if ihdr is None or len(ihdr) != 13:
        raise TemporalMediaError("ENCODE_FAILED", "frame IHDR missing",
                                 status_code=502)
    width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8 or color_type != 6 or interlace != 0:
        raise TemporalMediaError("ENCODE_FAILED",
                                 "normalized frame is not 8-bit RGBA PNG",
                                 status_code=502)
    with path.open("wb") as out:
        out.write(_PNG_SIG)
        _chunk(out, b"IHDR", ihdr)
        for ctype, payload in chunks:
            if ctype in _ANCillary:
                _chunk(out, ctype, payload)
        _chunk(out, b"acTL", struct.pack(">II", len(encoded), int(loop_count)))
        seq = 0
        import time
        for index, entry in enumerate(encoded):
            if cancel is not None and cancel():
                raise TemporalMediaError("CANCELLED",
                                         "encoding cancelled before frame commit",
                                         status_code=409)
            if deadline is not None and time.monotonic() > deadline:
                raise TemporalMediaError("ENCODE_TIMEOUT",
                                         "bounded encoder wall-clock budget exceeded",
                                         status_code=504)
            delay = max(0, min(65535, int(entry["duration_ms"])))
            fctl = struct.pack(">IIIIIHHBB", seq, width, height, 0, 0,
                               delay, 1000, 0, 0)
            _chunk(out, b"fcTL", fctl)
            seq += 1
            data = first if index == 0 else _normalized_png(
                frames_dir / entry["png_path"])
            idat = b"".join(d for t, d in _read_chunks(data) if t == b"IDAT")
            if not idat:
                raise TemporalMediaError("ENCODE_FAILED",
                                         "frame has no IDAT payload", status_code=502)
            if index == 0:
                _chunk(out, b"IDAT", idat)
            else:
                _chunk(out, b"fdAT", struct.pack(">I", seq) + idat)
                seq += 1
        _chunk(out, b"IEND", b"")


def _global_palette(frames_dir: Path, encoded: List[Dict[str, Any]]) -> Image.Image:
    """全片共用调色板：各帧缩略图拼贴后一次量化，避免逐帧换色造成假变化。"""
    tiles: List[Image.Image] = []
    for entry in encoded:
        with Image.open(frames_dir / entry["png_path"]) as im:
            im.load()
            tiles.append(im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR))
    if not tiles:
        raise TemporalMediaError("ENCODE_FAILED", "no frames for palette",
                                 status_code=502)
    cols = min(8, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    montage = Image.new("RGB", (cols * 64, rows * 64), (255, 255, 255))
    for i, tile in enumerate(tiles):
        montage.paste(tile, ((i % cols) * 64, (i // cols) * 64))
    return montage.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def write_gif(path: Path, encoded: List[Dict[str, Any]], frames_dir: Path,
              *, loop_count: int = 0, deadline: Optional[float] = None,
              cancel: Optional[Callable[[], bool]] = None,
              ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Pillow GIF + 全局调色板；返回（量化损失, 实际容器帧映射）。

    Pillow 的 GIF writer 会**自动合并相邻相同 P 帧**并累加时长。为避免
    「容器帧数 ≠ 计划帧数」导致独立解码错位，这里先按量化后的真实像素
    显式折叠：合并段的 segment/observation 身份与逐段时长仍完整保留。
    """
    import hashlib
    import time

    palette = _global_palette(frames_dir, encoded)
    images: List[Image.Image] = []
    durations: List[int] = []
    picture_keys: List[str] = []
    duration_loss_ms = 0
    color_loss = 0.0
    for entry in encoded:
        if cancel is not None and cancel():
            raise TemporalMediaError("CANCELLED", "encoding cancelled",
                                     status_code=409)
        if deadline is not None and time.monotonic() > deadline:
            raise TemporalMediaError("ENCODE_TIMEOUT",
                                     "bounded encoder wall-clock budget exceeded",
                                     status_code=504)
        centis = max(1, round(int(entry["duration_ms"]) / 10))
        durations.append(centis * 10)
        duration_loss_ms += abs(centis * 10 - int(entry["duration_ms"]))
        with Image.open(frames_dir / entry["png_path"]) as im:
            im.load()
            rgb = im.convert("RGB")
        quantized = rgb.quantize(palette=palette, dither=Image.Dither.NONE)
        sample = rgb.resize((64, 64), Image.Resampling.BILINEAR)
        restored = quantized.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
        import numpy as np
        diff = np.abs(np.asarray(sample, dtype=np.int16)
                      - np.asarray(restored, dtype=np.int16))
        color_loss += float(diff.mean())
        images.append(quantized)
        picture_keys.append(hashlib.sha256(quantized.tobytes()).hexdigest())

    # 显式折叠：与 Pillow 的合并规则一致，但映射由我们拥有并写进 manifest。
    effective: List[Dict[str, Any]] = []
    effective_images: List[Image.Image] = []
    effective_durations: List[int] = []
    for index, entry in enumerate(encoded):
        if effective and picture_keys[index] == picture_keys[effective[-1]["_source_index"]]:
            last = effective[-1]
            last["duration_ms"] += durations[index]
            for key, value in (entry.get("segment_durations_ms") or {}).items():
                last["segment_durations_ms"][key] = (
                    last["segment_durations_ms"].get(key, 0) + int(value))
            for idx in entry["segment_indices"]:
                if idx not in last["segment_indices"]:
                    last["segment_indices"].append(idx)
            for oid in entry["observation_ids"]:
                if oid is not None and oid not in last["observation_ids"]:
                    last["observation_ids"].append(oid)
            for ident in entry["identities"]:
                if ident not in last["identities"]:
                    last["identities"].append(ident)
            last["merged"] = True
            effective_durations[-1] += durations[index]
        else:
            merged = dict(entry)
            merged["duration_ms"] = durations[index]
            merged["segment_durations_ms"] = dict(entry.get("segment_durations_ms") or {})
            merged["segment_indices"] = list(entry["segment_indices"])
            merged["observation_ids"] = list(entry["observation_ids"])
            merged["identities"] = list(entry["identities"])
            merged["merged"] = bool(entry.get("merged"))
            merged["_source_index"] = index
            effective.append(merged)
            effective_images.append(images[index])
            effective_durations.append(durations[index])
    for item in effective:
        item.pop("_source_index", None)

    effective_images[0].save(
        path, format="GIF", save_all=True, append_images=effective_images[1:],
        duration=effective_durations, loop=loop_count, optimize=False, disposal=2)
    loss = {
        "palette": "single global 256-color palette derived from all frames",
        "palette_colors": 256,
        "duration_unit_ms": 10,
        "duration_quantization_loss_ms": int(duration_loss_ms),
        "mean_absolute_channel_error": round(color_loss / max(1, len(images)), 4),
        "alpha": "1-bit only",
        "container_merged_frames": len(encoded) - len(effective),
    }
    return loss, effective


def encode_media(plan: Dict[str, Any], encoded: List[Dict[str, Any]],
                 frames_dir: Path, out_dir: Path, *,
                 cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """编码为真实可播放文件；返回容器/量化/资源回执（不含媒体字节）。"""
    import time
    from .freeze import sha256_bytes

    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = str(plan["format"])
    suffix = ".apng" if fmt == "apng" else ".gif"
    path = out_dir / f"animation{suffix}"
    loop_count = int(plan["loop"]["count"])
    deadline = time.monotonic() + float(ENCODE_TIMEOUT_S)
    effective = encoded
    if fmt == "apng":
        write_apng(path, encoded, frames_dir, loop_count=loop_count,
                   deadline=deadline, cancel=cancel)
        loss: Dict[str, Any] = {
            "palette": None, "duration_unit_ms": 1,
            "duration_quantization_loss_ms": 0,
            "mean_absolute_channel_error": 0.0,
            "alpha": "full 8-bit RGBA",
            "container_merged_frames": 0,
        }
        container, codec = "PNG/APNG acTL+fcTL+fdAT", "streaming-zlib-RGBA"
        media_type = "image/apng" if suffix == ".apng" else "image/png"
    elif fmt == "gif":
        loss, effective = write_gif(path, encoded, frames_dir,
                                    loop_count=loop_count, deadline=deadline,
                                    cancel=cancel)
        container, codec = "GIF89a", "Pillow global-palette"
        media_type = "image/gif"
    else:
        raise TemporalMediaError("UNSUPPORTED_FORMAT",
                                 f"format {fmt!r} is not open for encoding",
                                 status_code=422)
    if cancel is not None and cancel():
        path.unlink(missing_ok=True)
        raise TemporalMediaError("CANCELLED", "cancelled before media publication",
                                 status_code=409)
    data = path.read_bytes()
    return {
        "path": str(path),
        "media_type": media_type,
        "extension": suffix,
        "bytes": len(data),
        "checksum": "sha256:" + sha256_bytes(data),
        "container": container,
        "codec": codec,
        "loop": loop_count,
        "encoded_frame_count": len(effective),
        "declared_total_duration_ms": sum(int(e["duration_ms"]) for e in effective),
        "effective_encoded": effective,
        "quantization": loss,
        "encoder_processes": 0,
        "encode_timeout_s": ENCODE_TIMEOUT_S,
        "encode_timeout_enforced": True,
    }


__all__ = ["build_encoded_frames", "write_apng", "write_gif", "encode_media"]
