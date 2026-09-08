"""SAR 域 P0/P1 修复 conformance 测试（审计 07-sar §8：R-1/R-2/R-3/R-4/R-6）。

- R-1（FN-1 · P0）：sar.vh_ratio 的「dB 域为 dB 差」虚假宣称删除——
  dB（负值）输入 → UnsupportedMethod 类型化锁定；descriptor/工具描述
  文本与实现同步（只宣称线性功率比值 + log-ratio 指路）；
- R-2（FN-2 · P0）：acquisition_comparability 死代码接线到
  sar_temporal_stats / sar_temporal_composite 工具（可选 acquisitions
  获取元数据 → 证据块 warnings，披露级不拒绝）；
- R-3（§6 · P1）：ENL 矩估计附 Wald 95% CI（delta 法
  var(ENL̂)≈2·ENL·(ENL+1)/n，均匀场景）——固定种子 4 视合成场景真值
  落入自报 CI；
- R-4（§6 · P1）：coherence 逐窗 Fisher z 95% CI（z=atanh γ、
  SE≈1/√(n_pairs−3)）——已知真相干覆盖频率 + 小样本宽 CI + n_pairs≤3
  → NaN；
- R-6（§7-R6 · P1）：thermal_noise_removal 补 ESA S1 IPF 引用
  （MPC-0392 / ESA-RS-CLI-52-0946）+ 可选 input_domain 显式量纲声明
  （auto 缺省行为逐位不变；全正 dB 场 + domain=db → 类型化拒绝）。
"""
import asyncio

import numpy as np
import pytest

from app.lib.gis.scientific_errors import UnsupportedMethod
from app.lib.geo_analysis.sar_calibration import remove_thermal_noise
from app.lib.geo_analysis.sar_temporal import (
    stack_comparability_warnings,
    vh_ratio,
)
from app.lib.geo_analysis.sar_v3 import (
    coherence_estimate,
    enl_confidence_interval,
    enl_map,
)

pytestmark = pytest.mark.unit


# ── R-1：vh_ratio dB 文案契约修正（P0）────────────────────────────────

def test_r1_vh_ratio_db_rejected_and_contract_text():
    # 线性功率路径保持精确（4/1=4、2/2=1）
    res = vh_ratio(np.array([[4.0, 2.0]]), np.array([[1.0, 2.0]]))
    np.testing.assert_allclose(res["array"], [[4.0, 1.0]], atol=1e-12)
    assert res["meta"]["formula"] == "vv / vh (linear power)"

    # dB（负值）输入 → UnsupportedMethod（类型化拒绝，锁定新契约）
    with pytest.raises(UnsupportedMethod, match="线性功率"):
        vh_ratio(np.array([[2.0, 4.0]]), np.array([[1.0, -0.5]]))
    # 全负 dB 场（历史「dB 差」宣称暗示支持的输入）同样拒绝
    with pytest.raises(UnsupportedMethod):
        vh_ratio(np.full((2, 2), -8.0), np.full((2, 2), -15.0))

    # descriptor / 工具描述与实现同步：不再宣称 dB 域路径，指路 log-ratio
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    desc = get_algorithm_registry().get("sar.vh_ratio")
    assert desc is not None
    assert not any("dB 差" in a for a in desc.assumptions), desc.assumptions
    assert any("log-ratio" in a for a in desc.assumptions)
    assert any("UnsupportedMethod" in a for a in desc.assumptions)

    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    schema = next(s["function"] for s in registry.get_schemas()
                  if s["function"]["name"] == "sar_vh_ratio")
    assert "dB 差" not in schema["description"]
    assert "log-ratio" in schema["description"]


# ── R-2：acquisition_comparability 接线（P0）─────────────────────────

def _acquisitions(n: int, *, orbit: str = "ascending",
                  angles=None) -> list:
    angles = angles or [32.0 + 0.1 * i for i in range(n)]
    return [
        {"polarization": "vv",
         "acquisition_date": f"2024-01-{i + 1:02d}",
         "incidence_angle_deg": float(angles[i]),
         "orbit_direction": orbit}
        for i in range(n)
    ]


