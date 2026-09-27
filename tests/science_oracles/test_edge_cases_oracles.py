"""edge_cases 域 science oracle 显式消费测试（87 cases · 15 targets）。

数据集 ``data/edge_cases.json`` 由 ``scripts/gen_science_oracles.py`` 生成并
冻结；本文件只回放（沿用 ``run_case`` 的既有容差语义），不重算期望。

``test_oracle_replay.py`` 经 ``all_domains()`` glob 隐式覆盖本数据集 ——
数据集被删除/清空/target 面缩水时会**静默**退出覆盖面；本文件显式点名
``edge_cases.json``，上述回归在此处硬失败。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, load_domain, run_case
from tests.science_oracles._contract import check_dataset_contract

DOMAIN = "edge_cases"
CASES = load_domain(DOMAIN)

# 当前 corpus 覆盖的 target 面（显式契约：只许增、不许减）。
EXPECTED_TARGETS: frozenset[str] = frozenset({
    "app.lib.geo_analysis.interpolation:_estimate_h3_cells",
    "app.lib.geo_analysis.interpolation:_parse_point_values",
    "app.lib.geo_analysis.interpolation:_suggest_lower_resolutions",
    "app.lib.geo_analysis.interpolation:_validate_power",
    "app.lib.geo_analysis.interpolation:_validate_resolution",
    "app.lib.geo_analysis.interpolation:idw_surface",
    "app.lib.geo_analysis.interpolation:nearest_neighbor_interpolation",
    "app.lib.geo_analysis.interpolation:nearest_neighbor_surface",
    "app.lib.geo_analysis.kriging:kriging_interpolation",
    "app.lib.geo_analysis.kriging:ordinary_kriging",
    "app.lib.geo_analysis.spectral:compute_spectral_index",
    "app.lib.geo_analysis.trend_surface:trend_predict",
    "app.lib.gis.crs_safety:classify_crs",
    "app.lib.gis.crs_safety:crs_class_allows",
    "app.lib.gis.crs_safety:recommend_metric_crs",})

# 生成器历史遗留的重复 case_id（不同输入共用 id；JSON 冻结不改，
# 按现状钉扎，新增重复才会失败）。
KNOWN_DUPLICATE_IDS: frozenset[str] = frozenset({
    "classify_crs_none",
})


def test_edge_cases_dataset_contract() -> None:
    ok, reason = check_dataset_contract(
        DOMAIN, CASES, EXPECTED_TARGETS, KNOWN_DUPLICATE_IDS)
    assert ok, reason


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{DOMAIN}.json::{c.case_id}" for c in CASES],
)
def test_edge_cases_oracle_replay(case: OracleCase) -> None:
    ok, reason = run_case(case)
    assert ok, f"[{DOMAIN}::{case.case_id}] target={case.target}: {reason}"
