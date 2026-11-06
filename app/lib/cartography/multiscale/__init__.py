"""多尺度制图综合与尺度表达（GOAL 04）—— 非破坏性表达策略层。

包结构：
- ``profile``：ScaleRepresentationProfile.v1 纯数据契约 + 双基准比例尺换算
  + MapSpec 落笔投影（M1）；
- ``importance``：语义重要性规则引擎（M2）；
- ``generalize``：拓扑安全几何综合引擎（M3）；
- ``disclosure``：显示/原始统计口径披露模型（M4/M5）。

纪律（DECISIONS.md）：本包只产**派生视图**（渲染副本/ref），原始分析资产
与 source revision 永不被修改；表达裁决复用 v1.6 representation 协议
（app/lib/cartography/representation.py），不建第二表达协议。
"""
from app.lib.cartography.multiscale.profile import (
    SCALE_REPRESENTATION_PROFILE_VERSION,
    ScaleRepresentationProfile,
    band_at_zoom,
    display_meters_per_pixel,
    display_scale_denominator,
    profile_fingerprint,
    validate_profile,
)

__all__ = [
    "SCALE_REPRESENTATION_PROFILE_VERSION",
    "ScaleRepresentationProfile",
    "band_at_zoom",
    "display_meters_per_pixel",
    "display_scale_denominator",
    "profile_fingerprint",
    "validate_profile",
]
