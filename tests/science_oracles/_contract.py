"""按域 oracle 数据集契约断言（test_<domain>_oracles.py 的共享实现）。

test_oracle_replay.py 经 ``all_domains()`` glob 隐式覆盖全部数据集：
单个 JSON 被删除或清空时覆盖面会**静默**缩水（只有 corpus 全空才报
RuntimeError）。各 ``test_<domain>_oracles.py`` 用本模块对自家数据集
做显式契约钉扎：存在、非空、case_id 唯一、expect.kind 合法、
``count`` 字段自洽、target 面不缩水、target 全部可解析（import 漂移
在此暴露，而不是等回放时以 "unexpected error" 的形式含混失败）。
"""
from __future__ import annotations

import json
from collections import Counter

from tests.science_oracles import DATA_DIR, OracleCase, resolve_target

_KNOWN_KINDS = frozenset({"exact", "allclose", "error"})


def check_dataset_contract(
    domain: str,
    cases: list[OracleCase],
    expected_targets: frozenset[str],
    known_duplicate_ids: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """核对单个数据集的显式契约；返回 (ok, 失败原因)，沿用 run_case 的惯例。

    ``known_duplicate_ids``：corpus 历史遗留的重复 case_id（生成器瑕疵，
    改 JSON 即改科学基准，故按现状显式钉扎）；出现钉扎清单之外的
    新重复才算回归。
    """
    try:
        _assert_contract(domain, cases, expected_targets, known_duplicate_ids)
    except AssertionError as exc:
        return False, str(exc)
    return True, ""


def _assert_contract(
    domain: str,
    cases: list[OracleCase],
    expected_targets: frozenset[str],
    known_duplicate_ids: frozenset[str],
) -> None:
    assert cases, f"data/{domain}.json 缺失或 cases 为空 —— oracle corpus 回归"

    counts = Counter(c.case_id for c in cases)
    dup = sorted(i for i, n in counts.items() if n > 1 and i not in known_duplicate_ids)
    unpinned = sorted(i for i in known_duplicate_ids if counts.get(i, 0) < 2)
    assert not dup, f"新出现 case_id 重复: {dup}"
    assert not unpinned, f"钉扎的重复 case_id 消失（corpus 被改？）: {unpinned}"

    bad_kinds = sorted(c.case_id for c in cases if c.kind not in _KNOWN_KINDS)
    assert not bad_kinds, f"未知 expect.kind: {bad_kinds}"

    targets = {c.target for c in cases}
    shrunk = expected_targets - targets
    assert not shrunk, f"target 面缩水（生成器漂移？）: {sorted(shrunk)}"

    unresolvable = sorted(t for t in targets if not _resolvable(t))
    assert not unresolvable, f"target 无法解析（API 改名/移除？）: {unresolvable}"

    raw = json.loads((DATA_DIR / f"{domain}.json").read_text())
    if "count" in raw:
        assert raw["count"] == len(cases), (
            f"count 字段 {raw['count']} != 实际 cases 数 {len(cases)}")


def _resolvable(target: str) -> bool:
    try:
        resolve_target(target)
    except Exception:  # noqa: BLE001 — 漂移检测就是要捕获一切解析失败
        return False
    return True
