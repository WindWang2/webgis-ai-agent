"""science-v3 R1（审计 03 §8）：Emerging Hot Spot Analysis 测试。

descriptor = ``temporal.emerging_hotspot``；实现 =
``app/lib/geo_analysis/spatiotemporal_eha.py``（逐期 Gi* + 逐箱 MK +
ESRI 17+1 分类）；工具 = ``emerging_hotspot_analysis``。

fixture 几何（12 箱 1D 路径格网，band = 间距 = 100 m ⇒ W_i = {i−1, i, i+1}）：

- 单个 4 宽区块（bins 0..3，计数 h，其余 0）的 Gi* z 可手算：
  x̄ = h/3，s = h·√8/3/...（s² = 8h²/36·3? 见下）——h=6 时 x̄=2、
  s²=(4·16+8·4)/12=8，z(中心箱1, Σw=3) = (18−6)/(√8·√(27/11)) = **2.7085**，
  z(边缘箱0, Σw=2) = 8/(√8·√(20/11)) = **2.0976**，
  z(远端箱5..10) = −6/(√8·√(27/11)) = **−1.35425**；
  BH 后 q(z=2.7085) = 2(1−Φ(2.7085))·12/2 = **0.04062** < 0.05（唯二显著热点）。
- 单个 3 宽区块（bins 8..10）的中心箱邻域恰为高值集合 ⇒ z = √(n−1) =
  **√11 = 3.31662**（Gi* 解析上限锚点），q = 2(1−Φ(3.31662))·12 = 0.01096。
- MK 手算锚（tie 校正，z=(S∓1)/√Var，p=erfc(|z|/√2)）：
  z 序列 [b,a,a,a]：S=3、tie{a:3} ⇒ Var=(156−66)/18=5 ⇒ z_mk=3/√5−... =
  (3−1)/√5 = **0.894427**，p = **0.371098**（不显著 → consecutive 而非
  intensifying）；
  6 期 [b,b,b,b,b,a]：S=5、tie{b:5} ⇒ Var=(510−300)/18=35/3 ⇒
  z_mk = **1.171158**，p = **0.241564**（new）。

区块「跳位」构造（每期至多一个强区块——两个同高区块并存会把 z 压到
1.9 以下、BH 后不显著，这是本 fixture 设计的几何事实）覆盖
persistent / new / historical / sporadic / consecutive / no_pattern；
intensifying / diminishing / 冷点镜像由纯规则核 ``_classify_one`` 的
手布尔 fixture 覆盖。
"""
import asyncio
import math

import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidCRS,
)
from app.lib.geo_analysis.spatiotemporal_eha import (
    EHA_CATEGORY_CODES,
    _classify_one,
    emerging_hotspot_narrated,
)

N = 12
COORDS = [(50.0 + 100.0 * i, 0.0) for i in range(N)]
BAND = 100.0

A = 2.7085    # 4 宽区块中心箱 z（手算锚，四舍五入到 1e-4）
B = -1.35425  # 远端箱 z


def _block(spec):
    """spec: [(lo, hi, h), ...] 每期一个区块（含端点），其余 0。"""
    cols = []
    for lo, hi, h in spec:
        c = [0] * N
        for i in range(lo, hi + 1):
            c[i] = h
        cols.append(c)
    return np.array(cols, dtype=float).T


# ── Gi* 手算锚 + BH-FDR 显著性 ────────────────────────────────────────

