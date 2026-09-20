"""Estimate-vs-actual 校准统计（R5，ADR-0204 D4）。

闭环的"actual → calibration"半边：dispatch 完成时的实际用量按
``(subsystem, tool)`` 键聚合进**有界**环形样本，产出 per-dim 的
actual/estimated 比值统计。

红线：
- **有界**：≤ MAX_KEYS 个键 × 每键每维 RING 样本（环形覆盖），进程内存
  占用常数级；
- **只观测不反馈**：运行时统计绝不自动修改估算先验/预算阈值 —— 先验更新
  只能经 :func:`suggest_priors` 产出离线建议文件、人工评审后显式改表
  （ADR-0182 provisional 纪律同源）；漂移兜底靠 provisional 预算 +
  admission 硬上限，不靠自学习；
- **绝不阻断**：record/stats 的任何异常由调用方兜底（dispatch complete
  记账同纪律）。
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from typing import Dict, List, Optional, Tuple

from app.services.governor.contract import (
    Dimension,
    ResourceEstimate,
    ResourceUsage,
)

logger = logging.getLogger(__name__)

#: 有界性（review 纪律：close/release 无生产调用方也要常数内存）
MAX_KEYS = 256
RING_SAMPLES = 64
MAX_KEY_LEN = 128

#: 单键纳入统计的最小样本（低于此数的键在 suggest 中标注 insufficient）
MIN_SAMPLES_FOR_SUGGESTION = 8


class _DimSamples:
    """一个 (key, dim) 的环形样本（expected, actual 对）。"""

    __slots__ = ("samples",)

    def __init__(self) -> None:
        self.samples: deque = deque(maxlen=RING_SAMPLES)

    def add(self, expected: float, actual: float) -> None:
        self.samples.append((float(expected), float(actual)))

    def stats(self) -> Optional[Dict[str, float]]:
        """n / mean_ratio / max_ratio；无有效样本 → None。

        ratio 只在 expected>0 且 actual≥0 时有效（expected≤0 的维度本就
        不该计费；负 actual 是观测异常，丢弃而非聚合成负漂移）。
        """
        ratios: List[float] = []
        for expected, actual in self.samples:
            if expected > 0 and actual >= 0:
                ratios.append(actual / expected)
        if not ratios:
            return None
        return {
            "n": len(ratios),
            "mean_ratio": sum(ratios) / len(ratios),
            "max_ratio": max(ratios),
        }


class CalibrationStore:
    """进程级 estimate-vs-actual 样本账（线程安全；bounded）。"""

    def __init__(self, *, max_keys: int = MAX_KEYS) -> None:
        self._max_keys = max_keys
        self._keys: Dict[str, Dict[str, _DimSamples]] = {}
        self._lock = threading.Lock()

    def record(self, tool_key: str, dim: Dimension,
               expected: float, actual: float) -> None:
        """记一个 (expected, actual) 样本（非法值静默丢弃）。"""
        key = str(tool_key or "")[:MAX_KEY_LEN]
        if not key or expected is None or actual is None:
            return
        try:
            expected = float(expected)
            actual = float(actual)
        except (TypeError, ValueError):
            return
        if expected <= 0 or actual < 0:
            return
        if not (math.isfinite(expected) and math.isfinite(actual)):
            # NaN 已被上两行排除；inf 拒收（比值统计与 JSON 序列化都不容它）
            return
        with self._lock:
            dims = self._keys.get(key)
            if dims is None:
                if len(self._keys) >= self._max_keys:
                    # 简单准入控制：满员时拒绝新键（已有键继续滚动）——
                    # 驱逐策略会把"最久未见"的键清掉，但校准面宁可保守：
                    # 新工具的漂移由 Prometheus 直方图兜底观测。
                    return
                dims = self._keys[key] = {}
            dims.setdefault(dim.value, _DimSamples()).add(expected, actual)

    def record_usage(self, tool_key: str, usage: ResourceUsage,
                     estimate: Optional[ResourceEstimate]) -> int:
        """ResourceUsage + estimate → 批量样本；返回记录数。

        actual 取 usage.dims + wall_time_s；expected 取 estimate 同维
        adjudged 值（unknown → 地板，与准入计费同口径，避免把"没估"记成
        比值 0）。
        """
        if usage is None:
            return 0
        recorded = 0
        actuals: Dict[Dimension, float] = {
            d: float(v) for d, v in (usage.dims or {}).items()
        }
        if usage.wall_time_s is not None:
            actuals[Dimension.WALL_TIME_S] = float(usage.wall_time_s)
        for dim, actual in actuals.items():
            if estimate is None:
                continue
            dv = estimate.dim(dim)
            if not dv.is_meaningful():
                continue
            expected = dv.adjudged(dim=dim)
            self.record(tool_key, dim, expected, actual)
            recorded += 1
        return recorded

    def stats(self, tool_key: Optional[str] = None) -> Dict[str, Dict]:
        """统计视图：缺省全量（键 → dim → {n, mean_ratio, max_ratio}）。"""
        with self._lock:
            keys = [str(tool_key)[:MAX_KEY_LEN]] if tool_key \
                else list(self._keys)
            out: Dict[str, Dict] = {}
            for key in keys:
                dims = self._keys.get(key) or {}
                view: Dict[str, Dict[str, float]] = {}
                for dim_name, samples in dims.items():
                    st = samples.stats()
                    if st is not None:
                        view[dim_name] = {
                            "n": st["n"],
                            "mean_ratio": round(st["mean_ratio"], 4),
                            "max_ratio": round(st["max_ratio"], 4),
                        }
                if view:
                    out[key] = view
            return out

    def snapshot(self) -> Dict:
        """可序列化快照（离线校准脚本消费；restore 可复原）。"""
        with self._lock:
            return {
                "version": "calibration.v1",
                "max_keys": self._max_keys,
                "ring": RING_SAMPLES,
                "keys": {
                    key: {
                        dim: {"samples": [
                            [e, a] for (e, a) in s.samples]}
                        for dim, s in dims.items()
                    }
                    for key, dims in self._keys.items()
                },
            }

    def restore(self, snap: Dict) -> None:
        """从快照复原（校准脚本回放/测试用；超界样本静默截断）。"""
        if not isinstance(snap, dict):
            return
        keys = snap.get("keys")
        if not isinstance(keys, dict):
            return
        with self._lock:
            self._keys = {}
            for key in list(keys)[:self._max_keys]:
                dims_raw = keys.get(key) or {}
                dims: Dict[str, _DimSamples] = {}
                for dim_name, payload in list(dims_raw.items())[:len(Dimension)]:
                    if not isinstance(payload, dict):
                        continue
                    holder = _DimSamples()
                    raw_samples = payload.get("samples")
                    if not isinstance(raw_samples, (list, tuple)):
                        continue
                    for pair in list(raw_samples)[-RING_SAMPLES:]:
                        if isinstance(pair, (list, tuple)) and len(pair) == 2:
                            try:
                                holder.add(float(pair[0]), float(pair[1]))
                            except (TypeError, ValueError):
                                continue
                    dims[str(dim_name)[:64]] = holder
                if dims:
                    self._keys[str(key)[:MAX_KEY_LEN]] = dims

    def key_count(self) -> int:
        with self._lock:
            return len(self._keys)


def suggest_priors(snapshot: Dict, *,
                   min_samples: int = MIN_SAMPLES_FOR_SUGGESTION,
                   drift_band: Tuple[float, float] = (0.7, 1.5)) -> Dict:
    """快照 → 离线先验建议（**建议文件，绝不自动应用**）。

    只对样本数 ≥ min_samples 的 (key, dim) 产出建议；mean_ratio 落在
    drift_band 内视为校准良好（无建议）。输出结构对齐
    ``config/governor_budgets.json`` 的评审流程：人工核对后显式改
    ``estimation.py`` 先验表 / budget manifest。
    """
    suggestions: List[Dict] = []
    keys = (snapshot or {}).get("keys") or {}
    for key, dims in keys.items():
        for dim_name, payload in (dims or {}).items():
            try:
                floor = Dimension(dim_name)
            except ValueError:
                continue
            holder = _DimSamples()
            raw_samples = (payload or {}).get("samples")
            if not isinstance(raw_samples, (list, tuple)):
                continue
            for pair in raw_samples:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    try:
                        holder.add(float(pair[0]), float(pair[1]))
                    except (TypeError, ValueError):
                        continue
            st = holder.stats()
            if st is None or st["n"] < min_samples:
                continue
            mean = st["mean_ratio"]
            if drift_band[0] <= mean <= drift_band[1]:
                continue
            suggestions.append({
                "tool_key": key,
                "dimension": floor.value,
                "n": int(st["n"]),
                "mean_ratio": round(mean, 4),
                "max_ratio": round(st["max_ratio"], 4),
                "suggested_factor": round(mean, 3),
                "action": (
                    "raise_prior" if mean > drift_band[1]
                    else "lower_prior"),
            })
    return {
        "version": "calibration_suggest.v1",
        "note": (
            "offline suggestion only — applying requires explicit edit of "
            "governor/estimation.py priors or governor_budgets.json "
            "(provisional discipline, ADR-0182/0204); mean_ratio is a "
                "central tendency and does not capture distribution shape — "
                "check max_ratio alongside"),
        "drift_band": list(drift_band),
        "min_samples": min_samples,
        "suggestions": suggestions,
    }


_PROCESS_STORE: Optional[CalibrationStore] = None
_PROCESS_LOCK = threading.Lock()


def get_calibration_store() -> CalibrationStore:
    """进程级默认 store（首调构造；有界 256 键）。"""
    global _PROCESS_STORE
    with _PROCESS_LOCK:
        if _PROCESS_STORE is None:
            _PROCESS_STORE = CalibrationStore()
        return _PROCESS_STORE


def reset_calibration_store_for_tests(store: Optional[CalibrationStore] = None
                                      ) -> CalibrationStore:
    global _PROCESS_STORE
    with _PROCESS_LOCK:
        _PROCESS_STORE = store if store is not None else CalibrationStore()
        return _PROCESS_STORE


__all__ = [
    "CalibrationStore",
    "get_calibration_store",
    "reset_calibration_store_for_tests",
    "suggest_priors",
    "MAX_KEYS",
    "RING_SAMPLES",
    "MIN_SAMPLES_FOR_SUGGESTION",
]
