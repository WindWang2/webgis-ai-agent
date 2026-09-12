"""AC-03 自适应符号化引擎（ADR-0152）单测：裁决优先级 / k 规则 / 上下文 /
离群值策略 / 纯函数确定性。

行为规格源：任务书 §0.4 全自动默认决策 + docs/dev/ac-03-symbology-recon.md。
"""
import pytest

from app.lib.cartography.symbology import (
    SymbologyConstraints,
    SymbologyDecision,
    SymbologyIntent,
    SymbologyProfile,
    apply_clip,
    resolve_symbology,
    symbology_decision_from_values,
)


HEAVY = [1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10, 13, 20, 40, 80, 160, 320, 640, 1280, 5000]
UNIFORM = [float(i) for i in range(10, 210, 2)]
MID_SKEW = [10, 12, 14, 11, 13, 15, 12, 14, 16, 13, 11, 15, 14, 12, 17, 16, 13, 18, 15, 14]


def _rejects(decision, kind):
    return [r for r in decision.rejected if r["kind"] == kind]


# ── 裁决优先级（§0.4）────────────────────────────────────────────────────────


def test_explicit_method_and_palette_respected():
    d = symbology_decision_from_values(
        HEAVY, requested_method="quantiles", requested_palette="Blues")
    assert d.method == "quantiles"
    assert d.palette == "Blues"
    assert d.source == "explicit"
    assert d.confidence == 1.0


def test_heavy_tail_beats_template_recommendation():
    d = symbology_decision_from_values(
        HEAVY, recommended_method="quantiles", recommended_palette="RdBu",
        recommended_k=6, origin="tmpl_x")
    assert d.method == "head_tail"
    assert d.source == "distribution"
    override = _rejects(d, "method")
    assert any(r["value"] == "quantiles" and "推翻" in r["reason"] for r in override)


def test_template_recommendation_wins_when_evidence_not_opposed():
    d = symbology_decision_from_values(
        MID_SKEW, recommended_method="quantiles", recommended_palette="RdBu",
        origin="tmpl_x")
    assert d.method == "quantiles"
    assert d.source == "recommended"
    assert d.palette == "RdBu"  # 审美偏好保留（族不同仅披露不降序）
    # 推荐色带只有 5 色 → 可分辨上限 5：recommended_k=6 被封顶到 5 并留痕
    d6 = symbology_decision_from_values(
        MID_SKEW, recommended_method="quantiles", recommended_k=6,
        recommended_palette="RdBu", origin="tmpl_x")
    assert d6.k == 5
    assert any("最多可分辨 5 级" in r["reason"] for r in _rejects(d6, "k"))


def test_uniform_no_pool_picks_equal_interval():
    # ADR-0073 文档承诺：近均匀 → equal_interval/quantiles（AC-03 修正空池路径）
    d = symbology_decision_from_values(UNIFORM)
    assert d.method == "equal_interval"


def test_low_evidence_fallback():
    d = symbology_decision_from_values([1.0, 2.0])
    assert d.method == "equal_interval"
    assert d.k == 3
    assert d.low_confidence is True
    assert d.source == "fallback"
    assert d.confidence == 0.35


def test_constant_field_is_low_confidence():
    d = symbology_decision_from_values([7.0] * 20)
    assert d.low_confidence is True
    assert d.k == 3


def test_mode_methods_pass_through_explicit():
    d = symbology_decision_from_values(MID_SKEW, requested_method="lisa")
    assert d.method == "lisa"
    assert d.source == "explicit"
    assert d.palette == ""  # lisa 语义色，不参与色带裁决
    d2 = symbology_decision_from_values(MID_SKEW, requested_method="categorical")
    assert d2.method == "categorical"
    assert d2.palette == "Set2"  # qualitative 族首选


# ── k 裁决（P2）──────────────────────────────────────────────────────────────


def test_k_clamped_to_bounds():
    d = symbology_decision_from_values(MID_SKEW, requested_k=10)
    assert d.k <= 7
    assert any(r["value"] == "10" for r in _rejects(d, "k"))
    d2 = symbology_decision_from_values(MID_SKEW, requested_k=1)
    assert d2.k == 3