def test_gistar_hand_anchor_and_fdr():
    counts = _block([(0, 3, 6), (0, 3, 6)])
    res = emerging_hotspot_narrated(counts, COORDS, distance_band=BAND)
    # 手算锚：z(中心) / z(边缘) / z(远端)。
    assert res["gi_star_z"][1][0] == pytest.approx(2.7085, abs=1e-3)
    assert res["gi_star_z"][0][0] == pytest.approx(2.0976, abs=1e-3)
    assert res["gi_star_z"][5][0] == pytest.approx(-1.35425, abs=1e-3)
    # BH-FDR：唯二并列最小 p → q = p·12/2 = 0.04062（显著）；边缘 z=2.0976
    # 的 p·12 ≈ 0.431（不显著）——FDR 校正真实参与热点判定。
    assert min(res["gi_star_q_fdr"][1]) == pytest.approx(0.04062, abs=5e-4)
    # bin0 的 p=2(1−Φ(2.0976))=0.03594 在 12 格中排第 3 → q = p·12/3 = 0.14376。
    assert res["gi_star_q_fdr"][0][0] == pytest.approx(0.14376, abs=5e-4)
    assert (res["gi_star_p"][1][0] * 12 / 2) == pytest.approx(
        res["gi_star_q_fdr"][1][0], rel=1e-3)

    # 解析上限锚：3 宽区块中心箱（邻域==高值集合）⇒ z = √(n−1) = √11。
    counts3 = _block([(0, 0, 0), (8, 10, 6)])  # 期 0 全 0（退化期）
    res3 = emerging_hotspot_narrated(counts3, COORDS, distance_band=BAND)
    assert res3["gi_star_z"][9][1] == pytest.approx(math.sqrt(11), abs=1e-3)
    assert res3["gi_star_q_fdr"][9][1] == pytest.approx(0.01096, abs=5e-4)
    # 期 0 零方差 → z 置 0 不参与显著性，且警告披露。
    assert res3["gi_star_z"][9][0] == 0.0
    assert res3["degenerate_periods"] == [0]
    assert any("零方差" in w for w in res3["warnings"])
    assert res3["categories"][9] == "new_hot_spot"


# ── 形态学分类 × 手算 MK 锚（真实计数管线）────────────────────────────

def test_categories_new_persistent_sporadic_consecutive_historical():
    coords = COORDS
    # persistent：同一区块 4 期不动 → bins 1,2 全期显著、z 恒定 → MK S=0。
    res = emerging_hotspot_narrated(
        _block([(0, 3, 6)] * 4), coords, distance_band=BAND)
    assert res["categories"][1] == res["categories"][2] == "persistent_hot_spot"
    assert res["category_codes"][1] == 4
    assert res["mk_trend"][1]["z"] == 0.0 and res["mk_trend"][1]["p_value"] == 1.0
    # 远端箱从始至终不显著 → no_pattern。
    assert res["categories"][5] == "no_pattern"

    # sporadic：R,L,L,R → bins 8,9 开-关-开（无冷史）。
    res = emerging_hotspot_narrated(
        _block([(7, 10, 6), (0, 3, 6), (0, 3, 6), (7, 10, 6)]),
        coords, distance_band=BAND)
    assert res["categories"][8] == res["categories"][9] == "sporadic_hot_spot"
    assert res["mk_trend"][8]["z"] == pytest.approx(0.0, abs=1e-6)
    # L 区块 bins 1,2：热 2/4 期、终期不热、先前热占比 2/3 < 90% → ESRI
    # 决策树落 no_pattern（historical 需要 ≥90%）。
    assert res["categories"][1] == "no_pattern"

    # consecutive：L,R,R,R → bins 8,9 末尾连续 3 期热、此前从未热；
    # MK（手算 S=3、tie{a:3}、Var=5）：z_mk=(3−1)/√5=0.894427、p=0.371098
    # 不显著 → consecutive 而非 intensifying。
    res = emerging_hotspot_narrated(
        _block([(0, 3, 6), (7, 10, 6), (7, 10, 6), (7, 10, 6)]),
        coords, distance_band=BAND)
    assert res["categories"][8] == res["categories"][9] == "consecutive_hot_spot"
    assert res["mk_trend"][8]["z"] == pytest.approx(0.894427, abs=1e-4)
    assert res["mk_trend"][8]["p_value"] == pytest.approx(0.371098, abs=1e-4)
    assert res["mk_trend"][8]["significant"] is False

    # new + historical（6 期）：L×5 后跳 R → bins 1,2 先前 5/5 期热、
    # 终期不热 → historical；bins 8,9 仅终期热（此前 z=B=-1.35425 温和
    # 不显著）→ new。MK 手算：S=±5、tie{b:5}、Var=35/3 ⇒ |z_mk|=1.171158、
    # p=0.241564（均不显著）。
    res = emerging_hotspot_narrated(
        _block([(0, 3, 6)] * 5 + [(7, 10, 6)]), coords, distance_band=BAND)
    assert res["categories"][1] == res["categories"][2] == "historical_hot_spot"
    assert res["categories"][8] == res["categories"][9] == "new_hot_spot"
    assert res["mk_trend"][1]["z"] == pytest.approx(-1.171158, abs=1e-4)
    assert res["mk_trend"][1]["p_value"] == pytest.approx(0.241564, abs=1e-4)
    assert res["mk_trend"][8]["z"] == pytest.approx(1.171158, abs=1e-4)


