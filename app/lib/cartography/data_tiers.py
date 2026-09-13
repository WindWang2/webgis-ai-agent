"""Data Tiers — 特征数据量纲的单点策略（V11 W3.1，ADR-0163，缺口 G10）。

V10 残留三档互不相认的字面量：5000（inline 载体）/ 20000（扫描与
标注极端档）/ 50000（导出与数据通道封顶）。本模块把它们收敛为**单一
事实源**，并提供「要素数 × 几何复杂度 × 视口」的连续预算函数。

设计（诚实分层）：

- 三个常量是**既有校准锚点**（数值即行为，改动走 ADR）—— 转换点全部
  改为 import 本模块，grep 断言禁止业务代码再现同义字面量；
- :func:`adaptive_feature_budget` 是连续策略：在三档之间按视口面积与
  几何复杂度插值出**有效预算**（复杂几何降预算、大视口升预算），返回
  档位 + 封顶值 + 依据 —— 「聚合 → MVT → 抽稀 → 视口裁剪」的选择
  （W3.2）消费本函数；
- 前端镜像 `frontend/lib/data-tiers.ts`（常量 parity 由
  ``test_frontend_tier_mirror`` 源码扫描锁定）。

量纲纪律：全部以 **FeatureCount** 计（几何复杂度是无量纲乘子 ≥1.0，
1.0 = 简单点/面要素；线状稠密折线约 2–4；栅格化面板 ≈ 6）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 档位一：inline 载体上限（免二次请求；mapspec_source INLINE_FEATURE_LIMIT）。
TIER_INLINE_FEATURES = 5000

#: 档位二：扫描/修复诊断与标注的极端档。评审澄清：本档承载**三种消费
#: 语义**（dot_density 的点预算 / label_plan 的标注降档 /
#: data_quality 的内联评测帽）—— 共用的是「20000 = 全量可扫描上界」
#: 这一个量纲判断；若某语义需独立标定，再拆专用常量（走 ADR）。
TIER_SCAN_CAP_FEATURES = 20000

#: 档位三：导出与数据通道封顶（mapspec_to_svg / publication_export /
#: cost_model DATA_FABRIC_MAX_FEATURES）。
TIER_EXPORT_FEATURES = 50000

#: 档位词表（自适应选择的返回值）。
TIER_NAMES = ("inline", "scan", "export")


@dataclass(frozen=True)
class DataBudget:
    """一次数据决策的有效预算（可序列化语义）。"""

    tier: str                 # inline | scan | export
    max_features: int
    reason: str
    feature_count: int = 0
    geometry_complexity: float = 1.0

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "maxFeatures": self.max_features,
            "reason": self.reason,
            "featureCount": self.feature_count,
            "geometryComplexity": self.geometry_complexity,
        }


def effective_inline_budget(
    *,
    geometry_complexity: float = 1.0,
    viewport_px_w: Optional[int] = None,
    viewport_px_h: Optional[int] = None,
) -> int:
    """连续策略的 inline 有效预算（确定性；三档锚点间的插值面）。

    - 基准 = ``TIER_INLINE_FEATURES``；
    - 视口缩放：以 1280×720（= 921.6 千px²）为基准视口，面积每翻一倍
      预算 ×1.5（亚线性 —— 密度边际收益递减），封顶 2×基准；
    - 复杂度惩罚：预算 / max(1.0, complexity)，下限 1000；
    - 结果夹在 [1000, 2×TIER_INLINE] 内，同输入恒同输出。
    """
    base = float(TIER_INLINE_FEATURES)
    if viewport_px_w and viewport_px_h and viewport_px_w > 0 and viewport_px_h > 0:
        kpx2 = (viewport_px_w * viewport_px_h) / 1000.0
        base_kpx2 = 921.6  # 1280×720
        scale = 1.5 ** _log2(max(kpx2, 1.0) / base_kpx2)
        base *= min(max(scale, 0.5), 2.0)
    complexity = max(1.0, float(geometry_complexity))
    budget = base / complexity
    return int(max(1000, min(budget, 2.0 * TIER_INLINE_FEATURES)))


def _log2(x: float) -> float:
    import math

    return math.log2(x) if x > 0 else 0.0


def select_data_strategy(
    *,
    feature_count: int,
    geometry_complexity: float = 1.0,
    viewport_px_w: Optional[int] = None,
    viewport_px_h: Optional[int] = None,
) -> DataBudget:
    """数据策略选择（W3.2 的决策面；确定性）。

    阶梯（「聚合 → MVT → 抽稀 → 视口裁剪」的自动选择挂点）：

    - ``feature_count ≤ inline 有效预算`` → **inline**（免请求直出）；
    - ``≤ TIER_SCAN_CAP_FEATURES`` → **scan**（fetch 全量 + 修复诊断/标注
      分档消费）；
    - ``≤ TIER_EXPORT_FEATURES`` → **export**（服务端流水线：聚合/抽稀后
      下发，导出通道可用）；
    - 超出 → **export** 但预算收紧到几何复杂度惩罚后的导出半档
      （500k 级走聚合/MVT，见 W3.2 基线）。
    """
    complexity = max(1.0, float(geometry_complexity))
    inline_cap = effective_inline_budget(
        geometry_complexity=complexity,
        viewport_px_w=viewport_px_w,
        viewport_px_h=viewport_px_h,
    )
    if feature_count <= inline_cap:
        return DataBudget(
            tier="inline", max_features=inline_cap,
            reason=f"≤inline 有效预算（视口/复杂度插值 {inline_cap}）",
            feature_count=feature_count, geometry_complexity=complexity,
        )
    if feature_count <= TIER_SCAN_CAP_FEATURES:
        return DataBudget(
            tier="scan", max_features=TIER_SCAN_CAP_FEATURES,
            reason=f"≤scan 档 {TIER_SCAN_CAP_FEATURES}（fetch + 诊断/标注分档）",
            feature_count=feature_count, geometry_complexity=complexity,
        )
    if feature_count <= TIER_EXPORT_FEATURES:
        return DataBudget(
            tier="export", max_features=TIER_EXPORT_FEATURES,
            reason=f"≤export 档 {TIER_EXPORT_FEATURES}（服务端流水线）",
            feature_count=feature_count, geometry_complexity=complexity,
        )
    # 超导出档：复杂度惩罚收紧（稠密几何的可渲染要素更少）
    tightened = max(1000, int(TIER_EXPORT_FEATURES / complexity))
    return DataBudget(
        tier="export", max_features=min(tightened, TIER_EXPORT_FEATURES),
        reason=(f">export 档 {TIER_EXPORT_FEATURES} —— 聚合/MVT/抽稀流水线，"
                f"复杂度 {complexity:.1f} 下有效预算 {tightened}"),
        feature_count=feature_count, geometry_complexity=complexity,
    )


__all__ = [
    "TIER_INLINE_FEATURES",
    "TIER_SCAN_CAP_FEATURES",
    "TIER_EXPORT_FEATURES",
    "TIER_NAMES",
    "DataBudget",
    "effective_inline_budget",
    "select_data_strategy",
]
