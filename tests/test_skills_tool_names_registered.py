"""#583: 运行时技能文档（app/skills/*.md）引用的工具名必须全部存在于 registry。

disaster_risk / site_selection / urban_planning 三个内置技能文档引用了
registry 中不存在的工具名（terrain_analysis / ndvi_analysis /
generate_report / spatial_query）。技能激活后该文档作为 system 指令注入，
LLM 按步骤调用死工具，收官步骤（生成报告）结构性失败（UNKNOWN_TOOL）。

本测试把 app/skills/*.md 中的反引号工具名与 registry 活体集合
（+ LEGACY_TOOL_NAME_MAP 别名）绑定，防止文档↔registry 双头维护再次
漂移 —— 与 #438 / #516 / #556 的 live-vocabulary 契约思路一致。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.tool_dispatch_service import LEGACY_TOOL_NAME_MAP

SKILLS_DIR = Path(__file__).resolve().parents[1] / "app" / "skills"

# #583 扫掉的四个死工具名，绝不允许回归。
DEAD_SKILL_TOOL_NAMES = {
    "terrain_analysis",
    "ndvi_analysis",
    "generate_report",
    "spatial_query",
}


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    return r


def _known_names(registry) -> set[str]:
    return set(registry.all_metadata().keys()) | set(LEGACY_TOOL_NAME_MAP.keys())


def _backticked_skill_references() -> list[tuple[str, int, str]]:
    """(file, line_no, backticked identifier) for every `tool_name` in skill docs."""
    md_files = sorted(SKILLS_DIR.glob("*.md"))
    refs: list[tuple[str, int, str]] = []
    for path in md_files:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for seg in re.findall(r"`([^`]+)`", line):
                for ident in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", seg):
                    if "_" not in ident:
                        continue
                    refs.append((path.name, line_no, ident))
    return refs


def test_skills_tool_names_registered(registry):
    known = _known_names(registry)
    refs = _backticked_skill_references()
    assert refs, f"no backticked tool-name references found under {SKILLS_DIR}"
    violations = [
        f"{path}:{line_no}: `{ident}`"
        for path, line_no, ident in refs
        if ident not in known
    ]
    assert not violations, (
        "Skill docs reference tool names missing from the live registry / "
        "legacy alias map (LLM following them hits UNKNOWN_TOOL):\n"
        + "\n".join(violations)
    )


def test_swept_dead_skill_tool_names_never_reappear():
    for path in sorted(SKILLS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for name in DEAD_SKILL_TOOL_NAMES:
            assert re.search(rf"`{re.escape(name)}`", text) is None, (
                f"{path.name} 重新引用了 #583 已移除的死工具名 {name!r}"
            )