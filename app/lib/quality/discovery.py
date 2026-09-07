"""测试引用发现（Quality Manifest 的派生源之一，ADR-0104）。

扫描 backend/frontend 测试源码，建立「registry 标识符 → 引用它的测试文件」
索引。这是**只读投影**：不修改任何测试；静态引用只是必要条件证据，
不等价于行为覆盖（与行覆盖率一样，不能单独当质量判断）。

设计约束：
- 确定：同一提交两次构建结果一致（排序、去重、repo 相对路径、正斜杠）。
- 有界：只索引「感兴趣的标识符」（调用方传入 registry 目录），不建全量符号表。
- 自排除：``tests/quality/`` 与 ``tests/unit/test_descriptor_coverage_gate.py``
  等闸测试会机械地引用全部标识符，扫描时排除，避免"闸文件让一切变成
  已测试"的自指污染。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# (repo 相对根, 后缀) —— 与 pytest.ini testpaths / vitest 配置保持同源。
SCAN_ROOTS: tuple = (
    ("tests", {".py"}),
    ("frontend/tests", {".ts", ".tsx"}),
    ("frontend/test", {".ts", ".tsx"}),
)

# 闸 / 生成器自身：机械引用所有标识符，必须排除。
EXCLUDED_PREFIXES: tuple = (
    "tests/quality/",
    "tests/unit/test_descriptor_coverage_gate.py",
)

_MAX_FILES = 5000  # 硬上界：防止未来误把大数据目录挂进扫描根。


def repo_root() -> Path:
    """repo 根（app/lib/quality/discovery.py → 上溯 3 级）。"""
    return Path(__file__).resolve().parents[3]


def _iter_scan_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for rel_root, suffixes in SCAN_ROOTS:
        base = root / rel_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            files.append(path)
            if len(files) >= _MAX_FILES:
                break
        if len(files) >= _MAX_FILES:
            break
    return files


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def discover_test_references(
    names: Iterable[str],
    root: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """返回 {name: [引用它的测试文件（repo 相对路径，升序）]}。

    只包含至少被引用一次的 name。纯标识符按词法 token 匹配；
    含非标识符字符的 name 退化为原文子串匹配。
    """
    base = root if root is not None else repo_root()
    wanted = sorted(set(names))
    if not wanted:
        return {}
    ident_names = {n for n in wanted if n.isidentifier()}
    other_names = [n for n in wanted if not n.isidentifier()]

    refs: Dict[str, List[str]] = {n: [] for n in wanted}
    for path in _iter_scan_files(base):
        rel = _rel(base, path)
        if rel.startswith(EXCLUDED_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ident_names:
            tokens = set(_TOKEN_RE.findall(text))
            for name in ident_names:
                if name in tokens:
                    refs[name].append(rel)
        for name in other_names:
            if name in text:
                refs[name].append(rel)
    return {n: sorted(files) for n, files in refs.items() if files}
