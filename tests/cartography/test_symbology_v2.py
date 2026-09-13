"""W2 符号化深化测试（V11，ADR-0162）。

覆盖：C1 v2 四扩展（bivariate / temporal_ramp / uncertainty / cost_hint）、
extrusion 高度/色彩双通道、像素密度单点、SymbologyDecision 只加不改契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography import symbology_v2 as s2
from app.lib.cartography.symbology import (
    BivariateSpec,
    symbology_decision_from_values,
)

VALUES = [float(v) for v in range(1, 41)]


# ── C1 只加不改契约 ──────────────────────────────────────────────────────

def test_decision_v10_shape_unchanged_without_extras() -> None:
    """不挂扩展时，决策序列化形状与 V10 一致（四个新键恒 None）。"""
    decision = symbology_decision_from_values(values=VALUES)
    dumped = decision.model_dump()
    assert dumped["bivariate"] is None
    assert dumped["temporal_ramp"] is None
    assert dumped["uncertainty"] is None
    assert dumped["cost_hint"] is None


def test_decision_accepts_optional_extras() -> None:
    decision = symbology_decision_from_values(values=VALUES)
    decision.bivariate = BivariateSpec(field_x="a", field_y="b")
    assert decision.model_dump()["bivariate"]["layout"] == "grid"


# ── W2.1 双变量 ──────────────────────────────────────────────────────────

def test_bivariate_spec_and_assign() -> None:
    spec = s2.resolve_bivariate(field_x="pop", field_y="income")
    assert spec.layout == "grid" and len(spec.colors) == 9
    assigns, payload = s2.bivariate_assign(
        spec,
        [{"id": str(i), "pop": i, "income": 100 - i} for i in range(1, 22)],
    )
    assert payload["matched"] == 21 and payload["skipped"] == 0
    assert len(assigns) == 21
    # 负相关：pop 增 → income 降，落格应铺满对角（x 增 y 减）
    first = assigns[0]
    last = assigns[-1]
    assert first["bin_x"] < last["bin_x"] and first["bin_y"] > last["bin_y"]


def test_bivariate_nan_honest_skip() -> None:
    spec = s2.resolve_bivariate(field_x="x", field_y="y")
    assigns, payload = s2.bivariate_assign(
        spec, [{"id": "1", "x": 1.0, "y": 2.0}, {"id": "2", "x": None, "y": 3.0}]
    )
    assert len(assigns) == 1 and payload["skipped"] == 1


def test_bivariate_dot_layout_and_fail_closed() -> None:
    dot = s2.resolve_bivariate(field_x="a", field_y="b", layout="dot")
    assert dot.layout == "dot"
    with pytest.raises(ValueError):
        s2.resolve_bivariate(field_x="a", field_y="b", layout="hex")
    with pytest.raises(ValueError):
        s2.resolve_bivariate(field_x="a", field_y="b", classes=5)
    with pytest.raises(ValueError):
        s2.resolve_bivariate(field_x="a", field_y="b", matrix="nonexistent")


# ── W2.2 时序色带 ────────────────────────────────────────────────────────

def test_temporal_ramp_deterministic_and_cross_map() -> None:
    a = s2.resolve_temporal_ramp(time_field="year", periods=["2019", "2021", "2020"])
    b = s2.resolve_temporal_ramp(time_field="year", periods=["2020", "2019", "2021"])
    assert a.ramp_id == b.ramp_id == "temporal-viridis-3"
    assert a.colors == b.colors
    assert a.periods == ["2019", "2020", "2021"]  # 升序去重
    assert a.legend_locked is True
    # 同 ramp_id 的两份 spec 跨图 legend 逐色一致（W2.2 验收核心）
    assert a.model_dump() == b.model_dump()


def test_temporal_ramp_fail_closed() -> None:
    with pytest.raises(ValueError):
        s2.resolve_temporal_ramp(time_field="year", periods=["2020"])
    with pytest.raises(ValueError):
        s2.resolve_temporal_ramp(time_field="year", periods=["a", "b"],
                                 base_palette="nonexistent")


# ── W2.3 不确定性 ────────────────────────────────────────────────────────

def test_uncertainty_modes_and_opacity_map() -> None:
    spec = s2.resolve_uncertainty(field="ci", mode="opacity")
    assert spec.disclosures
    values = [0.1, 0.5, 0.9]
    opacities = s2.uncertainty_opacity_map(spec, values)
    assert opacities[0] == pytest.approx(0.25)
    assert opacities[-1] == pytest.approx(1.0)
    # 单调（不确定度高 → 更透明 → opacity 数值映射单调）
    assert opacities == sorted(opacities)
    for mode in ("hatch", "band"):
        assert s2.resolve_uncertainty(field="ci", mode=mode).mode == mode
    with pytest.raises(ValueError):
        s2.resolve_uncertainty(field="ci", mode="glow")
    with pytest.raises(ValueError):
        s2.resolve_uncertainty(field="ci", min_opacity=0.9, max_opacity=0.1)


# ── W2.4 extrusion 双通道 ────────────────────────────────────────────────

def test_extrusion_dual_channel_independent() -> None:
    result = s2.extrusion_dual_channel(
        height_field="pop", heights=[10, 20, 30, 40, 50],
        color_field="income", color_values=[1, 2, 3, 4, 5],
    )
    assert result["height_channel"]["count"] == 5
    assert result["color_channel"] is not None
    assert result["redundant_encoding"] is False
    assert result["color_channel"]["k"] >= 1


def test_extrusion_redundant_encoding_disclosed() -> None:
    result = s2.extrusion_dual_channel(
        height_field="h", heights=[10, 20, 30], color_field="h",
        color_values=[10, 20, 30],
    )
    assert result["redundant_encoding"] is True
    assert result["disclosures"]


# ── W2.6 像素密度 ────────────────────────────────────────────────────────

def test_pixel_density_shared_signal() -> None:
    # 1280×720 = 921.6 千px²；5000 要素 ≈ 5.43/千px²（软硬上限之间的真实量纲）
    assert s2.compute_pixel_density(5000, 1280, 720) == pytest.approx(5.4253, abs=1e-3)
    assert s2.compute_pixel_density(1000, 1280, 720) == pytest.approx(1.0851, abs=1e-3)
    assert s2.compute_pixel_density(0, 1280, 720) == 0.0
    assert s2.compute_pixel_density(100, 0, 720) == 0.0


def test_density_feeds_k_adjudication() -> None:
    """共享信号进裁决：高密度 → k 下修（与 V10 密度语义衔接）。"""
    base = symbology_decision_from_values(
        values=VALUES, feature_density=s2.compute_pixel_density(5000, 1280, 720))
    calm = symbology_decision_from_values(
        values=VALUES, feature_density=s2.compute_pixel_density(200, 1920, 1080))
    assert base.k <= calm.k


# ── 成本提示 ─────────────────────────────────────────────────────────────

def test_cost_hint_deterministic() -> None:
    d1 = s2.attach_cost_hint(symbology_decision_from_values(values=VALUES))
    d2 = s2.attach_cost_hint(symbology_decision_from_values(values=VALUES))
    assert d1.cost_hint == d2.cost_hint
    assert d1.cost_hint.est_legend_rows == d1.k
