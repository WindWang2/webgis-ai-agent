#!/usr/bin/env python
"""债务棘轮门禁（#1377 收敛型延期项的可持续处置机制）。

对五类大规模存量债维护入库基线，``check`` 保证"只减不增"——
一次性清零 471 处 ``: any`` / 574 处 ``except: pass`` 不现实，但从此
每次提交都不允许让数字变大，重构自然单调收敛。

基线格式 v2（按文件颗粒化）::

    {"version": 2,
     "frontend_explicit_any": {"frontend/src/a.ts": 3, ...},
     "except_silent":        {"app/foo.py": 2, ...},
     "assertless_tests":     {"tests/test_x.py": 1, ...},
     "unannotated_defs":     {"app/bar.py": 12, ...},
     "unannotated_defs_pct": 11.74,
     "god_modules":          {"app/huge.py": 3252, ...}}

计数型规则（前四个 + god_modules）按"路径级 + 总量"双重只减不增；
``unannotated_defs_pct`` 保留全局占比语义（ISSUE-057 的 mypy 白名单棘轮
配套）。v1 标量总量基线仍可读：标量条目自动退回"仅总量比对"的旧判定，
v1 未含的规则键跳过；注意 v1 兼容读取下 `frontend_explicit_any` 因注释
屏蔽（G14 误报修复）比旧口径少计注释命中（当前树 -2），存在相应余量，
入库 v2 基线已按新口径重算锁定。``baseline`` 命令永远写 v2；v2 基线
缺失任何规则键即判违规（防删键关闸）。

指标::

    frontend_explicit_any  frontend/**/*.{ts,tsx} 中代码位置 ": any" 标注计数
                           （ISSUE-028；TS 词法扫描屏蔽注释/字符串/正则字面量，
                           只在真实标注位置计数；eslint no-explicit-any 已 warn 级配套）
    except_silent          app/**/*.py 中 body 仅 pass/... 的 except 处理器
                           （ISSUE-042；AST 计数，排除有日志/注释块的）
    god_modules            app/**/*.py 中 >2000 行的文件清单与行数
                           （ISSUE-044；新增条目即违规，存量允许 ±SLACK 浮动）
    assertless_tests       tests/**/*.py 中无任何断言调用的 test_* 函数
                           （ISSUE-059；AST 识别 assert/pytest.raises/mock断言）
    unannotated_defs       app/**/*.py 中未完整注解的函数数（按文件）
    unannotated_defs_pct   上述函数占全部函数的百分比（全局，ISSUE-057）

用法::

    python scripts/debt_ratchet.py measure [--root R]   # 打印当前指标（JSON）
    python scripts/debt_ratchet.py check   [--root R]   # 退化 → exit 1
    python scripts/debt_ratchet.py locate  [--rule R]   # 逐条列出 file:line
    python scripts/debt_ratchet.py baseline [--root R]  # 重写基线（收敛后调低水位）

``--root`` 供测试/演练指向临时目录；生产 CI 用默认仓库根。

基线文件：``scripts/debt_ratchet_baseline.json``（入库）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).with_name("debt_ratchet_baseline.json")

GOD_MODULE_LINES = 2000
GOD_MODULE_SLACK = 50  # 存量 God module 允许 ±50 行浮动（重构期间的震荡带）
BASELINE_VERSION = 2
MAX_LOCATE_LINES = 8  # check 失败输出里单文件最多列出的行号数（防刷屏）

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

# 计数型规则的稳定输出顺序（check / locate / baseline 摘要共用）
COUNT_RULES = ("frontend_explicit_any", "except_silent",
               "assertless_tests", "unannotated_defs")
LOCATE_RULES = ("frontend_explicit_any", "except_silent", "god_modules",
                "assertless_tests", "unannotated_defs")


@dataclass(frozen=True)
class DebtItem:
    """单条债务实例；``line`` 为 1 起行号，文件级条目（god module）为 0。"""

    rule: str
    path: str  # 相对扫描根的 posix 路径
    line: int
    detail: str = ""


# ---------------------------------------------------------------- TS 词法屏蔽

# `/` 出现在这些字符/关键字之后按正则字面量起始处理（经典启发式），
# 其余位置按除法处理。`)` 后一律按除法（`(a)/2` 远多于 `if (x) /re/`）；
# 不含 `<`：JSX 闭合标签 `</tag>` 的 `/` 远比 `< /re/` 起始的正则常见。
_REGEX_STARTERS = set("(,=:[!&|?{};+-*%~^>")
_REGEX_KEYWORD_RE = re.compile(
    r"\b(return|typeof|instanceof|in|of|new|delete|void|throw|case|do|else"
    r"|yield|await)$")


def _looks_like_regex_start(text: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    if j < 0:
        return True
    if text[j] in _REGEX_STARTERS:
        return True
    if text[j].isalnum() or text[j] in "_$)":
        return _REGEX_KEYWORD_RE.search(text[:j + 1]) is not None
    return False


def _mask_span(out: list[str], text: str, start: int, end: int) -> None:
    """把 [start, end) 屏蔽为空格，保留换行（保证行号偏移不变）。"""
    for k in range(start, min(end, len(text))):
        if text[k] != "\n":
            out[k] = " "


def _mask_ts_noncode(text: str) -> str:
    """把 TS/TSX 源码中注释、字符串/模板字面量、正则字面量的内容替换为等长空格。

    返回文本与输入等长且换行位置不变，因此对屏蔽后文本做 regex 匹配的
    行号与原文一致。目的：``: any`` 只在代码位置计数——注释里的 "any" 字样、
    字符串中的示例代码、正则字面量（如 lint 规则本体 ``/: any/``）都不算债。
    已知局限：JSX 文本节点不屏蔽（出现 ": any" 字样的概率与存量见 PR 审计）。
    """
    out = list(text)
    n = len(text)
    i = 0
    # 栈帧：["str", 引号字符] 或 ["interp", 花括号深度]（模板 ${...} 插值）
    frames: list[list] = []

    def in_code() -> bool:
        return not frames or frames[-1][0] == "interp"

    while i < n:
        c = text[i]
        if in_code() and c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            _mask_span(out, text, i, j)
            i = j
            continue
        if in_code() and c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            _mask_span(out, text, i, end)
            i = end
            continue
        if in_code() and c == "/" and _looks_like_regex_start(text, i):
            # 正则字面量：必须在本行内找到未转义的收尾 `/`（字符类 [] 内的
            # `/` 不收尾）。ECMAScript 规定正则字面量不能含裸换行——同行
            # 找不到收尾即判定为除法，一个字符也不屏蔽（防 `</tag>` 之类
            # 误判跨行吞掉真实标注，G14 review P1）。
            end_ok = None
            j = i + 1
            in_class = False
            while j < n:
                ch = text[j]
                if ch == "\n":
                    break
                if ch == "\\":
                    if j + 1 < n and text[j + 1] == "\n":
                        break
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    end_ok = j
                    break
                j += 1
            if end_ok is not None:
                _mask_span(out, text, i, end_ok + 1)
                i = end_ok + 1
            else:
                i += 1
            continue
        if in_code() and c in ("'", '"', "`"):
            frames.append(["str", c])
            i += 1
            continue
        top = frames[-1] if frames else None
        if top is not None and top[0] == "str":
            quote = top[1]
            if c == "\n" and quote != "`":
                # TS 的 '/" 字符串不允许裸换行（跨行即语法错误）。若此帧仍
                # 跨着换行，说明是词法误入（如 JSX 文本里的撇号）——在行尾
                # 复位，把泄漏的爆炸半径限制在一行内，防止奇偶级联翻转。
                frames.pop()
                i += 1
                continue
            if c == "\\":
                _mask_span(out, text, i, min(i + 2, n))
                i += 2
                continue
            if quote == "`" and c == "$" and i + 1 < n and text[i + 1] == "{":
                frames.append(["interp", 1])
                i += 2
                continue
            if c == quote:
                frames.pop()
            else:
                _mask_span(out, text, i, i + 1)
            i += 1
            continue
        if top is not None and top[0] == "interp":
            if c == "{":
                top[1] += 1
            elif c == "}":
                top[1] -= 1
                if top[1] == 0:
                    frames.pop()
            i += 1
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 条目采集


def _iter_files(root: Path, subdir: str, suffixes: tuple[str, ...]) -> list[Path]:
    base = root / subdir
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*")
        if p.suffix in suffixes
        and not any(part in _FRONTEND_EXCLUDE for part in p.parts)
    )


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _count_by_path(items: list[DebtItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        counts[it.path] = counts.get(it.path, 0) + 1
    return dict(sorted(counts.items()))


def collect_frontend_any(root: Path) -> list[DebtItem]:
    items: list[DebtItem] = []
    for path in _iter_files(root, "frontend", (".ts", ".tsx")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(root, path)
        masked = _mask_ts_noncode(text)
        for m in _ANY_RE.finditer(masked):
            items.append(DebtItem("frontend_explicit_any", rel,
                                  _line_of(masked, m.start())))
    return items


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


def collect_except_silent(root: Path) -> list[DebtItem]:
    items: list[DebtItem] = []
    for path in _iter_files(root, "app", (".py",)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError):
            continue
        rel = _rel(root, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_silent_handler(node):
                detail = "<bare except>"
                if node.type is not None:
                    detail = f"except {ast.unparse(node.type)}"[:60]
                items.append(DebtItem("except_silent", rel, node.lineno, detail))
    return items


def _god_module_lines(root: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for path in _iter_files(root, "app", (".py",)):
        try:
            lines = len(path.read_text(encoding="utf-8", errors="replace")
                        .splitlines())
        except OSError:
            continue
        if lines > GOD_MODULE_LINES:
            result[_rel(root, path)] = lines
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


def collect_assertless_tests(root: Path) -> list[DebtItem]:
    items: list[DebtItem] = []
    for path in _iter_files(root, "tests", (".py",)):
        if not path.name.startswith("test_"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError):
            continue
        rel = _rel(root, path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_") \
                    and not _has_assertion(node):
                items.append(DebtItem("assertless_tests", rel, node.lineno,
                                      node.name))
    return items


def _def_fully_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    params = (args.posonlyargs + args.args + args.kwonlyargs
              + ([args.vararg] if args.vararg else [])
              + ([args.kwarg] if args.kwarg else []))
    params_ok = all(
        a.annotation is not None or a.arg in ("self", "cls")
        for a in params
    )
    return params_ok and node.returns is not None


def _scan_unannotated(root: Path) -> tuple[list[DebtItem], float]:
    items: list[DebtItem] = []
    total = annotated = 0
    for path in _iter_files(root, "app", (".py",)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError):
            continue
        rel = _rel(root, path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                if _def_fully_annotated(node):
                    annotated += 1
                else:
                    items.append(DebtItem("unannotated_defs", rel, node.lineno,
                                          node.name))
    pct = round(100.0 * (1 - annotated / total), 2) if total else 0.0
    return items, pct


def _collect(root: Path) -> tuple[dict, dict[str, list[DebtItem]]]:
    any_items = collect_frontend_any(root)
    except_items = collect_except_silent(root)
    assertless_items = collect_assertless_tests(root)
    unann_items, unann_pct = _scan_unannotated(root)

    god_map = _god_module_lines(root)
    god_items = [
        DebtItem("god_modules", path, 0, f"{lines} lines > {GOD_MODULE_LINES}")
        for path, lines in sorted(god_map.items())
    ]

    current = {
        "version": BASELINE_VERSION,
        "frontend_explicit_any": _count_by_path(any_items),
        "except_silent": _count_by_path(except_items),
        "god_modules": god_map,
        "assertless_tests": _count_by_path(assertless_items),
        "unannotated_defs": _count_by_path(unann_items),
        "unannotated_defs_pct": unann_pct,
    }
    items: dict[str, list[DebtItem]] = {}
    for it in (*any_items, *except_items, *god_items,
               *assertless_items, *unann_items):
        items.setdefault(it.rule, []).append(it)
    return current, items


def measure(root: Path | None = None) -> dict:
    return _collect(root or REPO_ROOT)[0]


# ---------------------------------------------------------------- 门禁判定


def _as_path_map(value) -> dict | None:
    """v2 条目是 {路径: 计数}；v1 标量返回 None（仅总量比对）。"""
    return dict(value) if isinstance(value, dict) else None


def _locate_suffix(items: list[DebtItem], path: str) -> str:
    lines = sorted(i.line for i in items if i.path == path)
    shown = ", ".join(f"L{x}" for x in lines[:MAX_LOCATE_LINES])
    more = (f" …(+{len(lines) - MAX_LOCATE_LINES} 行)"
            if len(lines) > MAX_LOCATE_LINES else "")
    return f"；定位: {shown}{more}" if shown else ""


def _gate_failures(baseline: dict, current: dict,
                   items: dict[str, list[DebtItem]]) -> list[str]:
    failures: list[str] = []

    # v2 基线必须含全部规则键；缺键即判违规（防手改 JSON 删键关闸）。
    # 「缺键跳过」只留给 v1 标量基线的兼容读取（v1 本就没有新规则键）。
    is_v2 = baseline.get("version") == 2

    for key in COUNT_RULES:
        if key not in baseline:
            if is_v2:
                failures.append(
                    f"{key}: 基线（v2）缺失规则键 —— 基线文件不完整或被篡改，"
                    f"请用 `baseline` 命令重新生成")
            continue
        cur_map: dict = current[key]
        cur_total = sum(cur_map.values())
        base_value = baseline[key]
        base_map = _as_path_map(base_value)
        if base_map is None:
            # v1 标量：退回旧判定（仅总量）
            base_total = int(base_value)
            if cur_total > base_total:
                failures.append(
                    f"{key}: {cur_total} > baseline {base_total} "
                    f"(+{cur_total - base_total})")
            continue
        base_total = sum(base_map.values())
        for path in sorted(cur_map):
            cur, base = cur_map[path], base_map.get(path, 0)
            if cur > base:
                failures.append(
                    f"{key}: {path} {cur} > baseline {base} (+{cur - base})"
                    f"{_locate_suffix(items.get(key, []), path)}")
        if cur_total > base_total:
            failures.append(
                f"{key} 总量: {cur_total} > baseline {base_total} "
                f"(+{cur_total - base_total})")

    if "unannotated_defs_pct" not in baseline:
        if is_v2:
            failures.append(
                "unannotated_defs_pct: 基线（v2）缺失规则键 —— "
                "基线文件不完整或被篡改，请用 `baseline` 命令重新生成")
    else:
        base_pct = baseline["unannotated_defs_pct"]
        cur_pct = current["unannotated_defs_pct"]
        if cur_pct > base_pct:
            failures.append(
                f"unannotated_defs_pct: {cur_pct} > baseline {base_pct} "
                f"(+{round(cur_pct - base_pct, 2)})")

    base_gods: dict = baseline.get("god_modules", {})
    if not isinstance(base_gods, dict):
        base_gods = {}
    for path, lines in current["god_modules"].items():
        if path not in base_gods:
            failures.append(
                f"god_modules: NEW >{GOD_MODULE_LINES}-line file {path} ({lines})")
        elif lines > base_gods[path] + GOD_MODULE_SLACK:
            failures.append(
                f"god_modules: {path} grew {base_gods[path]}→{lines} "
                f"(slack {GOD_MODULE_SLACK})")
    return failures


def check(baseline_path=None, root=None) -> int:
    bp = Path(baseline_path) if baseline_path else BASELINE_PATH
    if not bp.is_file():
        print(f"baseline missing: {bp} — run `baseline` first")
        return 2
    baseline = json.loads(bp.read_text(encoding="utf-8"))
    current, items = _collect(root or REPO_ROOT)
    failures = _gate_failures(baseline, current, items)

    if failures:
        print("debt ratchet FAILED — 债务只减不增：")
        for f in failures:
            print(f"  x {f}")
        return 1
    print("debt ratchet OK — 各指标未超基线（v2 颗粒化：路径级+总量 双重棘轮）")
    for key in COUNT_RULES:
        cur = sum(current[key].values())
        if key not in baseline:
            print(f"  {key}: {cur} / baseline 无（v1 基线未含此规则，未启用门禁）")
            continue
        base_value = baseline[key]
        base = (sum(base_value.values()) if isinstance(base_value, dict)
                else int(base_value))
        delta = f"(-{base - cur})" if cur < base else ""
        print(f"  {key}: {cur} / baseline {base} {delta}")
    cur_pct = current["unannotated_defs_pct"]
    base_pct = baseline.get("unannotated_defs_pct", 0.0)
    delta = f"(-{round(base_pct - cur_pct, 2)})" if cur_pct < base_pct else ""
    print(f"  unannotated_defs_pct: {cur_pct} / baseline {base_pct} {delta}")
    print(f"  god_modules: {len(current['god_modules'])} / "
          f"baseline {len(baseline.get('god_modules', {}))}")
    return 0


def _locate_lines(root: Path, rule: str | None = None) -> list[str]:
    current, items = _collect(root)
    rules = (rule,) if rule else LOCATE_RULES
    out: list[str] = []
    for r in rules:
        group = sorted(items.get(r, []), key=lambda i: (i.path, i.line))
        out.append(f"# {r}: {len(group)}")
        for it in group:
            if it.line:
                detail = f"  {it.detail}" if it.detail else ""
                out.append(f"{it.path}:{it.line}{detail}")
            else:
                out.append(f"{it.path}  ({it.detail})")
    out.append(f"# unannotated_defs_pct: {current['unannotated_defs_pct']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="债务棘轮门禁（#1377）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command",
                    choices=("measure", "check", "locate", "baseline"))
    ap.add_argument("--root", type=Path, default=None,
                    help="扫描根目录（默认仓库根；测试/演练用）")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="基线文件（默认 scripts/debt_ratchet_baseline.json）")
    ap.add_argument("--rule", default=None,
                    help="locate: 只列出指定规则")
    args = ap.parse_args()
    root = args.root or REPO_ROOT

    if args.command == "baseline":
        data = measure(root)
        target = args.baseline or BASELINE_PATH
        target.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"baseline written: {target}")
        for key in COUNT_RULES:
            print(f"  {key}: {sum(data[key].values())}")
        print(f"  unannotated_defs_pct: {data['unannotated_defs_pct']}")
        print(f"  god_modules: {len(data['god_modules'])}")
        return 0
    if args.command == "check":
        return check(args.baseline, root)
    if args.command == "locate":
        for line in _locate_lines(root, args.rule):
            print(line)
        return 0
    print(json.dumps(measure(root), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
