"""tests/ 布局守卫（issue #1655 防回归）：pytest 模块名唯一性。

pytest 默认 prepend import 模式下，测试文件的模块名 = 从文件所在目录沿
``__init__.py`` 链向上截取的包路径 + basename；目录链无 ``__init__.py``
时退化为裸 basename。两个不同路径的文件计算出同一模块名，第二次收集
即报 ``import file mismatch`` 硬错误，整个 run 在跑任何用例前退出
（#1655：``gis_harness/interaction_ir/test_contracts.py`` 与
``spatial_events/test_contracts.py`` 均无 ``__init__.py``，撞名
``test_contracts``，Backend Tests lane 零执行、release-gate→build→
deploy 全阻塞）。

三层断言：
1. 真实 tests/ 树 PASS —— 当前全仓测试文件无模块名冲突。
2. 负向 fixture —— tmp 树构造两个无 ``__init__.py`` 目录下的同名
   test_contracts.py，守卫必须报冲突且失败信息点名两个冲突文件。
3. 修复语义 fixture —— 给其中一个目录补 ``__init__.py``（#1655 选项 A
   的修法）后守卫转 PASS，锁住"补 __init__.py 即消冲突"的契约。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = REPO_ROOT / "tests"


def _module_name_of(path: Path) -> str:
    """复刻 pytest prepend import 模式的模块名：沿 ``__init__.py`` 链向上截取包路径。"""
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def _collisions(root: Path) -> dict[str, list[Path]]:
    """按 pytest 模块名分组 root 下全部测试文件，返回冲突组 {模块名: [文件, ...]}。"""
    grouped: dict[str, list[Path]] = {}
    for pattern in ("test_*.py", "*_test.py"):
        for path in sorted(root.rglob(pattern)):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            grouped.setdefault(_module_name_of(path), []).append(path)
    return {name: files for name, files in grouped.items() if len(files) > 1}


def _render(collisions: dict[str, list[Path]]) -> str:
    lines = [
        "tests/ 布局违规：以下测试文件计算出同一 pytest 模块名，"
        "收集期会触发 import file mismatch 硬错误：",
    ]
    for name, files in sorted(collisions.items()):
        lines.append(f"  模块名 {name!r}:")
        lines.extend(f"    {f}" for f in files)
    lines.append("修复：给冲突目录链补 __init__.py 包化（推荐），或重命名为唯一 basename。")
    return "\n".join(lines)


# ── 1. 真实 tests/ 树 ─────────────────────────────────────────────────────────────


def test_real_tests_tree_has_unique_module_names():
    collisions = _collisions(TESTS_ROOT)
    assert not collisions, _render(collisions)


# ── 2. 负向 fixture ───────────────────────────────────────────────────────────────


def test_guard_flags_duplicate_basename_without_init(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    body = "def test_placeholder():\n    pass\n"
    (tmp_path / "a" / "test_contracts.py").write_text(body, encoding="utf-8")
    (tmp_path / "b" / "test_contracts.py").write_text(body, encoding="utf-8")
    collisions = _collisions(tmp_path)
    assert collisions, "两个无 __init__.py 目录下的同名测试文件必须被判冲突"
    rendered = _render(collisions)
    assert "test_contracts" in rendered
    assert str(tmp_path / "a" / "test_contracts.py") in rendered
    assert str(tmp_path / "b" / "test_contracts.py") in rendered


# ── 3. 修复语义：补 __init__.py 即消冲突（#1655 选项 A） ───────────────────────────


def test_guard_passes_once_package_init_added(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    body = "def test_placeholder():\n    pass\n"
    (tmp_path / "a" / "test_contracts.py").write_text(body, encoding="utf-8")
    (tmp_path / "b" / "test_contracts.py").write_text(body, encoding="utf-8")
    (tmp_path / "a" / "__init__.py").write_text("", encoding="utf-8")
    assert not _collisions(tmp_path), "补 __init__.py 包化后不应再判冲突"


@pytest.mark.parametrize("suffix", ["test_*.py", "*_test.py"])
def test_guard_covers_both_pytest_file_patterns(tmp_path, suffix):
    """pytest 默认 python_files = test_*.py *_test.py，两种形态都要纳入守卫。"""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    stem = "test_contracts" if suffix == "test_*.py" else "contracts_test"
    (tmp_path / "a" / f"{stem}.py").write_text("def test_p():\n    pass\n", encoding="utf-8")
    (tmp_path / "b" / f"{stem}.py").write_text("def test_p():\n    pass\n", encoding="utf-8")
    assert _collisions(tmp_path), f"守卫必须覆盖 pytest 文件模式 {suffix}"
