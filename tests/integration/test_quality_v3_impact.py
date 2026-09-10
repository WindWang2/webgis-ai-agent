"""影响分析与测试选择测试（Quality V3 W7）。

核心契约（Subagent-A M-1 修订）：
- import 闭包正确（含 `from pkg import sub` 边）；
- 完备性：每个 changed app 文件必须有 import 命中或映射兜底，否则
  显式 uncovered；
- 截断必须显式警告（继承 V2 [:40] 静默截断的教训）；
- 防漏报护栏：真实分支 diff 上，V2 目录映射的产物 ⊆ 本选择器结果。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.lib.integration.impact import (
    ImportGraph,
    _V2_DOMAIN_MAP,
    build_graph,
    map_directory_target,
    module_name_for,
    select_tests,
)

REPO = Path(__file__).resolve().parents[2]


# ── 纯函数单元 ──────────────────────────────────────────────────────────

def test_module_name_for():
    assert module_name_for("app/services/foo.py") == "app.services.foo"
    assert module_name_for("app/lib/integration/__init__.py") == \
        "app.lib.integration"
    assert module_name_for("scripts/foo.py") is None


def test_reverse_closure_transitive():
    graph = ImportGraph()
    # 链：t.py → app.a；app/b.py → app.a（b 依赖 a）
    graph.tests["tests/test_a.py"] = ["app.a"]
    graph.app["app/b.py"] = ["app.a"]
    closure = graph.reverse_app_closure({"app.a"})
    assert closure == {"app.a", "app.b"}, "闭包全程是模块名空间"
    # 反向：改 a → 测试命中；闭包不含无关模块
    graph.app["app/c.py"] = []
    closure2 = graph.reverse_app_closure({"app.c"})
    assert closure2 == {"app.c"}


# ── 合成仓库：正/负/边界 ────────────────────────────────────────────────

@pytest.fixture()
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "app/pkg").mkdir(parents=True)
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "app/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app/pkg/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app/pkg/leaf.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app/pkg/mid.py").write_text(
        "from app.pkg.leaf import VALUE\n", encoding="utf-8")
    (tmp_path / "tests/unit/test_mid.py").write_text(
        "from app.pkg import mid\n", encoding="utf-8")
    (tmp_path / "tests/test_root.py").write_text(
        "import app.pkg.leaf\n", encoding="utf-8")
    return tmp_path


def test_synthetic_closure_and_selection(py_repo: Path):
    graph = build_graph(py_repo, use_cache=False)
    # from pkg import mid → pkg 与 pkg.mid 都要进边（闭包不漏）
    assert "app.pkg" in graph.tests["tests/unit/test_mid.py"]
    assert "app.pkg.mid" in graph.tests["tests/unit/test_mid.py"]

    r = select_tests(["app/pkg/leaf.py"], repo_root=py_repo, graph=graph)
    assert "tests/unit/test_mid.py" in r.targets, "传递依赖必须选中"
    assert "tests/test_root.py" in r.targets, "直接 import 必须选中"
    assert r.complete, "有 tests 目录兜底，不该 uncovered"


def test_uncovered_when_no_tests_dir(py_repo: Path):
    """负例：合成仓库删掉 tests → import 命中仍在但映射兜底不存在；
    leaf 仍被 test 覆盖 → 不算 uncovered；孤立 app 文件算。"""
    import shutil
    shutil.rmtree(py_repo / "tests")
    (py_repo / "app/pkg/orphan.py").write_text("X = 1\n", encoding="utf-8")
    graph2 = build_graph(py_repo, use_cache=False)
    r = select_tests(["app/pkg/orphan.py"], repo_root=py_repo, graph=graph2)
    assert not r.complete, "无测试且无兜底目录 → 必须 uncovered"
    assert "app/pkg/orphan.py" in r.uncovered


def test_frontend_and_scripts_mapping(py_repo: Path):
    (py_repo / "scripts").mkdir()
    (py_repo / "scripts/tool.py").write_text("", encoding="utf-8")
    (py_repo / "tests/quality").mkdir()
    graph = build_graph(py_repo, use_cache=False)
    r = select_tests(["scripts/tool.py"], repo_root=py_repo, graph=graph)
    assert "tests/quality" in r.targets
    # frontend 映射 None：不算 uncovered、不产生后端目标
    r2 = select_tests(["frontend/src/a.ts"], repo_root=py_repo, graph=graph)
    assert r2.complete
    assert not [t for t in r2.targets if t.startswith("frontend")]


def test_truncation_is_explicit(py_repo: Path):
    graph = build_graph(py_repo, use_cache=False)
    r = select_tests(["app/pkg/leaf.py"], repo_root=py_repo, graph=graph,
                     max_targets=1)
    assert r.truncated and r.warnings, "截断必须显式"


def test_cache_incremental(py_repo: Path):
    g1 = build_graph(py_repo, use_cache=True)
    (py_repo / "app/pkg/leaf.py").write_text("VALUE = 2\n", encoding="utf-8")
    g2 = build_graph(py_repo, use_cache=True)
    assert g1.hashes["app/pkg/leaf.py"] != g2.hashes["app/pkg/leaf.py"]
    # 未变文件沿用缓存（hash 相等即可视为沿用）
    assert g1.hashes["app/pkg/mid.py"] == g2.hashes["app/pkg/mid.py"]


def test_syntaxwarning_suppressed(tmp_path: Path):
    """被扫描文件的历史转义序列不得污染输出（warnings 全程抑制）。"""
    import warnings

    d = tmp_path / "app"
    d.mkdir()
    (d / "legacy.py").write_text(r'RE = "\d+\*"  # 历史无效转义', encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # 任何 warning → 异常
        graph = build_graph(tmp_path, use_cache=False)
    assert graph.app["app/legacy.py"] == []


# ── 真实仓库：防漏报护栏（M-1） ─────────────────────────────────────────

def _branch_changed_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "origin/master...HEAD"],
        cwd=REPO, capture_output=True, text=True, timeout=30).stdout
    return [f for f in out.splitlines() if f]


def test_real_branch_guard_v2_mapping_subset_of_selector():
    """护栏：V2 目录映射在本分支 diff 上的产物 ⊆ 本选择器结果。

    V2 映射（quality_runner._changed_py_targets）：app/lib/gis→tests/unit/gis、
    app/lib/quality→tests/quality、app/tools→tests/unit/tools、
    app/services→tests/unit、app/api→tests、app/core→tests。
    """

    changed = [f for f in _branch_changed_files() if f.startswith("app/")]
    if not changed:
        # R2-M1：合并进 master 后 origin/master...HEAD 为空；纯 docs/前端
        # PR 也不涉 app —— 护栏只对"有 app 改动"的分支有意义
        pytest.skip("本分支无 app/ 改动（护栏语义仅覆盖 app 变更）")
    # 单一来源（R1-m6）：从 impact.py import，不再手工拷贝第三份
    v2_map = dict(_V2_DOMAIN_MAP)
    v2_targets: set[str] = set()
    for f in changed:
        for prefix, target in v2_map.items():
            if f.startswith(prefix + "/") and (REPO / target).is_dir():
                v2_targets.add(target)

    graph = build_graph(use_cache=True)
    result = select_tests(changed, graph=graph, max_targets=2000)
    missing = v2_targets - set(result.targets)
    assert not missing, (
        f"防漏报护栏破坏：V2 映射目标 {sorted(missing)} 未被选择器覆盖")
    assert result.complete, f"真实分支不得有 uncovered: {result.uncovered}"


def test_map_directory_target_fallbacks():
    # app 根级文件：首个存在的兜底目录（tests/unit 或 tests）
    assert map_directory_target("app/main.py", REPO) in ("tests", "tests/unit")
    assert map_directory_target("scripts/gen_quality_manifest.py", REPO) == \
        "tests/quality"
    assert map_directory_target("docs/quality/QUALITY_REPORT.md", REPO) == \
        "tests/quality"
    assert map_directory_target("README.md", REPO) == "tests"
    assert map_directory_target("frontend/src/app.tsx", REPO) is None
