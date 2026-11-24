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


def test_non_list_palette_is_fail_not_crash():
    """palette_colors 非序列（如 int）时 len() 不得炸掉整轮评审。"""
    status, evidence, _ = _classification_integrity(
        {"type": "continuous", "min": 0, "max": 1, "palette_colors": 5})
    assert status == "fail"
    assert evidence["colors_valid"] is False


def test_graduated_breaks_mapping_does_not_abort_review():
    """breaks 为 dict 时图例/样式等价检查不得因切片 TypeError 中断。"""
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    spec = {
        "version": "1.0",
        "sources": {"s1": {"type": "geojson"}},
        "layers": [{
            "id": "l1", "source": "s1", "type": "fill",
            "legend_spec": {
                "type": "graduated", "field": "v",
                "breaks": {"a": 1, "b": 2, "c": 3},
                "palette_colors": ["#111111", "#222222"],
            },
            "paint": {"fill-color": {
                "method": "step", "field": "v", "default": "#222222",
                "stops": [[1, "#111111"]],
            }},
        }],
    }
    report = evaluate_cartography_semantics(spec)
    assert report is not None


def test_legend_completeness_tolerates_non_dict_layout():
    from app.lib.cartography.semantic_checks import (
        CartographyReport,
        _check_map_legend_completeness,
    )

    report = CartographyReport()
    layers = [{
        "id": "l1", "visible": True,
        "legend_spec": {"type": "graduated", "field": "v",
                        "breaks": [1, 2, 3],
                        "palette_colors": ["#111111", "#222222"]},
    }]
    _check_map_legend_completeness(report, {"layout": ["bad"]}, layers)
    assert any(c.rule == "carto.legend.completeness" for c in report.checks)


def test_paint_field_checks_tolerate_non_dict_paint():
    from app.lib.cartography.semantic_checks import (
        CartographyReport,
        _check_layer_paint_fields,
    )

    report = CartographyReport()
    _check_layer_paint_fields(
        report, {"id": "l1", "paint": ["match", ["get", "k"], "#111111"]},
        "l1", "s1", None, set(),
    )
    assert report.checks == []


def test_warning_count_counts_deterministic_only():
    from app.lib.cartography.semantic_checks import CartographyReport

    report = CartographyReport()
    report.add_check("DET_WARNING", "warning", "deterministic warning")
    report.add_check(
        "DESIRED_WARNING", "warning", "desired-state warning",
        evidence_class="desired_state",
    )
    assert report.to_dict()["warning_count"] == 1
