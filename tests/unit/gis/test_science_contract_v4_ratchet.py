"""Scientific Contract V4 ratchet（science-v4 Wave 1）。

三个方向的机器可查约束：

1. **新增即受约束**：任何 heavy 算法（cpu/memory_cost == "high"）缺
   resource_envelope / cancellation_profile / tolerance 任一声明 → 红，
   除非在冻结 allowlist（science_contract_v4.UNDECLARED_HEAVY_ALLOWLIST）。
2. **洗白检测**：allowlist 成员必须真的 heavy 且真的缺声明 —— 补齐声明
   后不同步删成员 → 红（清单只许收缩，防反向膨胀）。
3. **ownership 硬门**：interpolation./terrain. 域（science-v4 ownership）
   的**全部**算法（不限 heavy）必须三声明齐全，且禁入 allowlist。
"""
from __future__ import annotations

import re

import pytest

from app.lib.gis.algorithms.science_contract_v4 import (
    OWNED_DOMAIN_PREFIXES,
    UNDECLARED_HEAVY_ALLOWLIST,
    is_heavy_cost,
    missing_v4_declarations,
)
from app.lib.gis.algorithm_registry import get_algorithm_registry


def _all_descriptors():
    registry = get_algorithm_registry()
    return [(aid, registry.get(aid)) for aid in registry.all_ids]


def test_new_heavy_algorithms_must_declare_v4_contract():
    """heavy 算法三声明齐全，或位于冻结 allowlist（新增即红）。"""
    offenders = []
    for aid, algo in _all_descriptors():
        if not is_heavy_cost(algo):
            continue
        missing = missing_v4_declarations(algo)
        if missing and aid not in UNDECLARED_HEAVY_ALLOWLIST:
            offenders.append(f"{aid}: missing {', '.join(missing)}")
    assert not offenders, (
        "heavy 算法缺 V4 科学契约声明（新增算法必须声明 resource_envelope/"
        "cancellation_profile/tolerance，或在 science_contract_v4 allowlist "
        f"记录基线）：\n" + "\n".join(offenders)
    )


def test_allowlist_members_are_still_undeclared_and_heavy():
    """洗白检测：成员必须仍 heavy 且仍缺声明 —— 补齐后必须同步删条目。"""
    stale = []
    known_ids = {aid for aid, _ in _all_descriptors()}
    for aid in sorted(UNDECLARED_HEAVY_ALLOWLIST):
        algo = get_algorithm_registry().get(aid)
        if algo is None:
            stale.append(f"{aid}: 算法已不存在（删除条目）")
            continue
        if not is_heavy_cost(algo):
            stale.append(f"{aid}: 已非 heavy（删除条目）")
        if not missing_v4_declarations(algo):
            stale.append(f"{aid}: 声明已齐全（从 allowlist 删除条目）")
    assert not stale, "allowlist 只许收缩：\n" + "\n".join(stale)


def test_owned_domains_fully_declared_and_never_allowlisted():
    """interpolation/terrain 全量三声明（不限 heavy）且禁入 allowlist。"""
    leaked = [
        aid for aid in UNDECLARED_HEAVY_ALLOWLIST
        if aid.startswith(OWNED_DOMAIN_PREFIXES)
    ]
    assert not leaked, f"ownership 域禁入 allowlist: {leaked}"
    offenders = []
    for aid, algo in _all_descriptors():
        if not aid.startswith(OWNED_DOMAIN_PREFIXES):
            continue
        missing = missing_v4_declarations(algo)
        if missing:
            offenders.append(f"{aid}: missing {', '.join(missing)}")
    assert not offenders, (
        "science-v4 ownership 域必须全量声明：\n" + "\n".join(offenders)
    )


def test_allowlist_is_sorted_frozen_baseline():
    """清单按源码书写序冻结（字典序）—— diff 可审计，杜绝无序追加。"""
    import inspect

    from app.lib.gis.algorithms import science_contract_v4

    src = inspect.getsource(science_contract_v4)
    written = re.findall(r'"([a-z_]+\.[a-z_.]+)",', src)
    assert written == sorted(written), "allowlist 源码条目必须按字典序书写"


@pytest.mark.parametrize(
    "aid", sorted(UNDECLARED_HEAVY_ALLOWLIST), ids=lambda x: x
)
def test_allowlist_entries_exist(aid: str):
    """逐条目存在性（参数化输出指明具体哪条腐烂）。"""
    assert get_algorithm_registry().get(aid) is not None