# ── 纯规则核：17+1 类全覆盖 + 互斥完备 ────────────────────────────────

def test_classification_rules_all_25_labels():
    T = lambda *b: np.array(b, dtype=bool)  # noqa: E731

    # 热点 8 类
    assert _classify_one(T(0, 0, 0, 1), T(0, 0, 0, 0), False, False) == "new_hot_spot"
    assert _classify_one(T(0, 0, 1, 1), T(0, 0, 0, 0), True, False) == "consecutive_hot_spot"
    assert _classify_one(T(0, 1, 1, 1), T(0, 0, 0, 0), False, False) == "consecutive_hot_spot"
    assert _classify_one(T(1, 1, 1, 1, 1, 1), T(0, 0, 0, 0, 0, 0), True, False) == "intensifying_hot_spot"
    assert _classify_one(T(1, 1, 1, 1, 1, 1), T(0, 0, 0, 0, 0, 0), False, False) == "persistent_hot_spot"
    assert _classify_one(T(1, 1, 1, 1, 1, 1), T(0, 0, 0, 0, 0, 0), False, True) == "diminishing_hot_spot"
    assert _classify_one(T(1, 0, 0, 1), T(0, 0, 0, 0), False, False) == "sporadic_hot_spot"
    assert _classify_one(T(1, 0, 0, 1), T(0, 1, 0, 0), False, False) == "oscillating_hot_spot"
    assert _classify_one(T(1, 1, 1, 1, 0), T(0, 0, 0, 0, 0), False, False) == "historical_hot_spot"
    assert _classify_one(T(1, 1, 1, 1, 0, 0), T(0, 0, 0, 0, 0, 0), False, False) == "no_pattern"  # 4/5 < 90%

    # 冷点 8 类（镜像；冷点「增强」= z 走低 ⇒ mk_dn 为 intensifying）
    assert _classify_one(T(0, 0, 0, 0), T(0, 0, 0, 1), False, False) == "new_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(0, 0, 1, 1), False, False) == "consecutive_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(0, 1, 1, 1), False, False) == "consecutive_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(1, 1, 1, 1, 1, 1), True, False) == "diminishing_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(1, 1, 1, 1, 1, 1), False, True) == "intensifying_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(1, 1, 1, 1, 1, 1), False, False) == "persistent_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(1, 0, 0, 1), False, False) == "sporadic_cold_spot"
    assert _classify_one(T(0, 1, 1, 0), T(1, 0, 0, 1), False, False) == "oscillating_cold_spot"
    assert _classify_one(T(0, 0, 0, 0), T(1, 1, 1, 1, 0), False, False) == "historical_cold_spot"

    # none：终期中性 + 先前无 90% 热/冷史
    assert _classify_one(T(0, 0, 0, 0), T(0, 0, 0, 0), False, False) == "no_pattern"
    assert _classify_one(T(1, 0, 0, 0), T(0, 0, 0, 0), False, False) == "no_pattern"


