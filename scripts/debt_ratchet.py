#!/usr/bin/env python
"""债务棘轮门禁（#1377 收敛型延期项的可持续处置机制）。

对五类大规模存量债维护入库基线，``check`` 保证"只减不增"——
一次性清零 471 处 ``: any`` / 574 处 ``except: pass`` 不现实，但从此
每次提交都不允许让数字变大，重构自然单调收敛。

指标::

    frontend_explicit_any  frontend/**/*.{ts,tsx} 中 ": any" 标注计数
                           （ISSUE-028；eslint no-explicit-any 已 warn 级配套）
    except_silent          app/**/*.py 中 body 仅 pass/... 的 except 处理器
                           （ISSUE-042；AST 计数，排除有日志/注释块的）
    god_modules            app/**/*.py 中 >2000 行的文件清单与行数
                           （ISSUE-044；新增条目即违规，存量允许 ±SLACK 浮动）
    assertless_tests       tests/**/*.py 中无任何断言调用的 test_* 函数
                           （ISSUE-059；AST 识别 assert/pytest.raises/mock断言）
    unannotated_defs       app/**/*.py 中未完整注解的函数占比 %
                           （ISSUE-057；配合 [tool.mypy] files= 白名单棘轮）

用法::

    python scripts/debt_ratchet.py measure            # 打印当前指标
    python scripts/debt_ratchet.py check              # 退化 → exit 1
    python scripts/debt_ratchet.py baseline           # 重写基线（收敛后调低水位）

基线文件：``scripts/debt_ratchet_baseline.json``（入库）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).with_name("debt_ratchet_baseline.json")

GOD_MODULE_LINES = 2000
GOD_MODULE_SLACK = 50  # 存量 God module 允许 ±50 行浮动（重构期间的震荡带）

_ANY_RE = re.compile(r":\s*any\b")
_FRONTEND_EXCLUDE = {
    "node_modules", ".next", "out", "build", "coverage", "dist",
    "next-env.d.ts",
}

_ASSERT_CALLS = {
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_any_call", "assert_not_called",
    "assert_has_calls", "assertEqual", "assertTrue", "assertFalse",
    "assertIn", "assertIs", "assertRaises", "assertAlmostEqual",
    "assertDictEqual", "assertListEqual", "assertIsNone", "assertIsNotNone",
    "assertGreater", "assertLess", "assertRegex", "fail",
}


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.suffix in suffixes
        and not any(part in _FRONTEND_EXCLUDE for part in p.parts)
    )


def _count_explicit_any() -> int:
    total = 0
    for path in _iter_files(REPO_ROOT / "frontend", (".ts", ".tsx")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(_ANY_RE.findall(text))
    return total


def _is_silent_handler(handler: ast.ExceptHandler) -> bool:
    body = [n for n in handler.body if not (
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    )]
    return bool(body) and all(
        isinstance(n, ast.Pass)
        or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            and n.value.value is ...)
        for n in body
    )


def _count_except_silent() -> int:
    total = 0
    for path in _iter_files(REPO_ROOT / "app", (".py",)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        total += sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and _is_silent_handler(node)
        )
    return total


def _god_modules() -> dict[str, int]:
    result: dict[str, int] = {}
    for path in _iter_files(REPO_ROOT / "app", (".py",)):
        try:
            lines = len(path.read_text(encoding="utf-8", errors="replace")
                        .splitlines())
        except OSError:
            continue
        if lines > GOD_MODULE_LINES:
            result[str(path.relative_to(REPO_ROOT)).replace("\\", "/")] = lines
    return result


def _has_assertion(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = ""
            f = node.func
            if isinstance(f, ast.Attribute):
                name = f.attr
            elif isinstance(f, ast.Name):
                name = f.id
            if name in _ASSERT_CALLS or name == "raises":
                return True
        if isinstance(node, ast.With):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call):
                    f = expr.func
                    if (isinstance(f, ast.Attribute) and f.attr == "raises") or (
                            isinstance(f, ast.Name) and f.id == "raises"):
                        return True
    return False


def _count_assertless_tests() -> int:
    total = 0
    for path in _iter_files(REPO_ROOT / "tests", (".py",)):
        if not path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_") \
                    and not _has_assertion(node):
                total += 1
    return total


def _unannotated_pct() -> float:
    total = annotated = 0
    for path in _iter_files(REPO_ROOT / "app", (".py",)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                args = node.args
                params = (args.posonlyargs + args.args + args.kwonlyargs
                          + ([args.vararg] if args.vararg else [])
                          + ([args.kwarg] if args.kwarg else []))
                params_ok = all(
                    a.annotation is not None or a.arg in ("self", "cls")
                    for a in params
                )
                if params_ok and node.returns is not None:
                    annotated += 1
    return round(100.0 * (1 - annotated / total), 2) if total else 0.0


def measure() -> dict:
    return {
        "frontend_explicit_any": _count_explicit_any(),
        "except_silent": _count_except_silent(),
        "god_modules": _god_modules(),
        "assertless_tests": _count_assertless_tests(),
        "unannotated_defs_pct": _unannotated_pct(),
    }


def check() -> int:
    if not BASELINE_PATH.is_file():
        print(f"baseline missing: {BASELINE_PATH} — run `baseline` first")
        return 2
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = measure()
    failures: list[str] = []

    for key in ("frontend_explicit_any", "except_silent",
                "assertless_tests", "unannotated_defs_pct"):
        base, cur = baseline.get(key, 0), current[key]
        if cur > base:
            failures.append(f"{key}: {cur} > baseline {base} (+{cur - base})")

    base_gods: dict = baseline.get("god_modules", {})
    for path, lines in current["god_modules"].items():
        if path not in base_gods:
            failures.append(f"god_modules: NEW >{GOD_MODULE_LINES}-line file {path} ({lines})")
        elif lines > base_gods[path] + GOD_MODULE_SLACK:
            failures.append(
                f"god_modules: {path} grew {base_gods[path]}→{lines} "
                f"(slack {GOD_MODULE_SLACK})")

    if failures:
        print("debt ratchet FAILED — 债务只减不增：")
        for f in failures:
            print(f"  x {f}")
        return 1
    print("debt ratchet OK — 各指标未超基线")
    for key in ("frontend_explicit_any", "except_silent",
                "assertless_tests", "unannotated_defs_pct"):
        cur, base = current[key], baseline.get(key, 0)
        delta = f"(-{base - cur})" if cur < base else ""
        print(f"  {key}: {cur} / baseline {base} {delta}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("measure", "check", "baseline"))
    args = ap.parse_args()

    if args.command == "baseline":
        data = measure()
        BASELINE_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"baseline written: {BASELINE_PATH}")
        return 0
    if args.command == "check":
        return check()
    data = measure()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
