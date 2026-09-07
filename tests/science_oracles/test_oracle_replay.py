"""Numerical Oracle Corpus 回放测试（Foundation V3 · Goal K）。

只回放 ``tests/science_oracles/data/*.json`` 的硬编码期望值 —— 不重算
期望、不触网、确定性。期望值由 ``scripts/gen_science_oracles.py``
在开发环境生成（生成脚本是期望值的"一次真相"，JSON 是冻结快照）。
"""
from __future__ import annotations

import pytest

from tests.science_oracles import OracleCase, all_domains, load_domain, run_case


def _cases() -> list:
    cases: list = []
    for domain in all_domains():
        cases.extend(load_domain(domain))
    return cases


CASES = _cases()

if not CASES:  # 数据目录为空时显式失败 —— corpus 是本任务的硬性交付物
    raise RuntimeError(
        "science oracle corpus is empty: run scripts/gen_science_oracles.py")


def _ids() -> list:
    return [f"{c.domain}::{c.case_id}" for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_oracle_case(case: OracleCase):
    ok, reason = run_case(case)
    if not ok:
        pytest.fail(f"[{case.domain}::{case.case_id}] target={case.target}: {reason}")
