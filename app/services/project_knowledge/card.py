"""ProjectContextCard：项目知识的严格预算 LLM 读模型（D5）。

预算：渲染 ≤ ``CARD_CHAR_BUDGET``（1600）字符、≤ ``CARD_MAX_ITEMS``（12）
条、失败警告 ≤ ``CARD_FAILURE_WARNING_MAX``（3）行；内容只有 ids/摘要/
ref 指针 —— 无 payload、无 CoT；不可信字符串一律 ``_xml_fence`` 转义；
单行渲染失败只跳过该行（fail-open per line，与 ``[GIS_MEMORY]`` 同纪律）；
空项目返回空串（绝不注入空块）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.services.chat.context.formatters import _xml_fence
from app.services.project_knowledge.contract import (
    CARD_CHAR_BUDGET,
    CARD_FAILURE_WARNING_MAX,
    CARD_MAX_ITEMS,
    CARD_SUMMARY_MAX,
    EK_ARTIFACT,
    EK_DATASET_VERSION,
    EK_FAILURE_PATTERN,
    EK_MAP_PRODUCT,
    EK_MISSION,
    EK_PLACE,
    EK_PREFERENCE,
    EK_WORKFLOW,
    KnowledgeEntry,
    ST_ACTIVE,
)

logger = logging.getLogger(__name__)

_TAG_UNTRUSTED_PROJECT_KNOWLEDGE = "untrusted_project_knowledge"
_MARKER = "PROJECT_KNOWLEDGE"

#: 实体类别的渲染标签（closed，字典序稳定的分组顺序）。
_KIND_LABELS = {
    EK_DATASET_VERSION: "数据集",
    EK_ARTIFACT: "产物",
    EK_MAP_PRODUCT: "地图成品",
    EK_WORKFLOW: "工作流",
    EK_MISSION: "Mission",
    EK_PLACE: "区域",
    EK_PREFERENCE: "偏好",
}
_KIND_ORDER: Tuple[str, ...] = (
    EK_DATASET_VERSION, EK_ARTIFACT, EK_MAP_PRODUCT, EK_WORKFLOW,
    EK_MISSION, EK_PLACE, EK_PREFERENCE,
)


@dataclass(frozen=True)
class ProjectKnowledgeCard:
    """渲染结果（调用方可观测预算与截断事实）。"""

    project_id: str
    text: str
    items: int
    truncated: bool
    omitted: int
    char_budget: int

    @property
    def chars(self) -> int:
        return len(self.text)


def _fence(v: object) -> str:
    return _xml_fence(_TAG_UNTRUSTED_PROJECT_KNOWLEDGE, v, max_len=CARD_SUMMARY_MAX)


def _render_entry_line(entry: KnowledgeEntry) -> str:
    """单条目一行：kind · subject — verdictable scope · authority 指针。"""
    scope_bits: List[str] = []
    if entry.bbox:
        scope_bits.append("AOI✓")
    if entry.temporal_label:
        scope_bits.append(f"T={_fence(entry.temporal_label)}")
    if entry.method_key:
        scope_bits.append("method✓")
    scope = f"[{' '.join(scope_bits)}]" if scope_bits else ""
    authority = f"{entry.authority_store}:{entry.authority_id}"
    subject = _fence(entry.subject)
    return f"- {subject} {scope} → {authority}".rstrip()


def _render_warning_line(entry: KnowledgeEntry) -> str:
    """失败警告行：error_code + 有界摘要 + mission 回指。"""
    detail = _fence(entry.summary or entry.subject)
    authority = f"{entry.authority_store}:{entry.authority_id}"
    code = _fence(entry.subject)
    return f"- ⚠ {code}：{detail} → {authority}"


def render_project_knowledge_card(
    entries: List[KnowledgeEntry],
    *,
    project_id: str,
    project_name: str = "",
    char_budget: int = CARD_CHAR_BUDGET,
    max_items: int = CARD_MAX_ITEMS,
    now: Optional[object] = None,
) -> ProjectKnowledgeCard:
    """渲染有界 ``<project_knowledge>`` 块；空条目返回空 text。

    分组稳定（datasets → artifacts → …），失败警告独立且行数有界；
    超预算截断 + 省略回执（诚实省略）。``now`` 参数保留渲染确定性测试用。
    """
    if not entries:
        return ProjectKnowledgeCard(
            project_id=project_id, text="", items=0,
            truncated=False, omitted=0, char_budget=char_budget,
        )

    header = (
        f"<project_knowledge project={_fence(project_name or project_id)}>\n"
        "项目知识与可复用资产索引（ids/指针，不是证据本身；权威源以 → 后为准）：\n"
    )
    ellipsis = "- …（更多条目已按预算省略）\n"
    footer = "</project_knowledge>\n"

    warnings = [e for e in entries if e.entity_kind == EK_FAILURE_PATTERN]
    normal = [
        e for e in entries
        if e.entity_kind != EK_FAILURE_PATTERN and e.status == ST_ACTIVE
    ]
    normal.sort(key=lambda e: (
        _KIND_ORDER.index(e.entity_kind)
        if e.entity_kind in _KIND_ORDER else len(_KIND_ORDER),
        e.id,
    ))

    body: List[str] = []
    used = len(header) + len(footer) + len(ellipsis)
    items = 0
    omitted = 0
    current_kind: Optional[str] = None

    def _try_line(line: str) -> bool:
        nonlocal used, items, omitted
        if items >= max_items or used + len(line) + 1 > char_budget:
            omitted += 1
            return False
        body.append(line + "\n")
        used += len(line) + 1
        items += 1
        return True

    for entry in normal:
        if entry.entity_kind != current_kind:
            current_kind = entry.entity_kind
            label = _KIND_LABELS.get(current_kind, current_kind)
            count = sum(1 for e in normal if e.entity_kind == current_kind)
            section = f"[{label} ×{count}]\n"
            if used + len(section) <= char_budget and items < max_items:
                body.append(section)
                used += len(section)
        try:
            line = _render_entry_line(entry)
        except Exception:  # noqa: BLE001 — 单行失败不炸整块
            continue
        if not _try_line(line):
            # 不 break：变长行下后续条目未必超限，逐条如实计数（review P3-7）。
            continue

    # 失败警告是独立面：装不下也要进省略回执（review P2-2 —— 静默丢弃
    # 却报告"未截断"不诚实）。超出 CARD_FAILURE_WARNING_MAX 的警告同样计数。
    bounded_warnings = warnings[:CARD_FAILURE_WARNING_MAX]
    skipped_warnings = len(warnings) - len(bounded_warnings)
    if warnings:
        section = f"[失败经验 ×{len(warnings)}（bounded warnings，可作风险先验）]\n"
        can_afford_section = (
            used + len(section) <= char_budget and items < max_items
        )
        if can_afford_section:
            body.append(section)
            used += len(section)
            for entry in bounded_warnings:
                try:
                    line = _render_warning_line(entry)
                except Exception:  # noqa: BLE001
                    continue
                _try_line(line)
        else:
            # section 都装不下：全部警告进省略回执。
            skipped_warnings += len(warnings)
    omitted += skipped_warnings

    if omitted:
        body.append(ellipsis)
    text = header + "".join(body)
    if text and not text.endswith("\n"):
        text += "\n"
    if body:
        text += footer
    return ProjectKnowledgeCard(
        project_id=project_id, text=text, items=items,
        truncated=omitted > 0, omitted=omitted, char_budget=char_budget,
    )


__all__ = ["ProjectKnowledgeCard", "render_project_knowledge_card"]