def test_classification_mutually_exclusive_and_complete():
    """随机布尔序列下：每箱恰一个标签、标签恒在 17+1 图例内（完备），
    且 17 个真实类别 + no_pattern 全部可达（互斥分支无一死代码）。"""
    rng = np.random.default_rng(42)
    labels = set()
    for _ in range(600):
        t = int(rng.integers(2, 9))
        hot = rng.random(t) < 0.5
        cold = (rng.random(t) < 0.5) & (~hot)
        label = _classify_one(hot, cold, bool(rng.integers(0, 2)),
                              bool(rng.integers(0, 2)))
        assert label in EHA_CATEGORY_CODES
        labels.add(label)
    assert {"new_hot_spot", "consecutive_hot_spot", "intensifying_hot_spot",
            "persistent_hot_spot", "diminishing_hot_spot", "sporadic_hot_spot",
            "oscillating_hot_spot", "historical_hot_spot", "new_cold_spot",
            "consecutive_cold_spot", "intensifying_cold_spot",
            "persistent_cold_spot", "diminishing_cold_spot",
            "sporadic_cold_spot", "oscillating_cold_spot",
            "historical_cold_spot", "no_pattern"} <= labels
    assert len(EHA_CATEGORY_CODES) == 17


# ── 随机数据：sporadic/none 占比合理 + 确定性 ─────────────────────────

def test_random_data_mostly_none_or_sporadic_and_deterministic():
    rng = np.random.default_rng(42)
    coords = [(float(rng.uniform(0, 1000)), float(rng.uniform(0, 1000)))
              for _ in range(12)]
    counts = rng.poisson(3.0, size=(12, 8)).astype(float)
    kwargs = dict(distance_band=350.0)
    res = emerging_hotspot_narrated(counts, coords, **kwargs)
    res2 = emerging_hotspot_narrated(counts, coords, **kwargs)
    # 确定性：无模拟/置换，同输入逐位一致。
    assert res == res2
    # 分类码互斥完备：逐箱恰一码、码值与图例一致。
    assert len(res["categories"]) == 12
    assert all(c in EHA_CATEGORY_CODES for c in res["categories"])
    assert sum(res["category_counts"].values()) == 12
    assert [EHA_CATEGORY_CODES[c] for c in res["categories"]] == res["category_codes"]
    # 随机泊松背景下，绝大多数箱应为「无形态/偶发」，不出现成规模的
    # intensifying/persistent（显著性 + 趋势同时伪阳性的随机期望极低）。
    benign = sum(res["category_counts"].get(k, 0)
                 for k in ("no_pattern", "sporadic_hot_spot", "sporadic_cold_spot",
                           "oscillating_hot_spot", "oscillating_cold_spot"))
    assert benign >= 6
    assert (res["category_counts"].get("intensifying_hot_spot", 0)
            + res["category_counts"].get("diminishing_hot_spot", 0)) == 0


# ── MK 不可得（n_periods < 4）的诚实退化 ─────────────────────────────

def test_two_periods_persistent_and_new_without_mk_trend():
    res = emerging_hotspot_narrated(
        _block([(0, 3, 6), (0, 3, 6)]), COORDS, distance_band=BAND)
    assert res["categories"][1] == "persistent_hot_spot"  # 形态学规则仍可分类
    assert res["n_trend_available"] == 0
    assert res["mk_trend"][0]["available"] is False
    assert "4" in res["mk_trend"][0]["reason"]
    assert any("MK" in w or "Mann-Kendall" in w for w in res["warnings"])

    # 2 期跳位：P1 L 区块、P2 R 区块 → historical + new。
    res2 = emerging_hotspot_narrated(
        _block([(0, 3, 6), (7, 10, 6)]), COORDS, distance_band=BAND)
    assert res2["categories"][1] == "historical_hot_spot"
    assert res2["categories"][8] == "new_hot_spot"


