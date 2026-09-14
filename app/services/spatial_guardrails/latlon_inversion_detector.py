"""经纬度倒置检测与置信度自适应纠偏（ADR-0195 D4）。

硬信号（值域矛盾）→ 置信度 1.0 直接判倒置；软信号（两种解释的海陆
评分对比）→ 按证据强度在 [0.60, 0.95] 插值，低于阈值仅告警不翻转。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class InversionReport:
    is_inverted: bool
    confidence: float
    corrected: tuple[float, float] | None
    evidence: dict = field(default_factory=dict)


def _interpretation_score(
    lng: float, lat: float, *, ocean_confirm_km: float
) -> float:
    """对一种 (lng, lat) 解释给地理合理性分：land=1.0 / coastal=0.5 /
    water_body=0.35 / ocean=0.0；|lat|>85 极地惩罚 ×0.5；值域非法 → 0。"""
    if not (math.isfinite(lng) and math.isfinite(lat)):
        return 0.0
    if abs(lat) > 90.0 or abs(lng) > 180.0:
        return 0.0
    # 延迟导入避免模块加载环（landmask_validator → data 资产）
    from app.services.spatial_guardrails.landmask_validator import classify_point

    zone = classify_point(lng, lat, ocean_confirm_km=ocean_confirm_km)
    if zone == "LAND":
        score = 1.0
    elif zone == "COASTAL_BAND":
        score = 0.5
    elif zone == "WATER_BODY":
        score = 0.35
    else:  # OCEAN_CONFIDENT
        score = 0.0
    if abs(lat) > 85.0:
        score *= 0.5
    return score


def detect_pair(
    pair,
    *,
    context: str | None = None,
    ocean_confirm_km: float = 150.0,
) -> InversionReport:
    """判定二元坐标对 [a, b] 是否发生 X/Y 轴倒置。

    context: 调用方语境提示（"lat_first" / "lng_first" / None），
    仅在证据平局时作为 tie-breaker，绝不覆盖硬证据。
    """
    del context  # v1：语境词表预留，证据优先
    a, b = float(pair[0]), float(pair[1])
    # 解释 A：输入本序 (lng=a, lat=b)；解释 B：翻转序 (lng=b, lat=a)
    a_ok = abs(a) <= 180.0 and abs(b) <= 90.0  # A 数值合法
    b_ok = abs(b) <= 180.0 and abs(a) <= 90.0  # B 数值合法
    evidence: dict = {
        "input": [a, b],
        "as_given": [a, b],
        "flipped": [b, a],
    }

    if not a_ok and b_ok:
        # 硬信号：本序值域矛盾（如 [39.9, 116.4]，b>90 不可能是纬度）→ 判倒置。
        evidence.update(hard_signal=True, as_given_zone=None, flipped_zone=None)
        return InversionReport(True, 1.0, (b, a), evidence)
    if a_ok and not b_ok:
        # 硬信号：本序合法而翻转序非法（如 [116.4, 39.9]）→ 未倒置。
        evidence.update(hard_signal=True, as_given_zone=None, flipped_zone=None)
        return InversionReport(False, 1.0, None, evidence)
    if not a_ok and not b_ok:
        # 双解释值域非法（如 [200, 39.9] / [120, 130]）→ 倒置检测不表态，
        # 交给 L1_FORMAT_CRS 值域校验拦截。
        evidence.update(hard_signal=True, as_given_zone=None, flipped_zone=None)
        return InversionReport(False, 0.0, None, evidence)

    # 软信号：两种解释的海陆评分对比。
    from app.services.spatial_guardrails.landmask_validator import classify_point

    score_a = _interpretation_score(a, b, ocean_confirm_km=ocean_confirm_km)
    score_b = _interpretation_score(b, a, ocean_confirm_km=ocean_confirm_km)
    evidence.update(
        hard_signal=False,
        as_given_zone=classify_point(a, b, ocean_confirm_km=ocean_confirm_km).value,
        flipped_zone=classify_point(b, a, ocean_confirm_km=ocean_confirm_km).value,
        as_given_score=round(score_a, 3),
        flipped_score=round(score_b, 3),
    )
    if score_a <= 0.5 and (score_b - score_a) >= 0.4:
        confidence = 0.60 + 0.35 * min(1.0, score_b - score_a)
        return InversionReport(True, confidence, (b, a), evidence)
    return InversionReport(False, 0.0, None, evidence)


def auto_flip(
    pair,
    *,
    min_confidence: float = 0.60,
    ocean_confirm_km: float = 150.0,
) -> tuple[list[float], InversionReport]:
    """检测 + 修复组合：置信度达标返回 [lng, lat] 修正值，否则原样返回。"""
    report = detect_pair(pair, ocean_confirm_km=ocean_confirm_km)
    if report.is_inverted and report.confidence >= min_confidence:
        return [report.corrected[0], report.corrected[1]], report
    return [float(pair[0]), float(pair[1])], report
