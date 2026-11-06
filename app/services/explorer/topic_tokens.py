"""Bounded, dependency-free topic matching shared by Explorer heuristics."""
from app.services.data_fabric.retrieval.keyword import tokenize

_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "show", "find", "data", "list",
    "请问", "帮我", "查询", "查找", "显示", "数据", "图层", "列表", "名录", "分布",
})


def topic_tokens(text: str) -> set[str]:
    """Reuse the acquisition tokenizer; discard short/boilerplate tokens."""
    return {token for token in tokenize(text[:4096]) if len(token) >= 2 and token not in _STOP_WORDS}
