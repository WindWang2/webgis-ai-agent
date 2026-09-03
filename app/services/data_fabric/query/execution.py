"""V2 执行助手：确定性采样、cursor 编解码、流式预算、聚合计算。

这些原语被 adapters 与 materialization 管线共享，保证各源之间语义一致
（差分测试的基础）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from app.services.data_fabric.errors import InvalidQueryError, QueryBudgetExceededError
from app.services.data_fabric.query.models import AggSpec, QuerySpecV2, SampleSpec


# ── 确定性采样 ──────────────────────────────────────────────────────────────


def sample_seed(dataset_fingerprint: Optional[str], sample: SampleSpec) -> int:
    """seed = f(dataset_fingerprint, size)；同一数据集同一采样规格永远一致。"""
    if sample.seed is not None:
        return sample.seed
    basis = f"{dataset_fingerprint or '-'}:{sample.size}:{sample.method}"
    return int(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8], 16)


def deterministic_sample(
    features: Sequence[Dict[str, Any]],
    sample: SampleSpec,
    dataset_fingerprint: Optional[str],
) -> List[Dict[str, Any]]:
    """确定性 reservoir 采样（seed 可复现）；method='first' 退化为前 N。"""
    n = len(features)
    if n <= sample.size:
        return list(features)
    if sample.method == "first":
        return list(features[: sample.size])
    seed = sample_seed(dataset_fingerprint, sample)
    rng = _LCG(seed)
    # Reservoir sampling (Vitter's Algorithm R) — 输入序确定 ⇒ 输出确定。
    reservoir: List[Dict[str, Any]] = list(features[: sample.size])
    for i in range(sample.size, n):
        j = rng.next() % (i + 1)
        if j < sample.size:
            reservoir[j] = features[i]
    return reservoir


class _LCG:
    """线性同余发生器：跨平台确定性（random.Random 的可复现性依赖版本）。"""

    def __init__(self, seed: int):
        self._state = (seed or 1) & 0xFFFFFFFFFFFF

    def next(self) -> int:
        # Musl 风格参数
        self._state = (6364136223846793005 * self._state + 1) & 0xFFFFFFFFFFFFFFFF
        return self._state >> 33


# ── Cursor（keyset）编解码 ──────────────────────────────────────────────────


def encode_cursor(order_keys: Sequence[Any]) -> str:
    """排序键值 → 不透明 base64 cursor。"""
    payload = json.dumps(list(order_keys), ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> List[Any]:
    try:
        payload = base64.urlsafe_b64decode(cursor.encode("ascii"))
        return json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidQueryError(f"malformed cursor: {e}") from e


# ── 流式预算守卫 ────────────────────────────────────────────────────────────


class StreamingBudget:
    """执行期行/字节/顶点预算（Wave H 内存红线的运行时强制点）。"""

    def __init__(self, max_rows: int, max_bytes: int, max_vertices: int):
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.max_vertices = max_vertices
        self.rows = 0
        self.bytes = 0
        self.vertices = 0

    def add_feature(self, feature: Dict[str, Any]) -> None:
        self.rows += 1
        if self.rows > self.max_rows:
            raise QueryBudgetExceededError(
                f"row budget exceeded ({self.max_rows})",
                details={"hint": "reduce limit, add filters/bbox, or use aggregation"},
            )
        self.bytes += _feature_bytes(feature)
        if self.bytes > self.max_bytes:
            raise QueryBudgetExceededError(
                f"byte budget exceeded ({self.max_bytes})",
                details={"hint": "project fewer fields or narrow the spatial filter"},
            )
        self.vertices += _feature_vertices(feature)
        if self.vertices > self.max_vertices:
            raise QueryBudgetExceededError(
                f"vertex budget exceeded ({self.max_vertices})",
                details={"hint": "narrow bbox or use tiles for high-density geometry"},
            )


def _feature_bytes(feature: Dict[str, Any]) -> int:
    props = feature.get("properties") or {}
    est = 64
    for k, v in props.items():
        est += len(k) + (len(v) if isinstance(v, str) else 16)
    geom = feature.get("geometry")
    if isinstance(geom, dict):
        coords = geom.get("coordinates")
        if isinstance(coords, list):
            est += _count_positions(coords) * 18
    return est


def _count_positions(coords: Any) -> int:
    if not isinstance(coords, list):
        return 0
    if coords and isinstance(coords[0], (int, float)):
        return 1
    return sum(_count_positions(c) for c in coords)


def _feature_vertices(feature: Dict[str, Any]) -> int:
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return 0
    return _count_positions(geom.get("coordinates"))


# ── 本地聚合（无下推源的有界聚合）─────────────────────────────────────────


def compute_aggregates(
    rows: Iterable[Dict[str, Any]],
    aggs: Sequence[AggSpec],
    group_by: Optional[Sequence[str]],
) -> List[Dict[str, Any]]:
    """对（已有界的）属性行执行聚合。值语义与 SQL 对齐（NULL 不计入聚合）。

    stddev 为总体标准差（STDDEV_POP）；PostGIS 编译走 stddev_samp 时 planner
    统一选择——本地与服务器差分测试以 same-population 口径对齐。
    """
    groups: Dict[Tuple, Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(g) for g in group_by) if group_by else ()
        acc = groups.get(key)
        if acc is None:
            acc = {"__rows__": []}
            groups[key] = acc
        acc["__rows__"].append(row)

    out: List[Dict[str, Any]] = []
    for key, acc in groups.items():
        result: Dict[str, Any] = {}
        if group_by:
            for g, v in zip(group_by, key):
                result[g] = v
        rows_n = acc["__rows__"]
        for a in aggs:
            name = a.func if a.field is None else f"{a.func}_{a.field}"
            if a.func == "count" and a.field is None:
                result[name] = len(rows_n)
                continue
            vals = [r.get(a.field) for r in rows_n if r.get(a.field) is not None]
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if a.func == "count":
                result[name] = len(vals)
            elif a.func == "distinct_count":
                result[name] = len(set(map(_hashable, vals)))
            elif a.func == "sum":
                result[name] = sum(nums) if nums else None
            elif a.func == "avg":
                result[name] = (sum(nums) / len(nums)) if nums else None
            elif a.func == "min":
                result[name] = min(vals) if vals else None
            elif a.func == "max":
                result[name] = max(vals) if vals else None
            elif a.func == "stddev":
                if len(nums) < 2:
                    result[name] = None
                else:
                    mean = sum(nums) / len(nums)
                    var = sum((x - mean) ** 2 for x in nums) / len(nums)  # POP
                    result[name] = math.sqrt(var)
        out.append(result)
    if not group_by and out == [] and not groups:
        # 空输入的全局聚合仍输出一行（与 SQL SELECT count(*) 一致）
        result = {}
        for a in aggs:
            name = a.func if a.field is None else f"{a.func}_{a.field}"
            result[name] = 0 if a.func in ("count",) else None
        out.append(result)
    return out


def _hashable(v: Any) -> Any:
    if isinstance(v, list):
        return tuple(v)
    return v


__all__ = [
    "sample_seed",
    "deterministic_sample",
    "encode_cursor",
    "decode_cursor",
    "StreamingBudget",
    "compute_aggregates",
]
