"""最终显示确认对账测试（F12 / ADR-0214 D6）。

锁定：期望面（编译期落定）vs 实际面（当前 MapSpec + render ACK）的
确定性机械对账；无期望面 = blocked。
"""
import pytest

from app.services.map_plan_compiler.compiler import DisplayExpectation
from app.services.map_plan_compiler.finalization import check_final_display


@pytest.mark.cartography
def test_all_expectations_met_is_confirmed():
    expectations = DisplayExpectation(
        layers={"pl-a": True, "pl-b": False},
        components=["comp-legend"],
    )
    current = {
        "layers": [
            {"id": "pl-a", "layout": {"visibility": "visible"}},
            {"id": "pl-b", "layout": {"visibility": "none"}},
        ],
        "layout": {"components": [{"id": "comp-legend", "enabled": True}]},
    }
    check = check_final_display(expectations, current, display_confirmed=True)
    assert check.status == "confirmed"
    assert check.failed_rows == []
    assert check.ack_ok is True


@pytest.mark.cartography
def test_visibility_mismatch_is_unconfirmed_with_reason():
    expectations = DisplayExpectation(layers={"pl-a": True})
    current = {"layers": [{"id": "pl-a", "layout": {"visibility": "none"}}]}
    check = check_final_display(expectations, current, display_confirmed=True)
    assert check.status == "unconfirmed"
    row = check.failed_rows[0]
    assert row.reason_code == "FINAL_LAYER_MISMATCH"
    assert row.expected == "visible" and row.actual == "hidden"


@pytest.mark.cartography
def test_missing_visible_layer_and_disabled_component():
    expectations = DisplayExpectation(layers={"pl-ghost": True},
                                      components=["comp-legend"])
    check = check_final_display(expectations, {"layers": [], "layout": {}},
                                display_confirmed=True)
    codes = {r.reason_code for r in check.failed_rows}
    assert codes == {"FINAL_LAYER_MISSING", "FINAL_COMPONENT_MISSING"}
    assert check.status == "unconfirmed"


@pytest.mark.cartography
def test_expected_hidden_but_absent_layer_is_ok():
    """期望隐藏 + 层根本不在场 ⇒ 满足（隐藏语义下缺省即不可见）。"""
    expectations = DisplayExpectation(layers={"pl-gone": False})
    check = check_final_display(expectations, {"layers": []}, display_confirmed=True)
    assert check.status == "confirmed"


@pytest.mark.cartography
def test_ack_pending_blocks_confirmation():
    expectations = DisplayExpectation(layers={"pl-a": True})
    current = {"layers": [{"id": "pl-a", "layout": {"visibility": "visible"}}]}
    check = check_final_display(expectations, current, display_confirmed=False,
                                ack_mode="required")
    assert check.status == "unconfirmed"
    assert check.ack_ok is False
    assert "FINAL_ACK_PENDING" in check.reason_codes


@pytest.mark.cartography
def test_empty_expectations_is_blocked_not_confirmed():
    check = check_final_display(DisplayExpectation(), {"layers": []},
                                display_confirmed=True)
    assert check.status == "blocked"
    assert check.reason_codes == ["FINAL_NO_EXPECTATIONS"]


@pytest.mark.cartography
def test_rows_deterministically_ordered():
    expectations = DisplayExpectation(
        layers={"pl-b": True, "pl-a": False},
        components=["comp-z", "comp-a"],
    )
    c1 = check_final_display(expectations, {"layers": [], "layout": {}},
                             display_confirmed=True)
    c2 = check_final_display(expectations, {"layers": [], "layout": {}},
                             display_confirmed=True)
    assert c1 == c2
    layer_targets = [r.target for r in c1.rows if r.kind == "layer"]
    assert layer_targets == sorted(layer_targets)
