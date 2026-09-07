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

import functools
import re
import subprocess
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

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


@functools.lru_cache(maxsize=8)
def _git_tracked_files(root: str) -> Optional[frozenset]:
    """git 已跟踪文件集（相对路径）。失败（非 git/无 git）返回 None。"""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True,
            text=True, timeout=30, check=True,
        ).stdout
    except Exception:  # noqa: BLE001 —— 退化为全扫描（本地实验场景）
        return None
    return frozenset(out.splitlines())


def _iter_scan_files(root: Path) -> List[Path]:
    """只扫 git 已跟踪文件（ADR-0104 R1 修复）：未跟踪/在飞的文件不得
    进入字节闸证据面，否则本地再生会把未提交状态烤进 manifest。"""
    files: List[Path] = []
    tracked = _git_tracked_files(str(root))
    if tracked is None:
        for rel_root, suffixes in SCAN_ROOTS:
            base = root / rel_root
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.suffix not in suffixes:
                    continue
                files.append(path)
                if len(files) >= _MAX_FILES:
                    return files
        return files
    for rel in sorted(tracked):
        hit = False
        for rel_root, suffixes in SCAN_ROOTS:
            if rel.startswith(rel_root + "/") and rel.endswith(tuple(suffixes)):
                hit = True
                break
        if not hit:
            continue
        path = root / rel
        if path.is_file():
            files.append(path)
        if len(files) >= _MAX_FILES:
            break
    return files


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


@functools.lru_cache(maxsize=8)
def _discover_cached(names: Tuple[str, ...], root_str: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    refs = _discover_impl(frozenset(names), Path(root_str))
    return tuple((n, tuple(files)) for n, files in refs.items())


def discover_test_references(
    names: Iterable[str],
    root: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """带 memo 的入口：同一 (names, root) 进程内只扫一遍盘
    （R1 review：gate 套件此前每次调用都全量重扫，10-25s 冗余）。"""
    base = (root if root is not None else repo_root()).resolve()
    ordered = tuple(sorted(set(names)))
    cached = _discover_cached(ordered, str(base))
    return {n: list(files) for n, files in cached}


def _discover_impl(names: FrozenSet[str], base: Path) -> Dict[str, List[str]]:
    """返回 {name: [引用它的测试文件（repo 相对路径，升序）]}。

    只包含至少被引用一次的 name。纯标识符按词法 token 匹配；
    含非标识符字符的 name 退化为原文子串匹配。
    """
    wanted = sorted(names)
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
