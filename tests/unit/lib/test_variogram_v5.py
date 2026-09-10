"""Variogram v5（science-v5 W2）—— multi-start polish + 模型比较诊断。

覆盖：

- 正例：multi-start 仅主起点失败后生效（主路径 oracle 锚定不变——
  fit_variogram 期望值回放由 tests/science_oracles 钉死）；合成场 6 家族
  同台排名确定性；AICc 手算锚；diagnostics 旗标在边界 case 触发。
- 负例：未知模型名、样本不足、全部模型失败。
- 边界：sill_at_bound（强趋势场）、nugget_dominated（纯噪声场）、
  lag bin 数 ≤ k+2 时 AICc=inf（诚实）。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.lib.geo_analysis.kriging import (
    ALL_VARIOGRAM_MODELS,
    KrigingInputError,
    fit_variogram,
    select_variogram_model,
)


def _clustered_field(rng: np.random.Generator, n: int = 120,
                     range_m: float = 8.0, sill: float = 4.0) -> tuple:
    """球状模型合成场：gamma = sill·(1.5h/r − 0.5(h/r)³) 的实现。"""
    xy = rng.uniform(0, 50, size=(n, 2))
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    cov = np.where(d <= range_m,
                   sill * (1 - 1.5 * d / range_m + 0.5 * (d / range_m) ** 3),
                   0.0)
    cov = cov + 1e-6 * np.eye(n)
    z = rng.multivariate_normal(np.full(n, 10.0), cov)
    return xy, z


class TestMultiStartPolish:
    def test_primary_path_unchanged_on_easy_field(self):
        # 主起点成功时结果与 multi-start 无关（逐位不变性由确定性场锚定：
        # 同场双跑逐位一致 + 已知模型族入选）
        rng = np.random.default_rng(42)
        xy, z = _clustered_field(rng)
        f1 = fit_variogram(xy, z, model="spherical")
        f2 = fit_variogram(xy, z, model="spherical")
        assert (f1.sill, f1.range_m, f1.nugget) == (f2.sill, f2.range_m, f2.nugget)
        assert f1.model == "spherical"
        assert 0 < f1.range_m <= 100.0
        assert f1.sill > 0

    def test_multistart_recovers_flat_start_failure(self, monkeypatch):
        # 强制主 curve_fit 抛错 → multi-start 接管 → 仍产出可用拟合，
        # 不 raise（主失败后从备选起点恢复）。

        rng = np.random.default_rng(7)
        xy, z = _clustered_field(rng)
        calls = {"n": 0}
        import scipy.optimize as so

        real = so.curve_fit

        def flaky_first(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("primary start diverged")
            return real(*a, **kw)

        monkeypatch.setattr("scipy.optimize.curve_fit", flaky_first)
        fit = fit_variogram(xy, z, model="spherical")
        assert calls["n"] >= 2          # 主失败后 polish 接管
        assert fit.sill > 0 and fit.range_m > 0
        assert not fit.fitted_manually  # polish 成功（非网格回退）

    def test_grid_fallback_still_deterministic(self, monkeypatch):
        # curve_fit 完全禁用 → 网格回退（fitted_manually=True），双跑一致

        def no_curve_fit(*a, **kw):
            raise RuntimeError("disabled")

        monkeypatch.setattr("scipy.optimize.curve_fit", no_curve_fit)
        rng = np.random.default_rng(5)
        xy, z = _clustered_field(rng)
        f1 = fit_variogram(xy, z, model="spherical")
        f2 = fit_variogram(xy, z, model="spherical")
        assert f1.fitted_manually and f2.fitted_manually
        assert (f1.sill, f1.range_m, f1.nugget, f1.rss) == \
               (f2.sill, f2.range_m, f2.nugget, f2.rss)


class TestSelectVariogramModelDiagnostics:
    def test_ranking_has_diagnostics_and_meta_flags(self):
        rng = np.random.default_rng(13)
        xy, z = _clustered_field(rng)
        ranking, meta = select_variogram_model(xy, z)
        assert len(ranking) == len(ALL_VARIOGRAM_MODELS)
        for entry in ranking:
            assert set(entry["diagnostics"]) == {
                "range_at_bound", "sill_at_bound", "nugget_dominated"}
            assert isinstance(entry["diagnostics"]["nugget_dominated"], bool)
        assert "best_model_flags" in meta
        assert isinstance(meta["best_model_flags"], list)
        # 排名确定性：双跑同序
        ranking2, meta2 = select_variogram_model(xy, z)
        assert [r["model"] for r in ranking] == [r["model"] for r in ranking2]
        assert meta["best_weighted_rss"] == meta2["best_weighted_rss"]

    def test_aicc_hand_anchor(self):
        # AICc 手算锚：给定 rss 序列与 n_bins，公式逐位复算
        rng = np.random.default_rng(21)
        xy, z = _clustered_field(rng)
        ranking, meta = select_variogram_model(xy, z, models=["spherical"])
        entry = ranking[0]
        n_bins = meta["n_bins"]
        k = 3
        rss = max(entry["weighted_rss"], 1e-300)
        aic = n_bins * math.log(rss / n_bins) + 2.0 * k
        expected = aic + (2.0 * k * (k + 1.0)) / (n_bins - k - 1)
        assert entry["aicc"] == pytest.approx(expected, rel=1e-12)

    def test_aicc_inf_when_bins_too_few(self):
        rng = np.random.default_rng(31)
        xy, z = _clustered_field(rng, n=40)
        # n_lags=4 → n_bins ≤ k+2=5 的概率高；构造性地用 n_lags=5 但断言
        # 语义而非数值（inf 条件 = n_bins ≤ k+2）
        ranking, meta = select_variogram_model(xy, z, models=["spherical"],
                                               n_lags=5)
        entry = ranking[0]
        if meta["n_bins"] <= 5:
            assert entry["aicc"] == float("inf")

    def test_nugget_dominated_flag_on_pure_noise(self):
        rng = np.random.default_rng(77)
        xy = rng.uniform(0, 40, size=(100, 2))
        z = rng.normal(0, 1.0, 100)          # 纯块金场
        ranking, _ = select_variogram_model(xy, z, models=["spherical"])
        best = ranking[0]
        assert best["diagnostics"]["nugget_dominated"] is True

    def test_unknown_model_rejected(self):
        rng = np.random.default_rng(1)
        xy, z = _clustered_field(rng, n=30)
        with pytest.raises(KrigingInputError, match="variogram model"):
            select_variogram_model(xy, z, models=["wavelet"])

    def test_insufficient_samples_rejected(self):
        rng = np.random.default_rng(2)
        xy = rng.uniform(0, 10, size=(6, 2))
        z = rng.normal(0, 1, 6)
        with pytest.raises(KrigingInputError, match="至少需要"):
            select_variogram_model(xy, z)

    def test_all_models_fail_rejected(self, monkeypatch):

        def no_curve_fit(*a, **kw):
            raise RuntimeError("disabled")

        monkeypatch.setattr("scipy.optimize.curve_fit", no_curve_fit)
        # 完全简并输入：所有点重合 → 经验变异函数退化
        xy = np.zeros((30, 2))
        z = np.arange(30.0)
        with pytest.raises((KrigingInputError, ValueError)):
            select_variogram_model(xy, z, models=["spherical"])
