"""point_pattern 域 science oracle 显式消费测试（29 cases · 9 targets）。

数据集 ``data/point_pattern.json`` 由 ``scripts/gen_science_oracles.py`` 生成并
冻结；本文件只回放（沿用 ``run_case`` 的既有容差语义），不重算期望。

``test_oracle_replay.py`` 经 ``all_domains()`` glob 隐式覆盖本数据集 ——
数据集被删除/清空/target 面缩水时会**静默**退出覆盖面；本文件显式点名
``point_pattern.json``，上述回归在此处硬失败。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, load_domain, run_case
from tests.science_oracles._contract import check_dataset_contract

DOMAIN = "point_pattern"
CASES = load_domain(DOMAIN)

# 当前 corpus 覆盖的 target 面（显式契约：只许增、不许减）。
EXPECTED_TARGETS: frozenset[str] = frozenset({
    "app.lib.geo_analysis.point_pattern:cross_k",
    "app.lib.geo_analysis.point_pattern:cross_pair_correlation",
    "app.lib.geo_analysis.point_pattern:g_f_j_functions",
    "app.lib.geo_analysis.point_pattern:knox_test",
    "app.lib.geo_analysis.point_pattern:mantel_test",
    "app.lib.geo_analysis.point_pattern:pcf",
    "app.lib.geo_analysis.point_pattern:quadrat_test",
    "app.lib.geo_analysis.point_pattern:ripley_k",
    "app.lib.geo_analysis.point_pattern:space_time_k",})



def test_point_pattern_dataset_contract() -> None:
    ok, reason = check_dataset_contract(DOMAIN, CASES, EXPECTED_TARGETS)
    assert ok, reason


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{DOMAIN}.json::{c.case_id}" for c in CASES],
)
def test_point_pattern_oracle_replay(case: OracleCase) -> None:
    ok, reason = run_case(case)
    assert ok, f"[{DOMAIN}::{case.case_id}] target={case.target}: {reason}"
