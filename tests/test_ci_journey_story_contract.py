"""The real J4 seed must survive the production StoryMap compiler.

The story page briefly shows message-derived chapters before the server's
compiled view replaces them. A heading present only in that transient view
must not make the journey pass.
"""
import json
from pathlib import Path

from app.lib.storymap.story_compiler import compile_story_map


def test_real_story_seed_keeps_both_headings_in_compiled_chapters():
    root = Path(__file__).resolve().parents[1]
    turns = json.loads((root / "frontend/e2e/fixtures/story-seed.json").read_text())
    messages = [
        message
        for turn in turns
        for message in (
            {"role": "user", "content": turn["message"]},
            {"role": "assistant", "content": turn["content"]},
        )
    ]
    spec = compile_story_map(messages=messages)
    assert [chapter.arc_role for chapter in spec.chapters] == [
        "introduction", "recommendation",
    ]
    assert "## 商业 POI 空间分布" in spec.chapters[0].narrative.splitlines()
    assert "## 15 分钟等时圈" in spec.chapters[1].narrative.splitlines()
