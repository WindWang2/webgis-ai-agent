"""network 域 science oracle 显式消费测试（177 cases · 15 targets）。

数据集 ``data/network.json`` 由 ``scripts/gen_science_oracles.py`` 生成并
冻结；本文件只回放（沿用 ``run_case`` 的既有容差语义），不重算期望。

``test_oracle_replay.py`` 经 ``all_domains()`` glob 隐式覆盖本数据集 ——
数据集被删除/清空/target 面缩水时会**静默**退出覆盖面；本文件显式点名
``network.json``，上述回归在此处硬失败。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, load_domain, run_case
from tests.science_oracles._contract import check_dataset_contract

DOMAIN = "network"
CASES = load_domain(DOMAIN)

# 当前 corpus 覆盖的 target 面（显式契约：只许增、不许减）。
EXPECTED_TARGETS: frozenset[str] = frozenset({
    "app.lib.geo_analysis.network:_speed_m_per_min",
    "app.services.network.accessibility:NetworkAccessibilityService._band_index",
    "app.services.network.accessibility:NetworkAccessibilityService._e2sfca_zone_weights",
    "app.services.network.allocation:_exact_combination_count",
    "app.services.network.allocation:_milp_scale_guard",
    "app.services.network.allocation:_validate_milp_inputs",
    "app.services.network.allocation:solve_p_center_milp",
    "app.services.network.allocation:solve_p_median_milp",
    "app.services.network.centrality:_parse_metrics",
    "app.services.network.graph_builder:haversine_distance",
    "app.services.network.graph_builder:linestring_length_m",
    "app.services.network.interaction:_check_param",
    "app.services.network.interaction:_top_contributions",
    "app.services.network.service_area:_break_to_cutoff",
    "app.services.network.service_area:_normalize_break_unit",})

# 生成器历史遗留的重复 case_id（不同输入共用 id；JSON 冻结不改，
# 按现状钉扎，新增重复才会失败）。
KNOWN_DUPLICATE_IDS: frozenset[str] = frozenset({
    "e2sfca_weights_z2_first",
    "e2sfca_weights_z4_first",
    "e2sfca_weights_z5_first",
    "normalize_break_unit_minutes",
    "parse_metrics_degree",
})


def test_network_dataset_contract() -> None:
    ok, reason = check_dataset_contract(
        DOMAIN, CASES, EXPECTED_TARGETS, KNOWN_DUPLICATE_IDS)
    assert ok, reason


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{DOMAIN}.json::{c.case_id}" for c in CASES],
)
def test_network_oracle_replay(case: OracleCase) -> None:
    ok, reason = run_case(case)
    assert ok, f"[{DOMAIN}::{case.case_id}] target={case.target}: {reason}"