def test_k_unique_value_cap():
    values = [1.0, 2.0, 3.0, 4.0] * 5  # 4 个唯一值
    d = symbology_decision_from_values(values, requested_k=7)
    assert d.k == 3  # max(3, n_unique-1)
    assert any("唯一值" in r["reason"] for r in _rejects(d, "k"))


def test_k_density_down_adjust():
    d = symbology_decision_from_values(MID_SKEW, feature_density=6.0)
    assert d.k == 4  # 基准 5 − 密度软上限 1
    d2 = symbology_decision_from_values(MID_SKEW, feature_density=20.0)
    assert d2.k == 3  # 5 − 2
    assert _rejects(d2, "k")


def test_k_separability_cap():
    # Blues 在 screen 下 k=5 中点采样 ΔE=10.8 < 12 → 色带可分辨上限 4 级。
    c = SymbologyConstraints(min_class_delta_e=12.0)
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW),
        SymbologyIntent(requested_palette="Blues"),
        c,
    )
    assert d.palette == "Blues"  # 偏好保留，k 受可分辨上限封顶
    assert d.k == 3  # Blues k=4 的前两档在 ΔE≥12 下不可分辨，上限实为 3
    assert any("可分辨" in r["reason"] for r in _rejects(d, "k"))


def test_small_n_forces_k3_even_with_explicit_k():
    d = symbology_decision_from_values([1.0, 2.0, 3.0], requested_k=7,
                                       requested_method="quantiles")
    assert d.k == 3
    assert any("k=3" in r["reason"] or r["value"] == "7" for r in _rejects(d, "k"))


# ── 色带上下文（P3）──────────────────────────────────────────────────────────


def test_cvd_context_swaps_unsafe_explicit_palette():
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW),
        SymbologyIntent(context="cvd_deuteranopia", requested_palette="RdYlGn"),
    )
    assert d.palette != "RdYlGn"
    # 降级必须留痕（09 线自愈动作清单）
    assert any(r["value"] == "RdYlGn" and "色盲安全" in r["reason"]
               for r in _rejects(d, "palette"))


def test_cvd_context_excludes_colorblind_unsafe_defaults():
    from app.lib.cartography.model_library import PALETTE_KINDS
    for ctx in ("cvd_deuteranopia", "cvd_protanopia"):
        d = resolve_symbology(
            SymbologyProfile(values=MID_SKEW), SymbologyIntent(context=ctx))
        meta = PALETTE_KINDS.get(d.palette)
        assert meta is None or meta.colorblind_safe, (ctx, d.palette)


def test_cvd_context_respects_template_palette_when_safe():
    # RdBu 是色盲安全发散带，CVD 上下文且证据不反对时应获尊重
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW),
        SymbologyIntent(context="cvd_deuteranopia", requested_palette="RdBu"),
    )
    assert d.palette == "RdBu"


def test_all_cvd_contexts_choose_separable_palette():
    for ctx in ("cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia"):
        d = resolve_symbology(
            SymbologyProfile(values=MID_SKEW), SymbologyIntent(context=ctx))
        from app.lib.cartography.palettes import (
            min_adjacent_delta_e, sample_ramp_colors, simulate_cvd)
        sim = [simulate_cvd(c, ctx) for c in sample_ramp_colors(d.palette, d.k)]
        assert min_adjacent_delta_e(sim) >= 10.0, ctx


def test_print_context_passes_grayscale_gate():
    from app.lib.cartography.palettes import (
        grayscale_ramp_separation, print_desaturate, sample_ramp_colors)
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW), SymbologyIntent(context="print"))
    sep = grayscale_ramp_separation(
        print_desaturate(sample_ramp_colors(d.palette, d.k)))
    assert sep >= 0.06


