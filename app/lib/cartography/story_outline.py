"""Story Outline — StoryMap 章节自动编排（V11 W5.4，ADR-0165）。

把会话过程（问题 → 数据 → 分析 → 成图）自动编排为故事页大纲（复用既有
``/story`` 页面语义，ADR-0147 storymap chapters）。确定性：章节序、标题、
要点全部由会话事实投影；缺事实的章节诚实缺省（不虚构叙事）。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 章节骨架（叙事四幕；与 /story 的 chapter 词汇对齐）。
_CHAPTER_SKELETON = (
    ("question", "问题", "本图回答什么"),
    ("data", "数据", "数据从哪里来"),
    ("analysis", "分析", "做了什么处理"),
    ("map", "成图", "结论与读图指引"),
)


def story_outline_from_session(
    facts: Dict[str, Any],
    *,
    title: str = "",
) -> Dict[str, Any]:
    """会话事实 → 故事大纲（确定性、可序列化）。

    ``facts`` 形状（全部可选，缺章节如实标 missing）：``{question,
    dataSources: [..], analysisSteps: [..], mapTitle, mapSpecId,
    takeaways: [..]}``。
    """
    chapters: List[Dict[str, Any]] = []
    for key, label, hint in _CHAPTER_SKELETON:
        if key == "question":
            body = [str(facts.get("question"))] if facts.get("question") else []
        elif key == "data":
            body = [str(x) for x in (facts.get("dataSources") or [])]
        elif key == "analysis":
            body = [str(x) for x in (facts.get("analysisSteps") or [])]
        else:
            body = [str(x) for x in (facts.get("takeaways") or [])]
            if facts.get("mapTitle"):
                body.insert(0, f"成图：{facts['mapTitle']}")
        chapters.append({
            "key": key, "label": label, "hint": hint,
            "points": body[:8],  # 有界
            "missing": not body,
        })
    return {
        "version": 1,
        "title": title or str(facts.get("mapTitle") or facts.get("question") or "地图故事"),
        "mapSpecId": facts.get("mapSpecId"),
        "chapters": chapters,
        "complete": all(not c["missing"] for c in chapters),
    }


__all__ = ["story_outline_from_session"]