def test_r2_acquisitions_comparability_wired_to_tools():
    # lib 助手：混轨 + 入射角差 >5° → 两条披露级警告（不抛异常）
    mixed = _acquisitions(4, orbit="ascending",
                          angles=[30.0, 30.2, 30.1, 40.0])
    mixed[1]["orbit_direction"] = "descending"
    warns = stack_comparability_warnings(mixed, 4)
    assert any("轨道方向混搭" in w for w in warns)
    assert any("入射角差" in w for w in warns)

    # 干净元数据（同轨、入射角差 ≤5°）→ 无警告；缺省 → 空列表
    assert stack_comparability_warnings(_acquisitions(4), 4) == []
    assert stack_comparability_warnings(None, 4) == []
    # 条数与切片数不一致 → 结构错误；非法极化词表 → ValueError
    with pytest.raises(ValueError, match="不一致"):
        stack_comparability_warnings(_acquisitions(3), 4)
    with pytest.raises(ValueError):
        stack_comparability_warnings(
            [{"polarization": "xx", "acquisition_date": "2024-01-01"}], 1)

    # 工具面接线：sar_temporal_stats / sar_temporal_composite 可选
    # acquisitions → 证据块 warnings + meta（披露级，不拒绝）
    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    stack = [[[1.0, 2.0], [3.0, 4.0]],
             [[2.0, 3.0], [4.0, 5.0]],
             [[0.5, 1.5], [2.5, 3.5]],
             [[1.5, 2.5], [3.5, 4.5]]]
    mixed4 = [dict(m, acquisition_date=f"2024-0{i + 1}-11")
              for i, m in enumerate(mixed)]

    stats_fn = registry._tools["sar_temporal_stats"]
    payload = asyncio.run(stats_fn(stack, "mean", acquisitions=mixed4))
    ev_warns = payload["scientific_evidence"]["warnings"]
    assert any("轨道方向混搭" in w for w in ev_warns)
    assert any("入射角差" in w for w in ev_warns)
    assert payload["meta"]["comparability_warnings"] == warns
    assert payload["success"] is True  # 披露级：结果照常产出

    comp_fn = registry._tools["sar_temporal_composite"]
    comp = asyncio.run(comp_fn(stack, "median", acquisitions=mixed4))
    assert any("轨道方向混搭" in w
               for w in comp["scientific_evidence"]["warnings"])
    assert comp["comparability_warnings"]

    # 缺省 acquisitions：行为与历史一致（无可比性警告）
    plain = asyncio.run(stats_fn(stack, "mean"))
    assert plain["meta"]["comparability_warnings"] == []
    assert plain["scientific_evidence"]["warnings"] == [
        plain["meta"]["disclosure"]]


# ── R-3：ENL 置信区间（P1）───────────────────────────────────────────

def test_r3_enl_confidence_interval_covers_truth():
    # 固定种子 4 视合成斑点场景（ENL 真值 = 4）
    rng = np.random.RandomState(2024)
    speckle = rng.gamma(shape=4.0, scale=25.0, size=(64, 64))
    res = enl_map(speckle, window=7)
    lo, hi = res["enl_ci95"]
    assert hi > lo > 0.0
    # 真值落入自报 CI（delta 法 var(ENL̂)≈2L(L+1)/n 的标称覆盖）
    assert lo < 4.0 < hi, (lo, hi, res["global_enl"])
    # 点估计在 CI 内；meta 披露方法与样本量
    assert lo <= res["global_enl"] <= hi
    assert res["meta"]["enl_ci_samples"] == 64 * 64
    assert "delta" in res["meta"]["enl_ci_method"]
    assert "enl_ci95" in res["meta"]

    # 手算一致：n 越大 CI 越窄；level 守卫
    lo9, hi9 = enl_confidence_interval(4.0, 100)
    lo4, hi4 = enl_confidence_interval(4.0, 10000)
    assert (hi9 - lo9) > (hi4 - lo4) > 0
    se = float(np.sqrt(2.0 * 4.0 * 5.0 / 100))
    assert hi9 == pytest.approx(4.0 + 1.959963984540054 * se, rel=1e-12)
    assert lo9 == pytest.approx(max(4.0 - 1.959963984540054 * se, 0.0),
                                rel=1e-12)
    with pytest.raises(ValueError, match="level"):
        enl_confidence_interval(4.0, 100, level=0.93)
    with pytest.raises(ValueError, match="正"):
        enl_confidence_interval(0.0, 100)


# ── R-4：coherence Fisher-z 置信区间（P1）────────────────────────────

