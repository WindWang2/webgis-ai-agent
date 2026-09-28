"""geostat 域 science oracle 显式消费测试（253 cases · 28 targets）。

数据集 ``data/geostat.json`` 由 ``scripts/gen_science_oracles.py`` 生成并
冻结；本文件只回放（沿用 ``run_case`` 的既有容差语义），不重算期望。

``test_oracle_replay.py`` 经 ``all_domains()`` glob 隐式覆盖本数据集 ——
数据集被删除/清空/target 面缩水时会**静默**退出覆盖面；本文件显式点名
``geostat.json``，上述回归在此处硬失败。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, load_domain, run_case
from tests.science_oracles._contract import check_dataset_contract

DOMAIN = "geostat"
CASES = load_domain(DOMAIN)

# 当前 corpus 覆盖的 target 面（显式契约：只许增、不许减）。
EXPECTED_TARGETS: frozenset[str] = frozenset({
    "app.lib.geo_analysis.interpolation:idw_loocv",
    "app.lib.geo_analysis.interpolation:idw_surface",
    "app.lib.geo_analysis.interpolation:nearest_neighbor_interpolation",
    "app.lib.geo_analysis.interpolation:nearest_neighbor_surface",
    "app.lib.geo_analysis.kriging:_gamma",
    "app.lib.geo_analysis.kriging:_matern_corr",
    "app.lib.geo_analysis.kriging:_validate_matern_smoothness",
    "app.lib.geo_analysis.kriging:_validate_solve_backend",
    "app.lib.geo_analysis.kriging:anisotropy_transform",
    "app.lib.geo_analysis.kriging:apply_anisotropy",
    "app.lib.geo_analysis.kriging:collocated_cokriging",
    "app.lib.geo_analysis.kriging:fit_variogram",
    "app.lib.geo_analysis.kriging:indicator_kriging",
    "app.lib.geo_analysis.kriging:kriging_interpolation",
    "app.lib.geo_analysis.kriging:ordinary_kriging",
    "app.lib.geo_analysis.rbf_interpolation:rbf_loocv",
    "app.lib.geo_analysis.rbf_interpolation:rbf_predict",
    "app.lib.geo_analysis.tin_interpolation:natural_neighbor_interpolation",
    "app.lib.geo_analysis.tin_interpolation:natural_neighbor_surface",
    "app.lib.geo_analysis.tin_interpolation:tin_loocv",
    "app.lib.geo_analysis.tin_interpolation:tin_predict",
    "app.lib.geo_analysis.tin_interpolation:tin_surface",
    "app.lib.geo_analysis.trend_surface:_check_order_feasible",
    "app.lib.geo_analysis.trend_surface:trend_fit_stats",
    "app.lib.geo_analysis.trend_surface:trend_loocv",
    "app.lib.geo_analysis.trend_surface:trend_predict",
    "app.lib.geo_analysis.trend_surface:trend_surface",
    "app.lib.geo_analysis.trend_surface:trend_terms",})



def test_geostat_dataset_contract() -> None:
    ok, reason = check_dataset_contract(DOMAIN, CASES, EXPECTED_TARGETS)
    assert ok, reason


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{DOMAIN}.json::{c.case_id}" for c in CASES],
)
def test_geostat_oracle_replay(case: OracleCase) -> None:
    ok, reason = run_case(case)
    assert ok, f"[{DOMAIN}::{case.case_id}] target={case.target}: {reason}"
