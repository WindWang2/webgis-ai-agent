"""V6 Bloom 半连接（ADR-0118 W5）：确定性 Bloom 键过滤 + 盈利阈值。

语义安全：Bloom 只有**假阳性**（多余行保留，后续精确 join 兜住），绝无
**假阴性**（不丢任何 join 命中行）—— 键过滤永不改变 join 结果。
确定性：sha256 双哈希派生 k 个位（无第三方依赖；同键集同位图）。
降级：键数超容量 → ``saturated=True``，调用方回退 V5 键集 semi-join
（``federation._semi_join_reduce_right`` 的诚实放弃文化在此延续）。

键归一化与 V5 join 语义的**相等类**对齐（``federation._hashable_key``：
1 ≡ 1.0、list/dict ≡ 同文本 str）；未知标量类型产出多等价变体并在探测侧
任一命中即保留 —— 假阳性方向冗余，绝不产生假阴性。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

#: 位图上限（128Kb —— 结构性内存界；超出即饱和降级）。
DEFAULT_MAX_BITS = 1 << 20
DEFAULT_FP_RATE = 0.01
#: 盈利阈值：预期节省字节须 ≥ 构建成本 × 该系数才启用。
PROFIT_THRESHOLD = 2.0
#: 最小约减比：低于此值（几乎不省）不启用。
MIN_REDUCTION_RATIO = 0.1


def canonical_variants(v: Any) -> List[bytes]:
    """join 键的全部等价 canonical 形态（M4，评审 R1）。

    与 ``federation._hashable_key`` 的**相等类**对齐：
    - list/dict → ``str(v)``（V5 中与同文本字符串**会**相等连接）；
    - int 与整值 float 同槽（1 ≡ 1.0）；
    - 未知标量类型（如 Decimal）：V5 字典相等可能跨类型命中
      （``hash(Decimal("1")) == hash(1)``）→ 保守产出多变体，
      ``contains`` 任一命中即保留（只多留，绝不漏）。
    """
    if v is None:
        return [b"n:"]
    if isinstance(v, bool):
        return [b"i:1" if v else b"i:0"]
    if isinstance(v, int):
        return [b"i:" + str(v).encode("ascii")]
    if isinstance(v, float):
        if v == int(v):
            return [b"i:" + str(int(v)).encode("ascii"), b"f:" + repr(v).encode("ascii")]
        return [b"f:" + repr(v).encode("ascii")]
    if isinstance(v, str):
        return [b"s:" + v.encode("utf-8", "surrogatepass")]
    if isinstance(v, (list, dict)):
        return [b"s:" + str(v).encode("utf-8", "replace"), b"o:" + str(v).encode("utf-8", "replace")]
    variants = [b"o:" + str(v).encode("utf-8", "replace")]
    try:
        if v == int(v):
            variants.append(b"i:" + str(int(v)).encode("ascii"))
    except (TypeError, ValueError, OverflowError):
        pass
    return variants


def _canonical_join_key(v: Any) -> bytes:
    """单一主形态（构建位图用；探测侧用 canonical_variants 全覆盖）。"""
    return canonical_variants(v)[0]


class BloomFilter:
    """有界确定性 Bloom（sha256 双哈希派生 k 位）。"""

    __slots__ = ("num_bits", "num_hashes", "_bits", "keys_added", "saturated")

    def __init__(self, num_bits: int, num_hashes: int):
        if num_bits <= 0 or num_hashes <= 0:
            raise ValueError("num_bits/num_hashes must be positive")
        self.num_bits = num_bits
        self.num_hashes = num_hashes
        self._bits = bytearray(num_bits // 8 + 1)
        self.keys_added = 0
        self.saturated = False

    @classmethod
    def with_capacity(
        cls,
        expected_keys: int,
        fp_rate: float = DEFAULT_FP_RATE,
        max_bits: int = DEFAULT_MAX_BITS,
    ) -> "BloomFilter":
        n = max(1, int(expected_keys))
        p = min(max(fp_rate, 1e-4), 0.5)
        ideal_m = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
        m = max(8, min(ideal_m, max_bits))
        k = max(1, round((m / n) * math.log(2)))
        return cls(num_bits=m, num_hashes=k)

    @property
    def bits(self) -> bytes:
        return bytes(self._bits)

    def add(self, key: Any) -> None:
        for variant in canonical_variants(key):
            self._set(variant)
        self.keys_added += 1
        # 经验饱和信号：键数超过按 1% fp 的理想容量 → 调用方降级。
        if not self.saturated:
            ideal = self.num_bits / (math.log(2) ** 2)
            if self.keys_added > ideal:
                self.saturated = True

    def __contains__(self, key: Any) -> bool:
        # 任一等价形态命中即保留（假阳性方向；绝不产生假阴性）
        return any(self._might_contain(variant) for variant in canonical_variants(key))

    def _positions(self, digest_key: bytes):
        h = hashlib.sha256(digest_key).digest()
        h1 = int.from_bytes(h[:8], "big")
        h2 = int.from_bytes(h[8:16], "big") | 1
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.num_bits

    def _set(self, digest_key: bytes) -> None:
        for pos in self._positions(digest_key):
            self._bits[pos >> 3] |= 1 << (pos & 7)

    def _might_contain(self, digest_key: bytes) -> bool:
        return all(
            self._bits[pos >> 3] & (1 << (pos & 7))
            for pos in self._positions(digest_key)
        )


def _extract_join_key(row: Dict[str, Any], field: str) -> Any:
    """链行取键（形状感知；与 federation._chain_row_key 同一语义的惰性引用）。"""
    from app.services.data_fabric.query.federation import _chain_row_key

    return _chain_row_key(row, field)


def build_bloom_from_rows(
    rows: List[Dict[str, Any]],
    field: str,
    *,
    expected_keys: Optional[int] = None,
    max_bits: int = DEFAULT_MAX_BITS,
) -> Tuple[Optional[BloomFilter], Dict[str, Any]]:
    """从累积左行构建键 Bloom。无任何有效键 → (None, stats)（诚实空）。"""
    keys = []
    for row in rows:
        k = _extract_join_key(row, field)
        if k is not None:
            keys.append(k)
    stats = {"keys_added": len(keys), "saturated": False}
    if not keys:
        return None, stats
    bloom = BloomFilter.with_capacity(
        expected_keys=expected_keys or len(keys), max_bits=max_bits
    )
    for k in keys:
        bloom.add(k)
    stats["saturated"] = bloom.saturated
    return bloom, stats


@dataclass(frozen=True)
class SemiJoinPlan:
    """一次半连接盈利决策（EXPLAIN 可复述）。"""

    enabled: bool
    reason: str
    estimated_reduction_ratio: float = 0.0
    expected_saved_rows: int = 0


def semi_join_plan(
    *,
    left_card: int,
    right_card: int,
    ndv_left: Optional[int],
    ndv_right: Optional[int],
    applicable: bool = True,
    bytes_per_row: int = 1800,
    build_cost_per_key: int = 32,
) -> SemiJoinPlan:
    """盈利阈值：预期约减字节 ≥ 构建成本 × PROFIT_THRESHOLD 才启用。

    - ``applicable=False``（空间/聚合跳）→ 不启用（键过滤不适用）；
    - 任何一侧 ndv 缺失 → 保守关闭（统计不足不改变传输语义）；
    - 空左侧 → 关闭（无键可构建）。
    """
    if not applicable:
        return SemiJoinPlan(
            enabled=False, reason="semi-join not applicable to this join kind"
        )
    if left_card <= 0:
        return SemiJoinPlan(
            enabled=False, reason="empty left side; nothing to build from"
        )
    if not ndv_left or not ndv_right:
        return SemiJoinPlan(
            enabled=False,
            reason="insufficient ndv statistics; conservative local semi-join only",
        )
    ratio = 1.0 - min(1.0, ndv_left / max(ndv_right, 1))
    if ratio < MIN_REDUCTION_RATIO:
        return SemiJoinPlan(
            enabled=False,
            reason=f"marginal reduction ({ratio:.2f} < {MIN_REDUCTION_RATIO})",
            estimated_reduction_ratio=ratio,
        )
    saved_rows = int(right_card * ratio)
    expected_saved = saved_rows * bytes_per_row
    build_cost = left_card * build_cost_per_key
    if expected_saved < build_cost * PROFIT_THRESHOLD:
        return SemiJoinPlan(
            enabled=False,
            reason=(
                f"expected saved bytes ({expected_saved}) < build cost "
                f"({build_cost}) x {PROFIT_THRESHOLD}"
            ),
            estimated_reduction_ratio=ratio,
            expected_saved_rows=saved_rows,
        )
    return SemiJoinPlan(
        enabled=True,
        reason=(
            f"bloom prefilter: ~{saved_rows}/{right_card} right rows skipped "
            f"(ratio {ratio:.2f})"
        ),
        estimated_reduction_ratio=ratio,
        expected_saved_rows=saved_rows,
    )


__all__ = [
    "BloomFilter",
    "SemiJoinPlan",
    "build_bloom_from_rows",
    "semi_join_plan",
    "DEFAULT_MAX_BITS",
    "DEFAULT_FP_RATE",
    "PROFIT_THRESHOLD",
    "MIN_REDUCTION_RATIO",
]
