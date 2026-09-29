"""时钟纪律门禁（DoD：新代码禁裸 utcnow / 无时区 datetime.now）。

范围 = 本方向新增模块（turn_journal 全包 + clock + harness_journal 模型 +
kernel 接线点）。全库存量 naive utcnow 是 issue #1551 的独立清偿方向，
不在此 gate 范围（避免把本 PR 变成全库大扫除）。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

GATED_PATHS = [
    "app/services/turn_journal",
    "app/lib/runtime/clock.py",
    "app/models/harness_journal.py",
]

BANNED_CALLS = {"utcnow", "utcfromtimestamp"}


def _py_files():
    for rel in GATED_PATHS:
        root = REPO / rel
        if root.is_file():
            yield root
        else:
            yield from sorted(root.rglob("*.py"))


def test_gated_modules_have_no_naive_utc_construction() -> None:
    offenders: list[str] = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", "") if isinstance(func, ast.Attribute) \
                else getattr(func, "id", "")
            owner = ""
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
                owner = func.value.attr
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = func.value.id
            if name in BANNED_CALLS and owner == "datetime":
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} "
                                 f"datetime.{name}()")
            if name == "now" and owner == "datetime" and len(node.args) == 0:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} "
                                 f"datetime.now() without tz")
    assert offenders == [], "新模块禁止裸 naive UTC 构造:\n" + "\n".join(offenders)


def test_gated_surface_really_covered() -> None:
    """防呆：gate 清单失效（目录被改名/搬走）时本测试必须炸。"""
    files = list(_py_files())
    assert len(files) >= 8  # turn_journal 6 文件 + clock + harness_journal
    assert any(p.name == "clock.py" for p in files)
    assert any(p.name == "sink.py" for p in files)
