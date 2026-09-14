"""Q022（qc-loop round 1）回归锁：median<=0（skew=None）时模板偏好不得崩溃。

历史缺陷：``_skew_ratio`` 对 median<=0 返回 None，而「分布证据不反对 →
偏好获尊重」分支直接 ``f"{skew:.0%}"`` 对 None 做百分号格式化 → TypeError。
resolve_symbology 的 7 个接线点对负值/非正中位数据（变化检测、异常面、
收支差值等 diverging 场景）一旦带模板推荐分类器就全体崩溃。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.symbology import symbology_decision_from_values

# n=10 ≥ small_n、唯一值>2、median=-0.75 ≤ 0 → _skew_ratio 返回 None
NEGATIVE_MEDIAN = [-5.0, -3.0, -2.0, -1.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0]


def test_none_skew_respects_template_preference_without_crash():
    decision = symbology_decision_from_values(
        NEGATIVE_MEDIAN, recommended_method="quantiles")
    assert decision.method == "quantiles"
    respected = [r for r in decision.reasons if "分布证据不反对" in r]
    assert respected, decision.reasons
    assert "n/a" in respected[0]


def test_positive_skew_reason_format_unchanged():
    # 常规右偏数据：理由串保持原有「skew=NN%」形态（锁定非 None 分支不动）
    decision = symbology_decision_from_values(
        # 温和右偏（同 test_template_preference_contract 的 MID_SKEW 形态，
        # 低于重尾阈值 → 走「偏好获尊重」分支）
        [10.0, 12.0, 14.0, 11.0, 13.0, 15.0, 12.0, 14.0, 16.0, 13.0,
         11.0, 15.0, 14.0, 12.0, 17.0, 16.0, 13.0, 18.0, 15.0, 14.0],
        recommended_method="quantiles")
    respected = [r for r in decision.reasons if "分布证据不反对" in r]
    assert respected, decision.reasons
    assert "%" in respected[0] and "n/a" not in respected[0]
