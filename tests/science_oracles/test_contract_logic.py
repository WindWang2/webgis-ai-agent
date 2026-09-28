"""check_dataset_contract 自身的逻辑锁（钉扎/缩水/kind/可解析/count 分支）。

各 test_<domain>_oracles.py 只证明「当前 corpus 满足契约」；本文件用可
拼接的 case 集直接驱动契约函数的每个失败分支，防止守卫逻辑自身漂移。
基座选用 science_v4（无 count 字段）与 crs_units（有 count 字段），
避开对真实 JSON 的写操作。
"""
from __future__ import annotations

import dataclasses

from tests.science_oracles import OracleCase, load_domain
from tests.science_oracles._contract import check_dataset_contract

_BASE = load_domain("science_v4")  # 7 cases · 3 targets · 无 count 字段
_BASE_TARGETS = frozenset(c.target for c in _BASE)


def test_real_domain_passes_contract() -> None:
    ok, reason = check_dataset_contract("science_v4", _BASE, _BASE_TARGETS)
    assert ok, reason


def test_empty_cases_fails() -> None:
    ok, reason = check_dataset_contract("science_v4", [], _BASE_TARGETS)
    assert not ok
    assert "缺失或 cases 为空" in reason


def test_new_duplicate_id_fails() -> None:
    ok, reason = check_dataset_contract(
        "science_v4", _BASE + [_BASE[0]], _BASE_TARGETS)
    assert not ok
    assert _BASE[0].case_id in reason


def test_pinned_duplicate_exactly_two_passes() -> None:
    ok, reason = check_dataset_contract(
        "science_v4", _BASE + [_BASE[0]], _BASE_TARGETS,
        known_duplicate_ids=frozenset({_BASE[0].case_id}))
    assert ok, reason


def test_pinned_id_growing_to_three_fails() -> None:
    ok, reason = check_dataset_contract(
        "science_v4", _BASE + [_BASE[0], _BASE[0]], _BASE_TARGETS,
        known_duplicate_ids=frozenset({_BASE[0].case_id}))
    assert not ok
    assert "超出 2 份" in reason


def test_pinned_id_single_copy_fails() -> None:
    reduced = [c for c in _BASE if c.case_id != _BASE[0].case_id]
    ok, reason = check_dataset_contract(
        "science_v4", reduced, _BASE_TARGETS,
        known_duplicate_ids=frozenset({_BASE[0].case_id}))
    assert not ok
    assert "份数漂移或消失" in reason


def test_target_shrink_fails() -> None:
    ok, reason = check_dataset_contract(
        "science_v4", _BASE, _BASE_TARGETS | {"app.lib.geo_analysis.terrain:x"})
    assert not ok
    assert "target 面缩水" in reason


def test_unresolvable_target_fails() -> None:
    probe = dataclasses.replace(
        _BASE[0], case_id="unresolvable_probe",
        target="app.lib.geo_analysis.__no_such_module__:f")
    ok, reason = check_dataset_contract(
        "science_v4", _BASE + [probe], _BASE_TARGETS | {probe.target})
    assert not ok
    assert "无法解析" in reason


def test_unknown_kind_fails() -> None:
    probe = OracleCase(case_id="kind_probe", target=_BASE[0].target,
                       args=[], kwargs={},
                       expect={"kind": "fuzzy", "value": 0},
                       domain="science_v4")
    ok, reason = check_dataset_contract("science_v4", _BASE + [probe],
                                        _BASE_TARGETS)
    assert not ok
    assert "未知 expect.kind" in reason


def test_count_field_mismatch_fails() -> None:
    cu = load_domain("crs_units")  # count 字段 = 5
    reduced = [c for c in cu if c.case_id != cu[0].case_id]
    targets = frozenset(c.target for c in reduced)
    ok, reason = check_dataset_contract("crs_units", reduced, targets)
    assert not ok
    assert "count 字段" in reason
