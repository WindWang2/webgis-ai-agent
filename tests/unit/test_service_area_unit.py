"""Issue #693 item 7: service_area break_unit validation; seconds->minutes conversion."""

import pytest
from app.services.network.service_area import _break_to_cutoff, _normalize_break_unit


def test_break_unit_minutes_ok():
    assert _normalize_break_unit("minutes") == "minutes"
    assert _break_to_cutoff(5, "minutes", None) == 300.0  # 5 min -> 300 s


def test_break_unit_meters_ok():
    assert _normalize_break_unit("meters") == "meters"
    assert _break_to_cutoff(500, "meters", None) == 500.0


def test_break_unit_seconds_converts():
    # seconds break: with default seconds graph, passthrough; with minutes impedance, divide
    assert _normalize_break_unit("seconds") == "seconds"
    assert _break_to_cutoff(300, "seconds", None) == 300.0


def test_break_unit_invalid_raises():
    with pytest.raises(ValueError, match="Unsupported break_unit"):
        _normalize_break_unit("hours")
    with pytest.raises(ValueError):
        _break_to_cutoff(5, "hours", None)


def test_break_unit_seconds_vs_time_weight():
    from app.services.network.models import Impedance
    # seconds break on minutes-weighted graph: 120s -> 2 min
    assert _break_to_cutoff(120, "seconds", Impedance(name="x", unit="minutes")) == 2.0