def test_print_context_rejects_low_gray_separation_palette():
    # Set2 灰度 ΔL=0.054 < 0.06：要么换带，要么 k 被压到灰度可分级档，
    # 但最终 (palette, k) 必须通过灰度门限——Set2 在任何 k 都不可分级，
    # 所以必须换带并留痕。
    from app.lib.cartography.palettes import (
        grayscale_ramp_separation, print_desaturate, sample_ramp_colors)
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW),
        SymbologyIntent(context="print", requested_palette="Set2"),
    )
    final = (d.palette, d.k)
    if final[0] == "Set2":
        sep = grayscale_ramp_separation(
            print_desaturate(sample_ramp_colors(*final)))
        assert sep >= 0.06, "Set2 留任必须以通过灰度门限为前提"
    else:
        assert _rejects(d, "palette")


def test_dark_basemap_prefers_perceptual():
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW, basemap_luminance=0.05))
    assert d.palette in ("Viridis", "Magma", "Inferno", "Plasma")


def test_diverging_data_kind_picks_diverging_family():
    d = resolve_symbology(
        SymbologyProfile(values=[-5, -3, -1, 0, 1, 3, 5, 2, -2, 4], data_kind="diverging"))
    assert d.palette in ("RdBu", "PuOr", "RdYlGn")


def test_cyclic_honestly_downgrades_with_disclosure():
    d = resolve_symbology(
        SymbologyProfile(values=MID_SKEW, data_kind="cyclic"))
    assert d.palette in ("Viridis", "Magma", "Inferno", "Plasma")
    assert any("cyclic" in r or "感知均匀" in r for r in d.reasons)


# ── 离群值与值域（P4）────────────────────────────────────────────────────────


def test_clip_p99_for_moderate_spike():
    values = [float(i % 10) + 1 for i in range(9990)] + [1000.0] * 10
    d = symbology_decision_from_values(values)
    assert d.clip_policy == "clip_p99"
    assert d.clip_high is not None
    assert d.n_clipped >= 1


def test_heavy_tail_clip_policy_head_tail():
    d = symbology_decision_from_values(HEAVY)
    assert d.clip_policy == "head_tail"


def test_log_for_extreme_span_all_positive():
    # 线性均匀铺满 5 个数量级（低偏度 + 跨 ≥4 个数量级）→ log 分级
    values = [1.0 + i * (100000.0 - 1.0) / 199 for i in range(200)]
    d = symbology_decision_from_values(values)
    assert d.clip_policy == "log"


def test_no_outlier_no_clip():
    d = symbology_decision_from_values(MID_SKEW)
    assert d.clip_policy == "none"
    assert d.n_clipped == 0


def test_explicit_clip_respected():
    d = symbology_decision_from_values(MID_SKEW, )
    d2 = resolve_symbology(
        SymbologyProfile(values=MID_SKEW),
        SymbologyIntent(requested_clip="clip_p99"),
    )
    assert d2.clip_policy == "clip_p99"
    assert d.clip_policy == "none"


def test_apply_clip_p99():
    values = [1.0] * 50 + [100.0]
    clipped, lo, high, n = apply_clip(values, "clip_p99")
    assert n == 1
    assert clipped[-1] == high
    assert all(v <= high for v in clipped)
    same, lo2, high2, n2 = apply_clip(values, "none")
    assert same == values and n2 == 0


# ── 工件形状与确定性 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("values", [HEAVY, UNIFORM, MID_SKEW, [1.0], [5.0] * 9])
def test_decision_shape_and_determinism(values):
    d1 = symbology_decision_from_values(values)
    d2 = symbology_decision_from_values(values)
    assert d1.model_dump() == d2.model_dump()
    assert {"method", "k", "palette", "clip_policy", "context", "reasons",
            "rejected", "confidence", "source"} <= set(d1.model_dump())
    for r in d1.rejected:
        assert set(r) == {"kind", "value", "reason"}
    assert 3 <= d1.k <= 7
    assert d1.method in ("quantiles", "equal_interval", "natural_breaks",
                         "std_dev", "head_tail", "categorical", "lisa")


def test_serialization_roundtrip():
    d = symbology_decision_from_values(HEAVY, origin="tmpl_gold")
    payload = d.model_dump()
    d2 = SymbologyDecision(**payload)
    assert d2 == d
