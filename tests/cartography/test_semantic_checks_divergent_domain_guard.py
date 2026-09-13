"""Q001（qc-loop round 1）回归锁：divergent 图例 center 校验不得因域非数值崩溃。

历史缺陷：legend_spec 为 {"type": "divergent", "center": 0}（无 min/max）时，
center_valid 右侧直接 ``float(mn)``/``float(mx)`` 抛 TypeError，炸掉整轮
``evaluate_cartography_semantics``。语义审查对畸形 LLM 产出的契约是
「出 fail 判定 + 证据」，绝不以异常收场；域非法时 valid 已为 False，
center 只在域合法时有意义。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.semantic_checks import _classification_integrity


def test_divergent_center_with_non_numeric_domain_is_fail_not_crash():
    status, evidence, message = _classification_integrity(
        {"type": "divergent", "center": 0})
    assert status == "fail"
    assert evidence["domain_valid"] is False
    assert evidence["center_valid"] is False
    assert message


def test_divergent_valid_domain_with_center_still_passes():
    status, evidence, _ = _classification_integrity({
        "type": "divergent", "min": -10, "max": 10, "center": 0,
        "palette_colors": ["#1d4ed8", "#ffffff", "#dc2626"],
    })
    assert status == "pass"
    assert evidence["center_valid"] is True