def test_r4_coherence_fisher_z_ci_coverage_and_width():
    rng = np.random.RandomState(23)
    n = 64
    gamma_true = 0.5
    def _unit() -> np.ndarray:
        return (rng.standard_normal((n, n))
                + 1j * rng.standard_normal((n, n))) / np.sqrt(2.0)

    slc_a = _unit()
    noise = _unit()
    slc_b = gamma_true * slc_a + np.sqrt(1.0 - gamma_true ** 2) * noise

    res = coherence_estimate(slc_a, slc_b, window=5)
    lo = res["gamma_ci95_low"]
    hi = res["gamma_ci95_high"]
    gamma = res["gamma"]
    # 逐窗 CI 包含点估计；真值覆盖频率 ≥0.7（95% Fisher z 标称 ~0.93+）
    inner = (slice(2, n - 2), slice(2, n - 2))
    assert np.all(lo[inner] <= gamma[inner] + 1e-12)
    assert np.all(gamma[inner] <= hi[inner] + 1e-12)
    coverage = float(np.mean((lo[inner] <= gamma_true)
                             & (gamma_true <= hi[inner])))
    assert coverage >= 0.7, coverage

    # 小样本 → 宽 CI（窗口 3：n_pairs=9 vs 窗口 5：n_pairs=25）
    res_w3 = coherence_estimate(slc_a, slc_b, window=3)
    width5 = float(np.mean(hi[inner] - lo[inner]))
    i3 = (slice(1, n - 1), slice(1, n - 1))
    width3 = float(np.mean(res_w3["gamma_ci95_high"][i3]
                           - res_w3["gamma_ci95_low"][i3]))
    assert width3 > width5 > 0.0

    # 自相干 γ=1：CI 收敛到 [≈1, 1]（有限 z 上钳，无 inf 泄漏）
    res_self = coherence_estimate(slc_a, slc_a, window=5)
    assert np.all(res_self["gamma_ci95_high"] <= 1.0 + 1e-12)
    assert res_self["gamma_ci95_low"][n // 2, n // 2] == pytest.approx(
        1.0, abs=1e-4)
    # n_pairs ≤ 3 的窗口 → CI NaN（诚实不虚构）；meta 披露方法
    hole = np.ones((n, n), dtype=bool)
    hole[0:5, 0:5] = False                      # 5×5 nodata 洞
    a_hole = np.where(hole, slc_a, np.nan)
    b_hole = np.where(hole, slc_b, np.nan)
    res_hole = coherence_estimate(a_hole, b_hole, window=3)
    n_pairs = res_hole["valid_pairs"]
    ci_lo_h = res_hole["gamma_ci95_low"]
    assert np.all(np.isnan(ci_lo_h[n_pairs <= 3.0]))
    finite = np.isfinite(res_hole["gamma"]) & (n_pairs > 3.0)
    assert np.all(np.isfinite(ci_lo_h[finite]))
    assert "Fisher" in res_hole["meta"]["ci_method"]


# ── R-6：热噪声 ESA IPF 引用 + input_domain 显式量纲（P1）────────────

def test_r6_thermal_noise_esa_reference_and_input_domain():
    # 引用登记：真实题录（ESA S-1 MPC 技术注记 MPC-0392 / ESA-RS-CLI-52-0946）
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.method_references import get_method_reference

    ref = get_method_reference("esa_s1_ipf_denoising")
    assert ref is not None
    assert "MPC-0392" in ref.citation or "ESA-RS-CLI-52-0946" in ref.citation
    assert "Sentinel-1" in ref.citation
    desc = get_algorithm_registry().get("sar.thermal_noise_removal")
    assert "esa_s1_ipf_denoising" in desc.method_references

    db_field = np.full((2, 2), 5.0)     # 全正「dB 场」——符号启发式盲区

    # 缺省 auto：行为与历史逐位一致（meta 记录 input_domain）
    res_auto = remove_thermal_noise(db_field, noise_floor=1.0)
    assert res_auto["meta"]["input_domain"] == "auto"
    np.testing.assert_allclose(res_auto["array"], [[4.0, 4.0], [4.0, 4.0]],
                               atol=1e-15)
    res_lin = remove_thermal_noise(db_field, noise_floor=1.0,
                                   input_domain="linear")
    np.testing.assert_array_equal(res_auto["array"], res_lin["array"])

    # 显式 domain=db → 类型化拒绝（正确路由；全正 dB 场不再穿透）
    with pytest.raises(UnsupportedMethod, match="线性强度"):
        remove_thermal_noise(db_field, noise_floor=1.0, input_domain="db")
    # 声明 linear + 负值 → 仍拒绝（线性强度物理非负）
    with pytest.raises(UnsupportedMethod):
        remove_thermal_noise(np.array([[1.0, -2.0]]), noise_floor=0.5,
                             input_domain="linear")
    # 未知 domain → ValueError；工具 schema 含 input_domain
    with pytest.raises(ValueError, match="input_domain"):
        remove_thermal_noise(db_field, noise_floor=1.0, input_domain="power")

    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    schema = next(s["function"] for s in registry.get_schemas()
                  if s["function"]["name"] == "sar_remove_thermal_noise")
    assert "input_domain" in schema["parameters"]["properties"]
    thermal_fn = registry._tools["sar_remove_thermal_noise"]
    payload = asyncio.run(thermal_fn([[5.0, 1.0], [0.5, 2.0]],
                                     noise_floor=1.0))
    assert payload["input_domain"] == "auto"
    assert payload["success"] is True
    with pytest.raises(UnsupportedMethod):
        asyncio.run(thermal_fn([[5.0, 5.0], [5.0, 5.0]], noise_floor=1.0,
                               input_domain="db"))
