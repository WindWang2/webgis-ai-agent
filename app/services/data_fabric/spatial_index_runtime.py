"""执行期空间分区/索引设施（ADR-0101 D8，V4 §18）。

- **STRtree 复用**：指纹寻址的有界 LRU 缓存，线程安全；命中免去重复
  建树 —— 只做性能提示，**绝不做正确性机制**（索引永远由当前数据
  重建可得；缓存键含权威修订指纹，修订变化 → 键变化 → 自然失效）。
- **网格分区**：bbox 均匀网格分区（空间连接的 partition 策略入口）。
- **H3 分区**（h3 ≥4.5，v4 API）：特征质心落格；依赖在仓库 mandates
  列表内，缺失时诚实 ``SpatialIndexUnavailable``。

有界性：缓存条目数 + 近似字节双界；分区数硬上界；绝无 O(N²)。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 默认网格划分数（每轴）。
DEFAULT_GRID_CELL = 16
#: H3 分区缺省分辨率（~几十 km 格）。
DEFAULT_H3_RESOLUTION = 7
#: H3 可用性（进程内惰性探测）。
_H3_OK: Optional[bool] = None


class SpatialIndexUnavailable(RuntimeError):
    """空间索引/分区依赖不可用（诚实失败，绝不静默降级到错误结果）。"""


def h3_available() -> bool:
    global _H3_OK
    try:
        import h3  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _feature_centroid(feature: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """特征质心（外包框中心；不做精确质心计算 —— 分区只需稳定快筛）。"""
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return None
    xs: List[float] = []
    ys: List[float] = []
    stack: List[Any] = [geom.get("coordinates")]
    while stack:
        cur = stack.pop()
        if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], (int, float)) \
                and len(cur) >= 2 and isinstance(cur[1], (int, float)):
            xs.append(float(cur[0]))
            ys.append(float(cur[1]))
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _feature_bbox(feature: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    centroid = _feature_centroid(feature)
    if centroid is None:
        return None
    geom = feature.get("geometry")
    xs: List[float] = []
    ys: List[float] = []
    stack: List[Any] = [geom.get("coordinates")]  # type: ignore[arg-type]
    while stack:
        cur = stack.pop()
        if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], (int, float)) \
                and len(cur) >= 2 and isinstance(cur[1], (int, float)):
            xs.append(float(cur[0]))
            ys.append(float(cur[1]))
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


class SpatialIndexRuntime:
    """指纹寻址的 STRtree/索引复用缓存（有界 LRU + 近似字节界）。"""

    def __init__(self, max_entries: int = 64, max_bytes: int = 64 * 1024 * 1024):
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(features_hash: str, kind: str = "strtree") -> str:
        """索引缓存键（调用方以数据集/节点内容指纹为 features_hash）。"""
        return f"{kind}:{hashlib.sha256(features_hash.encode()).hexdigest()[:16]}"

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.get("index")

    def put(self, key: str, index: Any, approx_bytes: int) -> None:
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.get("__bytes__", 0)
            if approx_bytes > self._max_bytes:
                return
            self._entries[key] = {"index": index, "__bytes__": approx_bytes}
            self._bytes += approx_bytes
            while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.get("__bytes__", 0)
                if not self._entries:
                    break

    def invalidate(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
                self._bytes = 0
            else:
                old = self._entries.pop(key, None)
                if old is not None:
                    self._bytes -= old.get("__bytes__", 0)


def build_strtree_index(
    features: List[Dict[str, Any]],
    *,
    content_fingerprint: str,
    runtime: Optional[SpatialIndexRuntime] = None,
) -> Tuple[Any, List[Dict[str, Any]]]:
    """构建（或复用）STRtree 空间索引。

    返回 ``(tree, features)``；调用方用 ``tree.query(bbox)`` 取候选下标
    （shapely 2.x 返回 ndarray 下标）。缓存命中时不重建树。
    """
    if runtime is not None:
        key = SpatialIndexRuntime.fingerprint(content_fingerprint)
        cached = runtime.get(key)
        if cached is not None:
            tree, _geoms = cached
            return tree, features
    try:
        from shapely import STRtree
        import shapely
    except Exception as exc:  # noqa: BLE001
        raise SpatialIndexUnavailable(f"shapely unavailable: {exc}") from exc
    geoms = []
    for f in features:
        bbox = _feature_bbox(f)
        if bbox is None:
            geoms.append(None)
            continue
        minx, miny, maxx, maxy = bbox
        geoms.append(shapely.box(minx, miny, maxx, maxy))
    valid = [g for g in geoms if g is not None]
    tree = STRtree(valid) if valid else None
    if runtime is not None and tree is not None:
        approx = sum(len(str(g)) for g in valid[:64]) * max(1, len(valid) // 64)
        runtime.put(SpatialIndexRuntime.fingerprint(content_fingerprint),
                    (tree, geoms), approx)
    return tree, geoms


def grid_partition(
    features: List[Dict[str, Any]],
    *,
    cells_per_axis: int = DEFAULT_GRID_CELL,
) -> Dict[Tuple[int, int], List[Dict[str, Any]]]:
    """bbox 均匀网格分区（质心落格；分区数 ≤ cells²，结构性有界）。"""
    if cells_per_axis < 1 or cells_per_axis > 256:
        raise ValueError(f"cells_per_axis out of range [1,256]: {cells_per_axis}")
    out: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for f in features:
        centroid = _feature_centroid(f)
        if centroid is None:
            continue
        cx, cy = centroid
        # 偏移到非负域后均匀落格（分区只需稳定，不需要地理正确）。
        gx = min(cells_per_axis - 1, max(0, int((cx + 360.0) / (720.0 / cells_per_axis))))
        gy = min(cells_per_axis - 1, max(0, int((cy + 90.0) / (180.0 / cells_per_axis))))
        out.setdefault((gx, gy), []).append(f)
    return out


def h3_partition(
    features: List[Dict[str, Any]],
    *,
    resolution: int = DEFAULT_H3_RESOLUTION,
) -> Dict[str, List[Dict[str, Any]]]:
    """H3 质心分区（v4 API ``latlng_to_cell``；缺失依赖 → typed 失败）。"""
    if not h3_available():
        raise SpatialIndexUnavailable(
            "h3 dependency unavailable; use grid_partition or install h3>=4.5")
    import h3

    out: Dict[str, List[Dict[str, Any]]] = {}
    for f in features:
        centroid = _feature_centroid(f)
        if centroid is None:
            continue
        cx, cy = centroid
        cell = h3.latlng_to_cell(cy, cx, resolution)  # (lat, lng)
        out.setdefault(cell, []).append(f)
    return out
