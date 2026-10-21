"""全库收集契约（#1702 / #1707 防复发）。

两条已修缺陷的共同形状是「收集阶段静熔断」，只在真实全库
``pytest --collect-only`` 下现形，故用子进程跑一次真实收集并断言：

- #1702：``tests/unit/spatial_events/test_contracts.py`` 与
  ``tests/unit/gis_harness/interaction_ir/test_contracts.py`` 曾因两目录
  非包、模块名同为裸 ``test_contracts``，在默认 prepend 导入模式下撞车，
  收集到第二个即 ``import file mismatch`` 并中断整个 session（HEAD 零
  测试可跑）。修复 = 两目录补 ``__init__.py`` 成为包。本契约断言全库
  收集退出码为 0 且无 ERROR 行 —— 同类撞车在此现形。
- #1707：39 个 ``p4_test_*.py`` 不匹配默认 ``python_files``，目录收集
  永远看不到（显式传路径才执行，缺口彻底隐形）。修复 = 机械重命名为
  ``test_p4_*.py``。本契约断言 ``tests/`` 下无 ``p4_test_*.py`` 残留，
  且收集中 ``test_p4_`` 用例数不低于基线。

契约故意跑全库（约 30s）：只查单个目录防不住其它同名撞车或配置漂移。
本文件栖身 spatial_events/ —— #1702 撞车的两个目录之一，就近守护。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_ROOT = _REPO_ROOT / "tests"
# issue/任务规定的计数下限；实际基线 26k+，留足并行修复员增删用例的余量，
# 但任何 testpaths/配置漂移导致的大面积消失都会击穿。
_MIN_COLLECTED = 2500
# 39 个重命名文件首次实跑共 208 条收集项（192 个 test 函数 + 参数化展开）；
# 留约 4% 余量防参数化微调，但整批再次隐形必被击穿。
_MIN_P4_ITEMS = 200
_COLLECT_TIMEOUT_S = 600


@pytest.fixture(scope="module")
def collection() -> subprocess.CompletedProcess[str]:
    """子进程执行全库 ``--collect-only``（隔离嵌套 pytest 的插件与随机种子副作用）。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "--no-cov",
            "-p",
            "no:randomly",
            "tests/",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_COLLECT_TIMEOUT_S,
    )


def test_full_collection_is_clean(collection: subprocess.CompletedProcess[str]) -> None:
    """#1702：全库收集零 error —— 同名 test_*.py 撞车或配置漂移在此现形。"""
    assert collection.returncode == 0, (
        f"pytest --collect-only 退出码 {collection.returncode}（0=收集全部成功）\n"
        f"stdout 尾部:\n{collection.stdout[-2000:]}"
    )
    error_lines = [line for line in collection.stdout.splitlines() if line.startswith("ERROR")]
    assert not error_lines, f"收集阶段存在 error:\n{error_lines}"
    assert "Interrupted" not in collection.stdout, "收集被中断（同名模块撞车的典型症状）"


def test_collected_count_above_baseline(collection: subprocess.CompletedProcess[str]) -> None:
    """收集用例总数 > 2500：防 testpaths/配置漂移导致大面积静默消失。"""
    totals = re.findall(r"(\d+) tests? collected", collection.stdout)
    assert totals, f"未解析到收集计数，stdout 尾部:\n{collection.stdout[-1000:]}"
    collected = int(totals[-1])
    assert collected > _MIN_COLLECTED, f"仅收集到 {collected} 条用例（下限 {_MIN_COLLECTED}）"


def test_no_p4_test_files_remain() -> None:
    """#1707：tests/ 下不得残留不匹配默认 python_files 的 p4_test_*.py。"""
    residual = sorted(str(path.relative_to(_REPO_ROOT)) for path in _TESTS_ROOT.glob("**/p4_test_*.py"))
    assert not residual, f"#1707 复发：仍存在目录收集中隐形的 p4_test_*.py: {residual}"


def test_p4_items_are_collected(collection: subprocess.CompletedProcess[str]) -> None:
    """#1707：重命名后的 test_p4_* 文件用例必须真实出现在全库收集中。

    只数「文件名为 test_p4_*.py」的 node id——仓库里另有以 test_p4_ 开头的
    测试函数名（tests/perf/test_geocompute_v7_perf.py 等），按裸字符串数会混入。
    """
    p4_items = [
        line
        for line in collection.stdout.splitlines()
        if re.search(r"/test_p4_[^/]+\.py::", line)
    ]
    assert len(p4_items) >= _MIN_P4_ITEMS, (
        f"收集中仅见 {len(p4_items)} 条 test_p4_* 文件用例（下限 {_MIN_P4_ITEMS}）——"
        "p4 批测试有再次隐形的风险"
    )
