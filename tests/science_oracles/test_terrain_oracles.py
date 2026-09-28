"""terrain 域 science oracle 显式消费测试（216 cases · 30 targets）。

数据集 ``data/terrain.json`` 由 ``scripts/gen_science_oracles.py`` 生成并
冻结；本文件只回放（沿用 ``run_case`` 的既有容差语义），不重算期望。

``test_oracle_replay.py`` 经 ``all_domains()`` glob 隐式覆盖本数据集 ——
数据集被删除/清空/target 面缩水时会**静默**退出覆盖面；本文件显式点名
``terrain.json``，上述回归在此处硬失败。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, load_domain, run_case
from tests.science_oracles._contract import check_dataset_contract

DOMAIN = "terrain"
CASES = load_domain(DOMAIN)

# 当前 corpus 覆盖的 target 面（显式契约：只许增、不许减）。
EXPECTED_TARGETS: frozenset[str] = frozenset({
    "app.lib.geo_analysis.terrain:_aligned",
    "app.lib.geo_analysis.terrain:_border_inside",
    "app.lib.geo_analysis.terrain:_guard_cells",
    "app.lib.geo_analysis.terrain:_neighbor_distances",
    "app.lib.geo_analysis.terrain:_slope_to_radians",
    "app.lib.geo_analysis.terrain:_specific_catchment_area",
    "app.lib.geo_analysis.terrain:_validate_azimuth_list",
    "app.lib.geo_analysis.terrain:_validate_cell_sizes",
    "app.lib.geo_analysis.terrain:_validate_horizon_radius",
    "app.lib.geo_analysis.terrain:_validate_window",
    "app.lib.geo_analysis.terrain:d8_flow",
    "app.lib.geo_analysis.terrain:dinf_flow_direction",
    "app.lib.geo_analysis.terrain:extract_contours",
    "app.lib.geo_analysis.terrain:extract_streams",
    "app.lib.geo_analysis.terrain:fill_depressions",
    "app.lib.geo_analysis.terrain:flow_length",
    "app.lib.geo_analysis.terrain:geomorphons",
    "app.lib.geo_analysis.terrain:hillshade_multiazimuth",
    "app.lib.geo_analysis.terrain:horizon_angle",
    "app.lib.geo_analysis.terrain:landform_classification",
    "app.lib.geo_analysis.terrain:ls_factor",
    "app.lib.geo_analysis.terrain:roughness",
    "app.lib.geo_analysis.terrain:sky_view_factor",
    "app.lib.geo_analysis.terrain:stream_power_index",
    "app.lib.geo_analysis.terrain:surface_curvature",
    "app.lib.geo_analysis.terrain:terrain_openness",
    "app.lib.geo_analysis.terrain:terrain_ruggedness_index",
    "app.lib.geo_analysis.terrain:topographic_position_index",
    "app.lib.geo_analysis.terrain:topographic_wetness_index",
    "app.lib.geo_analysis.terrain:viewshed",})



def test_terrain_dataset_contract() -> None:
    ok, reason = check_dataset_contract(DOMAIN, CASES, EXPECTED_TARGETS)
    assert ok, reason


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{DOMAIN}.json::{c.case_id}" for c in CASES],
)
def test_terrain_oracle_replay(case: OracleCase) -> None:
    ok, reason = run_case(case)
    assert ok, f"[{DOMAIN}::{case.case_id}] target={case.target}: {reason}"
