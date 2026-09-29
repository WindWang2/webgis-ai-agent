#!/usr/bin/env python3
"""import 边界门禁（ADR-0216 D3，标准库 AST，零新依赖）。

用法：``python scripts/check_import_boundaries.py <repo_root>``

规则（零容忍，含函数级懒导入 —— #1541/#1542 的真实形态就是函数体内 import）：
  - ``app/core/**``            → 禁 import app.{services,api,lib,schemas,tools,extensions_platform}
  - ``app/contracts/**``       → 禁 import 上层（services/api/lib/schemas/tools/extensions_platform）
  - ``app/lib/cartography/**`` → 禁 import app.{services,api,tools,extensions_platform}

豁免仅两种（ADR-0216 D3）：
  1. ``if TYPE_CHECKING:`` 块内的 import（类型面，非运行时边）；
  2. 行内 ``# h01:allow`` 注释（逃生口；tests/test_import_boundaries.py 锁定
     全仓 0 使用，用了必须在 PR 说明并同步该测试）。

退出码：0 = 无违规（stdout 带 ``[import-boundaries] OK``）；1 = 有违规
（stderr 逐条列出：违规文件、import 目标、规则依据）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Optional, Tuple

#: 受治理层 → 禁止依赖的 app.* 前缀（ADR-0216 D3）。
FORBIDDEN: List[Tuple[str, Tuple[str, ...]]] = [
    ("app/core", (
        "app.services", "app.api", "app.lib",
        "app.schemas", "app.tools", "app.extensions_platform",
    )),
    ("app/contracts", (
        "app.services", "app.api", "app.lib",
        "app.schemas", "app.tools", "app.extensions_platform",
    )),
    ("app/lib/cartography", (
        "app.services", "app.api",
        "app.tools", "app.extensions_platform",
    )),
]

ALLOW_MARK = "h01:allow"


def _is_type_checking_test(node: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` 判定。"""
    if not isinstance(node, ast.Name):
        return False
    return node.id == "TYPE_CHECKING"


def _type_checking_guarded(node: ast.AST, parents: dict) -> bool:
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.If) and _is_type_checking_test(cur.test):
            return True
        cur = parents.get(id(cur))
    return False


def _build_parents(tree: ast.AST) -> dict:
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _forbidden_roots(path: Path) -> Optional[Tuple[str, ...]]:
    posix = path.as_posix()
    for prefix, roots in FORBIDDEN:
        if posix.startswith(prefix + "/") or posix == prefix:
            return roots
    return None


def _targets(node: ast.AST) -> List[str]:
    """import 语句 → 绝对目标模块清单（相对导入返回空 —— 跨层必是绝对路径）。"""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return []
        base = node.module or ""
        # ``from app.services import chat_engine`` → 目标含包本身与成员模块。
        out = [base] if base else []
        out.extend(f"{base}.{a.name}" for a in node.names if base)
        return out
    return []


def _line_has_allow(source_lines: List[str], lineno: int) -> bool:
    if 1 <= lineno <= len(source_lines):
        return ALLOW_MARK in source_lines[lineno - 1]
    return False


def check(root: Path) -> List[str]:
    violations: List[str] = []
    app_dir = root / "app"
    if not app_dir.is_dir():
        return violations
    for path in sorted(app_dir.rglob("*.py")):
        roots = _forbidden_roots(path.relative_to(root))
        if roots is None:
            continue
        rel = path.relative_to(root).as_posix()
        dotted = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines()
        parents = _build_parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if _type_checking_guarded(node, parents):
                continue
            if _line_has_allow(lines, getattr(node, "lineno", 0)):
                continue
            for target in _targets(node):
                if any(target == r or target.startswith(r + ".") for r in roots):
                    layer = dotted.rsplit(".", 1)[0] if "." in dotted else dotted
                    violations.append(
                        f"{rel}: import {target} 违反 ADR-0216 分层边界"
                        f"（{layer} 层禁止依赖该目标模块，"
                        f"module={dotted}）"
                    )
    return violations


def main(argv: List[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    violations = check(root)
    if violations:
        print("import 边界违规（ADR-0216）:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "修复：把共享形状下沉 app/contracts（kernel），或改由上层调用方注入；"
            "确属遗留无法即刻修复的，行内 `# h01:allow <理由>` 并在 PR 说明"
            "（tests/test_import_boundaries.py 锁定全仓 0 使用）。",
            file=sys.stderr,
        )
        return 1
    print(f"[import-boundaries] OK (scanned {root / 'app'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