# ── 类型化守卫（审计口径：先拒绝，后计算）────────────────────────────

def test_typed_guards():
    good = _block([(0, 3, 6), (0, 3, 6), (0, 3, 6), (7, 10, 6)])
    with pytest.raises(ValueError):
        emerging_hotspot_narrated(good[:, :1], COORDS)          # 单期
    with pytest.raises(InsufficientSamples):
        emerging_hotspot_narrated(good[:2], COORDS[:2])         # <3 箱
    with pytest.raises(DegenerateData):
        emerging_hotspot_narrated(np.full((12, 4), 5.0), COORDS)  # 全常量
    with pytest.raises(ValueError):
        bad = good.copy()
        bad[3, 1] = np.nan
        emerging_hotspot_narrated(bad, COORDS)                  # NaN
    with pytest.raises(ValueError):
        neg = good.copy()
        neg[2, 0] = -1.0
        emerging_hotspot_narrated(neg, COORDS)                  # 负计数
    with pytest.raises(ValueError):
        emerging_hotspot_narrated(good, COORDS[:11])            # 形状不匹配
    with pytest.raises(DegenerateData):
        dup = [c if i != 3 else COORDS[2] for i, c in enumerate(COORDS)]
        emerging_hotspot_narrated(good, dup)                    # 重合质心
    with pytest.raises(InvalidCRS):
        emerging_hotspot_narrated(good, COORDS, crs="EPSG:4326")
    with pytest.raises(ValueError):
        emerging_hotspot_narrated(good, COORDS, alpha=0.0)
    with pytest.raises(ValueError):
        emerging_hotspot_narrated(good, COORDS, distance_band=-5.0)


# ── 工具层：薄包装 + 证据块（MK p → StatisticalSignificance）─────────

def _eha_geojson():
    counts = _block([(0, 3, 6), (7, 10, 6), (7, 10, 6), (7, 10, 6)])
    features = []
    for i in range(N):
        lo, la = 116.30 + i * 0.0008, 39.90
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lo, la]},
            "properties": {"bin_id": i, "counts": counts[i].tolist()},
        })
    return {"type": "FeatureCollection", "features": features}


def test_emerging_hotspot_tool_evidence_blocks():
    from app.tools.registry import ToolRegistry
    from app.tools.temporal_tools import register_temporal_tools

    registry = ToolRegistry()
    register_temporal_tools(registry)
    payload = asyncio.run(registry.dispatch("emerging_hotspot_analysis", {
        "dataset": _eha_geojson(),
        "counts_field": "counts",
        "distance_band_m": 100.0,
        "alpha": 0.05,
    }))
    assert payload.get("success") is True, payload.get("message")
    # 分类穿过工具边界（consecutive fixture：bins 8,9）。
    assert payload["categories"][8] == "consecutive_hot_spot"
    assert payload["category_codes"][8] == 2
    # 证据块：descriptor 元数据 + 两类 StatisticalSignificance
    #（逐期 Gi* 族 BH-FDR + 逐箱 MK 趋势）。
    ev = payload["scientific_evidence"]
    assert ev["algorithm"] == "temporal.emerging_hotspot"
    kinds = [u["uncertainty_type"] for u in ev["uncertainty"]]
    assert kinds.count("statistical_significance") == 2
    gi = next(u for u in ev["uncertainty"]
              if u.get("target") == "gi_star_periodic")
    assert gi["multiple_testing"] == "BH-FDR"
    mk = next(u for u in ev["uncertainty"]
              if u.get("target") == "mk_trend_per_bin")
    assert mk["statistic_name"].startswith("Mann-Kendall")
    assert 0.0 <= mk["p_value"] <= 1.0
    # 投影与参数披露。
    assert any("UTM" in w for w in ev["warnings"])
    assert ev["parameters_applied"]["counts_field"] == "counts"
    assert ev["parameters_applied"]["distance_band_m"] == 100.0
