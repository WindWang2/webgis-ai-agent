"""行为化覆盖发现（Quality V2）——"dispatch 真的被调用过" 的静态证据。

与 ``discovery.py``（词法 token 匹配，"测试提到过名字"）互补：本模块用
AST 找到 **真实的 dispatch 调用点**——

    await registry.dispatch("buffer_analysis", {...})
    service.dispatch("map_view.fly_to", args)   # 字面量工具名才算
    reg.dispatch(tool_name, ...)                # 变量名 → 静态不可解，诚实跳过

判定规则：``<expr>.dispatch(<字符串字面量>, ...)`` 的第一个位置参数是
字符串常量，或存在 ``tool_name="..."`` 关键字。下界语义（诚实方向：以
"调用形态"为准，不把纯提及计为调用）；残余不精确处：不校验接收者类型
（任何名为 dispatch 的方法调用都计），因此个别非 registry 调度器的同名
方法可能被计入 —— min_behavioral_dispatch 由此取保守下限。

约束（与 discovery 同款）：
- 只读投影：绝不修改测试；
- 确定：同一提交两次扫描结果一致（排序、去重、repo 相对路径）；
- 有界：复用 discovery 的扫描根/排除清单/文件数硬上界。
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.lib.quality.discovery import _iter_scan_files, _rel, repo_root

#: 行为证据等级（工具行 ``behavior`` 字段词表）。
BEHAVIOR_LEVELS: Tuple[str, ...] = ("dispatch", "mention", "none")


def _literal_tool_names(node: ast.AST) -> Tuple[str, ...]:
    """从 Call 节点提取字面量工具名（无则空）。"""
    if not isinstance(node, ast.Call):
        return ()
    func = node.func
    is_dispatch = isinstance(func, ast.Attribute) and func.attr == "dispatch"
    if not is_dispatch:
        return ()
    names: List[str] = []
    if node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.append(first.value)
    for kw in node.keywords:
        if (
            kw.arg == "tool_name"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            names.append(kw.value.value)
    return tuple(names)


@functools.lru_cache(maxsize=8)
def _behavioral_cached(root_str: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    base = Path(root_str)
    hits: Dict[str, List[str]] = {}
    for path in _iter_scan_files(base):
        rel = _rel(base, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        found: List[str] = []
        for node in ast.walk(tree):
            found.extend(_literal_tool_names(node))
        for name in set(found):
            hits.setdefault(name, []).append(rel)
    return tuple((n, tuple(files)) for n, files in sorted(hits.items()))


def discover_behavioral_dispatch(
    root: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """{tool_name: [真实 dispatch 它的测试文件（升序）]}（进程内 memo）。"""
    base = (root if root is not None else repo_root()).resolve()
    cached = _behavioral_cached(str(base))
    return {name: list(files) for name, files in cached}


def behavior_level(
    name: str,
    behavioral: Dict[str, List[str]],
    static_refs: List[str],
) -> str:
    """工具名 → 行为证据等级（BEHAVIOR_LEVELS 词表）。"""
    if behavioral.get(name):
        return "dispatch"
    if static_refs:
        return "mention"
    return "none"
