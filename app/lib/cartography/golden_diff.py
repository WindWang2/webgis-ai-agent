"""像素级 golden 图像 diff（任务书 10 线 P3，ADR-0159）。

容差策略沿用 runtime probe 经验（``tests/unit/test_runtime_fixture_contract.py``
的取色容差）：逐通道 ±16 视为同色；同场景多取色点之间的通道距离必须 >48
（取色点彼此不可分的"golden"是假 golden）。整体判定：≥98% 像素逐通道差在
容差内 → 通过；尺寸不一致直接失败（诚实报错，绝不静默 resize 对齐）。

纯函数、无 IO 依赖（bytes 进 bytes 出），供 ``scripts/golden_baseline.py``
与 tests/quality 回归锁共用。Pillow 是既有直接依赖。
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 逐通道绝对差容差（runtime probe 同款 ±16）
PER_CHANNEL_TOLERANCE = 16
#: 同场景多取色点间的最小通道距离（probe 经验 >48）
SAMPLE_POINT_MIN_DISTANCE = 48
#: 容差内像素占比的通过线
PASS_PIXEL_RATIO = 0.98


def decode_png(png_bytes: bytes) -> Tuple[int, int, bytes]:
    """PNG bytes → (width, height, RGB8 像素字节)。失败抛 ValueError。"""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            rgb = img.convert("RGB")
            return rgb.width, rgb.height, rgb.tobytes()
    except Exception as exc:  # noqa: BLE001 — 统一成 ValueError 语义
        raise ValueError(f"undecodable PNG: {exc}") from exc


def _clamp(color: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, c)) for c in color)  # type: ignore[return-value]


def color_distance(
    a: Sequence[int], b: Sequence[int], tolerance: int = 0
) -> int:
    """通道距离：三通道绝对差之和（超过 tolerance 的部分才算距离）。"""
    return sum(
        max(0, abs(int(a[i]) - int(b[i])) - tolerance) for i in range(3)
    )


def image_diff(
    baseline_png: bytes,
    current_png: bytes,
    per_channel_tolerance: int = PER_CHANNEL_TOLERANCE,
    pass_ratio: float = PASS_PIXEL_RATIO,
) -> Dict[str, Any]:
    """像素级 diff：逐通道容差统计 + 整体通过判定（numpy 向量化）。"""
    bw, bh, base = decode_png(baseline_png)
    cw, ch, cur = decode_png(current_png)
    if (bw, bh) != (cw, ch):
        return {
            "pass": False,
            "reason": (
                f"size mismatch: baseline {bw}x{bh} vs current {cw}x{ch}"
                " —— 不静默 resize 对齐，先确认视口契约"
            ),
            "baseline_size": [bw, bh],
            "current_size": [cw, ch],
        }
    import numpy as np

    a = np.frombuffer(base, dtype=np.uint8).reshape(bh, bw, 3).astype(np.int16)
    b = np.frombuffer(cur, dtype=np.uint8).reshape(ch, cw, 3).astype(np.int16)
    abs_diff = np.abs(a - b)                      # (h, w, 3)
    pixel_max = abs_diff.max(axis=2)              # (h, w)
    total = bw * bh
    within = int((pixel_max <= per_channel_tolerance).sum())
    ratio = within / total if total else 0.0
    return {
        "pass": ratio >= pass_ratio,
        "width": bw,
        "height": bh,
        "pixels": total,
        "within_tolerance": within,
        "within_ratio": round(ratio, 5),
        "pass_ratio_required": pass_ratio,
        "per_channel_tolerance": per_channel_tolerance,
        "mean_abs_diff": round(float(abs_diff.mean()), 3),
        "mean_channel_diff": [
            round(float(c), 3) for c in abs_diff.reshape(-1, 3).mean(axis=0)
        ],
        "max_channel_diff": int(pixel_max.max()),
    }


def sample_points_distinguishable(
    points: Sequence[Tuple[int, int, int]],
    min_distance: int = SAMPLE_POINT_MIN_DISTANCE,
    per_channel_tolerance: int = PER_CHANNEL_TOLERANCE,
) -> Dict[str, Any]:
    """同场景多取色点两两通道距离 >48 —— 彼此不可分的 golden 是假 golden。

    单点（或空）无所谓可分性，直接通过。
    """
    pairs: List[Dict[str, Any]] = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            distance = color_distance(
                points[i], points[j], tolerance=per_channel_tolerance
            )
            pairs.append({
                "a": list(points[i]),
                "b": list(points[j]),
                "distance": distance,
                "ok": distance > min_distance,
            })
    return {
        "pass": all(p["ok"] for p in pairs),
        "min_distance_required": min_distance,
        "pairs": pairs,
    }


def compare_golden(
    baseline_png: bytes,
    current_png: bytes,
    baseline_samples: Optional[Sequence[Tuple[int, int, int]]] = (),
    current_samples: Optional[Sequence[Tuple[int, int, int]]] = (),
) -> Dict[str, Any]:
    """完整 golden 断言：像素 diff + 取色点可分性（两侧都要可分）。"""
    diff = image_diff(baseline_png, current_png)
    result: Dict[str, Any] = {"pixel_diff": diff}
    ok = bool(diff.get("pass"))
    if baseline_samples:
        base_sep = sample_points_distinguishable(list(baseline_samples))
        result["baseline_sample_separability"] = base_sep
        ok = ok and base_sep["pass"]
    if current_samples:
        cur_sep = sample_points_distinguishable(list(current_samples))
        result["current_sample_separability"] = cur_sep
        ok = ok and cur_sep["pass"]
    result["pass"] = ok
    if "reason" not in result and not result["pass"]:
        result["reason"] = "pixel diff or sample separability failed"
    return result
