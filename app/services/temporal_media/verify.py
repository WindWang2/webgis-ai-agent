"""WP08/WP11 — 独立解码与内容 oracle。

编码器退出码为零**不等于**成功：用独立读取路径（Pillow）逐帧解码最终容器，
核对首/末/中间及变化边界的画面、时间标注、持续时间与 manifest 映射；
小 fixture 检查全部帧。

故障注入必须红检：时间标签提前一帧 / 全片替换成首帧 / 删除一个变化帧 /
单层过滤未生效 / 帧文件损坏，均不得通过。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from .contracts import TemporalMediaError
from .freeze import canonical_json, sha256_bytes, sha256_text


def _normalize_rgba(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return im.convert("RGBA")


def decode_container(path: Path, fmt: str) -> Dict[str, Any]:
    """独立读取：逐帧 seek，记录时长/尺寸/像素摘要，不全片驻留。"""
    with Image.open(path) as im:
        n_frames = int(getattr(im, "n_frames", 1) or 1)
        loop = im.info.get("loop")
        size = list(im.size)
        frames: List[Dict[str, Any]] = []
        for index in range(n_frames):
            im.seek(index)
            rgba = im.convert("RGBA")
            frames.append({
                "index": index,
                "duration_ms": int(round(float(im.info.get("duration") or 0))),
                "size": list(im.size),
                "pixel_sha256": "sha256:" + sha256_bytes(rgba.tobytes()),
            })
    return {"n_frames": n_frames, "loop": loop, "size": size,
            "frames": frames, "format": fmt}


def _parse_contract_instant(raw: str, tz: Any) -> Any:
    """独立解析：把契约 ISO instant 与要素原始值都变成 aware datetime 再比较。

    不复用 ``time_filter.normalize_boundary_value``（那是渲染与过滤共用的实现），
    避免「归一逻辑错了，渲染和 oracle 同步错还全绿」。
    """
    from datetime import datetime, timezone

    cleaned = str(raw).replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _oracle_expected_ids(plan: Dict[str, Any], layer_id: str,
                         window: Dict[str, str]) -> List[str]:
    """从冻结文档**独立**重算本窗应出现的要素（不信任渲染证据计数）。"""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tc = plan["temporal_contract"]
    tz = ZoneInfo(tc.get("timezone") or "UTC")
    rep = tc["time_representation"]
    gran = tc["granularity"]
    field = str(tc["time_field"])
    start = _parse_contract_instant(window["start"], tz)
    end = _parse_contract_instant(window["end"], tz)

    def value_instant(value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return None
        if rep == "year_int":
            if not isinstance(value, (int, float)):
                return None
            return datetime(int(value), 1, 1, tzinfo=tz)
        if rep in ("epoch_s", "epoch_ms"):
            if not isinstance(value, (int, float)):
                return None
            seconds = float(value) / (1000.0 if rep == "epoch_ms" else 1.0)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if not isinstance(value, str):
            return None
        if rep == "iso_string":
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        try:
            if gran == "year":
                return datetime(int(value[:4]), 1, 1, tzinfo=tz)
            if gran in ("month", "quarter"):
                return datetime(int(value[:4]), int(value[5:7]), 1, tzinfo=tz)
            return datetime(int(value[:4]), int(value[5:7]), int(value[8:10]),
                            tzinfo=tz)
        except (ValueError, TypeError, IndexError):
            return None

    doc = plan["frozen"]["document"]
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    target_sid = None
    for layer in doc.get("layers") or []:
        if isinstance(layer, dict) and str(layer.get("id") or "") == layer_id:
            target_sid = layer.get("source")
            break
    src = sources.get(target_sid) if isinstance(target_sid, str) else None
    payload = (src.get("inlineData") or src.get("data")) if isinstance(src, dict) else None
    out: List[str] = []
    if not isinstance(payload, dict):
        return out
    for feature in payload.get("features") or []:
        if not isinstance(feature, dict):
            continue
        instant = value_instant((feature.get("properties") or {}).get(field))
        if instant is not None and start <= instant < end:
            out.append(str(feature.get("id")) if feature.get("id") is not None
                       else "h" + sha256_text(canonical_json(feature))[:12])
    return sorted(out)


def _expected_label(plan: Dict[str, Any], segment: Dict[str, Any]) -> str:
    """计划是冻结事实源：标签从观测桶/缺期身份重新拼出，不接受证据自述。"""
    mode = str(plan["display"]["time_display"])
    if mode == "none":
        return ""
    label = str(segment["observation_label"])
    if segment["kind"] == "empty":
        return f"{label}（缺期·无观测）"
    if segment["kind"] == "held":
        source = next((str(o["label"]) for o in plan["observations"]
                       if o["id"] == segment["held_from"]), "")
        return f"{label}（缺期·沿用 {source} 观测）" if source \
            else f"{label}（缺期·沿用）"
    if mode == "label_window":
        return (f"{label} [{segment['observation_start']}"
                f" ~ {segment['observation_end']})")
    return label


def _mae(path_a: Path, image_b: Image.Image) -> float:
    import numpy as np
    with Image.open(path_a) as im:
        im.load()
        a = np.asarray(im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR),
                       dtype=np.int16)
    b = np.asarray(image_b.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR),
                   dtype=np.int16)
    return float(np.abs(a - b).mean())


def _decoded_mae(path: Path, index: int, expected_png: Path) -> float:
    import numpy as np
    with Image.open(path) as im:
        im.seek(index)
        a = np.asarray(im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR),
                       dtype=np.int16)
    with Image.open(expected_png) as ref:
        ref.load()
        b = np.asarray(ref.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR),
                       dtype=np.int16)
    return float(np.abs(a - b).mean())


def verify_delivery(
    plan: Dict[str, Any], encoded: List[Dict[str, Any]],
    media: Dict[str, Any], digital_by_segment: List[Dict[str, Any]],
    frames_dir: Path, svgs_dir: Path,
    *, cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """完整核验；任一项失败抛 typed 错误，绝不把损坏媒体标 completed。"""
    checks: List[Dict[str, Any]] = []
    failures: List[str] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "passed": bool(ok),
                       **({"detail": detail} if detail is not None else {})})
        if not ok:
            failures.append(name)

    path = Path(media["path"])
    if not path.is_file() or path.stat().st_size == 0:
        raise TemporalMediaError("CONTAINER_MISSING",
                                 "encoded media file is missing or empty",
                                 status_code=502)
    with open(path, "rb") as handle:
        raw = handle.read()
    check("container_checksum", "sha256:" + sha256_bytes(raw) == media["checksum"])
    magic_ok = (raw[:8] == b"\x89PNG\r\n\x1a\n"
                if plan["format"] == "apng"
                else raw[:6] in (b"GIF89a", b"GIF87a"))
    check("container_magic", magic_ok)
    decoded = decode_container(path, str(plan["format"]))
    check("frame_count", decoded["n_frames"] == len(encoded),
          {"decoded": decoded["n_frames"], "planned": len(encoded)})
    check("canvas_size",
          decoded["size"] == [int(plan["canvas"]["width"]),
                              int(plan["canvas"]["height"])], decoded["size"])
    check("loop", int(decoded["loop"] or 0) == int(plan["loop"]["count"]),
          {"decoded": decoded["loop"], "planned": plan["loop"]["count"]})

    segments = plan["display"]["segments"]
    covered: List[int] = []
    segment_duration: Dict[int, int] = {}
    for entry in encoded:
        covered.extend(int(i) for i in entry["segment_indices"])
        for key, value in (entry.get("segment_durations_ms") or {}).items():
            idx = int(key)
            segment_duration[idx] = segment_duration.get(idx, 0) + int(value)
    check("segment_mapping_complete",
          sorted(set(covered)) == list(range(len(segments))),
          {"covered_unique": len(set(covered)), "expected": len(segments)})
    check("segment_duration_mapping",
          all(segment_duration.get(int(s["index"]), -1) == int(s["duration_ms"])
              for s in segments),
          {str(k): v for k, v in list(segment_duration.items())[:12]})

    # 数字帧证据：文件可解码、checksum 正确、时间标注与内容窗独立复核。
    digital_by_id: Dict[str, Dict[str, Any]] = {}
    for segment in segments:
        evidence = digital_by_segment[int(segment["index"])]
        key = evidence["digital_frame_id"]
        digital_by_id[key] = evidence
        png_path = frames_dir / evidence["png_path"]
        if not png_path.is_file():
            check(f"digital_frame_{segment['index']}_present", False)
            continue
        data = png_path.read_bytes()
        check(f"digital_frame_{segment['index']}_checksum",
              "sha256:" + sha256_bytes(data) == evidence["png_sha256"])
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                check(f"digital_frame_{segment['index']}_decodable",
                      list(im.size) == evidence["size"])
        except Exception:  # noqa: BLE001
            check(f"digital_frame_{segment['index']}_decodable", False)
        expected_label = _expected_label(plan, segment)
        check(f"digital_frame_{segment['index']}_label",
              evidence["time_label"] == expected_label,
              {"expected": expected_label[:80], "got": str(evidence["time_label"])[:80]})
        svg_path = svgs_dir / f"{key}.svg"
        if segment["time_label"]:
            if not svg_path.is_file():
                check(f"digital_frame_{segment['index']}_svg", False)
            else:
                svg = svg_path.read_text(encoding="utf-8")
                window_note = (
                    "窗口 [{} ~ {}）".format(
                        evidence["display_window"]["start"],
                        evidence["display_window"]["end"]))
                check(f"digital_frame_{segment['index']}_svg",
                      "sha256:" + sha256_text(svg) == evidence["svg_sha256"]
                      and f"时间: {expected_label}" in svg
                      and window_note in svg,
                      {"svg_bytes": len(svg), "window_note": window_note[:96]})
        oracle_ok = True
        for layer in evidence["layers"]:
            expected_ids = _oracle_expected_ids(plan, str(layer["layer_id"]),
                                                evidence["content_window"])
            if sorted(layer["feature_ids"])[:64] != expected_ids[:64]:
                oracle_ok = False
        check(f"digital_frame_{segment['index']}_content_oracle", oracle_ok)

    # 相邻不同内容的观测不得产出同一张画面（全片替换成首帧的红检）。
    if plan["display"]["time_display"] == "none":
        for prev, nxt in zip(segments, segments[1:]):
            a = digital_by_segment[int(prev["index"])]
            b = digital_by_segment[int(nxt["index"])]
            if (prev["identity"] != nxt["identity"] and a["png_sha256"] == b["png_sha256"]
                    and any(a["layers"][i]["expected_feature_ids"]
                            != b["layers"][i]["expected_feature_ids"]
                            for i in range(min(len(a["layers"]), len(b["layers"]))))):
                check("distinct_observation_picture", False,
                      {"segment": int(nxt["index"])})

    decoded_frames = decoded["frames"]
    tolerance = 0 if plan["format"] == "apng" else 10
    for index, entry in enumerate(encoded):
        if index >= len(decoded_frames):
            break
        frame = decoded_frames[index]
        check(f"duration_{index}",
              abs(int(frame["duration_ms"]) - int(entry["duration_ms"])) <= tolerance,
              {"decoded": frame["duration_ms"], "planned": entry["duration_ms"]})
        check(f"frame_size_{index}",
              frame["size"] == [int(plan["canvas"]["width"]),
                                int(plan["canvas"]["height"])])
        evidence = digital_by_id.get(entry["digital_frame_id"])
        if evidence is None:
            check(f"frame_picture_{index}", False)
            continue
        expected_png = frames_dir / evidence["png_path"]
        if plan["format"] == "apng":
            expected = _normalize_rgba(expected_png)
            check(f"frame_picture_{index}",
                  frame["pixel_sha256"] == "sha256:" + sha256_bytes(expected.tobytes()))
        else:
            try:
                error = _decoded_mae(path, index, expected_png)
                limit = max(25.0, float(
                    (media.get("quantization") or {}).get(
                        "mean_absolute_channel_error") or 0.0) * 2 + 12.0)
                check(f"frame_picture_{index}", error <= limit,
                      {"mean_absolute_error": round(error, 3), "limit": round(limit, 3)})
            except Exception:  # noqa: BLE001
                check(f"frame_picture_{index}", False)

    total_decoded = sum(int(f["duration_ms"]) for f in decoded_frames)
    planned_total = int(plan["display"]["timing"]["total_display_ms"])
    check("total_duration", abs(total_decoded - planned_total)
          <= (0 if plan["format"] == "apng" else 10 * len(decoded_frames)),
          {"decoded": total_decoded, "planned": planned_total})
    if cancel is not None and cancel():
        raise TemporalMediaError("CANCELLED", "cancelled during verification",
                                 status_code=409)
    passed = not failures
    return {
        "schema_version": "temporal_media_verification.v1",
        "passed": passed,
        "failures": failures,
        "checks": checks[:400],
        "container": {"n_frames": decoded["n_frames"], "loop": decoded["loop"],
                      "size": decoded["size"], "bytes": media["bytes"]},
        "decoded_frames": len(decoded_frames),
        "total_duration_ms": total_decoded,
        "verified_boundaries": sorted({0, len(decoded_frames) - 1,
                                       len(decoded_frames) // 2}),
    }


__all__ = ["decode_container", "verify_delivery", "_expected_label",
           "_oracle_expected_ids"]
