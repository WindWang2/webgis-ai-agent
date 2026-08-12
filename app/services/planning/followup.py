"""Deterministic follow-up message classification (design-v3 §followup).

No LLM. A follow-up user message is classified into a ``FollowUpKind`` from
small keyword lists plus session state (active plan, active domains, whether
the session already holds data refs). Feeds ``should_plan`` and the supersede
logic in the integration slice. Keyword lists are intentionally small,
data-driven and unit-testable.
"""
import re
from enum import Enum

# Style-change signals: visual / cartographic tweaks to the current result.
STYLE_KEYWORDS = [
    "颜色", "蓝色", "红色", "绿色", "黄色", "样式", "风格", "透明", "大小",
    "粗细", "标注", "配色", "加粗", "图例", "标签",
    "color", "style", "size", "opacity", "label", "legend", "bold",
]

# Continue-the-current-plan signals (only meaningful with an active plan).
CONTINUATION_KEYWORDS = [
    "继续", "接着", "继续分析", "继续做", "continue", "next", "keep going",
]

# Reference-back-to-prior-results signals (only meaningful when the session
# already holds data refs). Deliberately phrase-based ("这个结果"), so a bare
# 再 / 那个 in a new request does not hijack the classifier.
REF_REUSE_KEYWORDS = [
    "刚才", "上一个", "那个", "该结果", "这个结果", "上次", "前一个",
    "之前的结果", "上面的", "this result", "that result", "previous result",
]


class FollowUpKind(str, Enum):
    """Classification of a follow-up message (design-v3 §followup)."""

    new_goal = "new_goal"
    style_change = "style_change"
    ref_reuse = "ref_reuse"
    continuation = "continuation"
    unclear = "unclear"


def _kw_in(kw: str, text: str) -> bool:
    """CJK keywords match as substrings; ASCII keywords match on word boundary."""
    if kw.isascii():
        return re.search(rf"\b{re.escape(kw.lower())}\b", text) is not None
    return kw in text


def _has_any_keyword(keywords: list[str], text: str) -> bool:
    return any(_kw_in(kw, text) for kw in keywords)


def classify_followup(
    message: str,
    *,
    has_active_plan: bool,
    active_domains: list[str],
    session_has_refs: bool,
    domain_keywords: dict[str, list[str]],
) -> FollowUpKind:
    """Classify a follow-up message deterministically.

    Rules, in priority order:

    1. style keyword AND no new-domain keyword    → style_change
    2. ref-reuse keyword AND ``session_has_refs`` → ref_reuse
    3. continuation keyword AND active plan       → continuation
    4. domain keyword outside ``active_domains``  → new_goal
    5. otherwise                                  → unclear

    A "new domain keyword" is a keyword from ``domain_keywords`` whose domain
    is not in ``active_domains`` — style tweaks over the already-active domain
    stay style_change (e.g. 热力图换成蓝色 with statistics active).
    """
    text = (message or "").lower()
    active = set(active_domains or [])

    has_new_domain = False
    for domain, keywords in (domain_keywords or {}).items():
        if domain in active:
            continue
        if _has_any_keyword(keywords, text):
            has_new_domain = True
            break

    if _has_any_keyword(STYLE_KEYWORDS, text) and not has_new_domain:
        return FollowUpKind.style_change
    if session_has_refs and _has_any_keyword(REF_REUSE_KEYWORDS, text):
        return FollowUpKind.ref_reuse
    if has_active_plan and _has_any_keyword(CONTINUATION_KEYWORDS, text):
        return FollowUpKind.continuation
    if has_new_domain:
        return FollowUpKind.new_goal
    return FollowUpKind.unclear
