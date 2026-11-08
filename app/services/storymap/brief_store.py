"""G11 —— 简报持久化与编辑操作（BRF-03/BRF-07）。

存储壳：``map_briefs`` 行 + 乐观并发（``expected_revision`` 不匹配 →
RevisionConflict，绝不无声吞掉人工内容）；所有编辑操作在**校验后的
MapBriefSpec** 上进行（不存半合法载荷），revision 单调 +1。

重生成纪律（BRF-O03/O11）：manual 块与 locked 块永不覆盖；删除的用户
块不复活（regenerate 以现存块为准合并）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.storymap.brief_spec import (
    MAX_BRIEF_BLOCKS,
    BriefBlock,
    MapBriefSpec,
)
from app.models.db_model import Conversation
from app.models.map_brief import MapBrief

#: spec 序列化上限（与 story asset 同预算）。
MAX_BRIEF_SPEC_BYTES = 512 * 1024


class BriefNotFound(Exception):
    """简报不存在或不属于该会话（404 语义，防存在性泄露）。"""


class RevisionConflict(Exception):
    """乐观并发冲突（409 语义：expected_revision != 当前 revision）。"""


class BriefCorrupt(Exception):
    """已存载荷损坏（422 语义，不伪造默认）。"""


async def create_brief(
    db: AsyncSession, conv: Conversation, spec: MapBriefSpec
) -> Dict[str, Any]:
    """创建新简报（会话内唯一 —— 已存在则冲突提示替换语义）。"""
    existing = await db.scalar(select(MapBrief).where(
        MapBrief.conversation_id == conv.id))
    if existing is not None:
        raise RevisionConflict(
            "brief already exists for this session; use PATCH to edit")
    payload = spec.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_BRIEF_SPEC_BYTES:
        raise ValueError(f"brief spec exceeds {MAX_BRIEF_SPEC_BYTES} bytes")
    row = MapBrief(
        conversation_id=conv.id,
        title=(spec.metadata.title or "地图简报")[:255],
        spec=text, revision=1)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # 并发双创建窗口（uq_mb_conversation）：回滚本帧后按冲突语义
        # 返回，绝不留半行。
        await db.rollback()
        raise RevisionConflict(
            "brief already exists for this session; use PATCH to edit")
    return {"brief_id": row.id, "revision": 1, "title": row.title}


async def load_brief(
    db: AsyncSession, conv: Conversation
) -> MapBriefSpec:
    """读会话简报（MapBriefSpec）。无简报 → BriefNotFound。"""
    row = await db.scalar(select(MapBrief).where(
        MapBrief.conversation_id == conv.id))
    if row is None:
        raise BriefNotFound("no brief for this session")
    try:
        payload = json.loads(row.spec)
    except json.JSONDecodeError as exc:
        raise BriefCorrupt("stored brief payload is corrupt") from exc
    try:
        spec = MapBriefSpec.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — 损坏载荷显式失败
        raise BriefCorrupt(f"stored brief invalid: {exc}") from exc
    return spec


async def brief_row(db: AsyncSession, conv: Conversation) -> MapBrief:
    row = await db.scalar(select(MapBrief).where(
        MapBrief.conversation_id == conv.id))
    if row is None:
        raise BriefNotFound("no brief for this session")
    return row


async def save_brief(
    db: AsyncSession,
    conv: Conversation,
    spec: MapBriefSpec,
    *,
    expected_revision: int,
) -> Dict[str, Any]:
    """乐观并发保存：expected_revision 不匹配 → RevisionConflict。

    校验失败（ValueError/ValidationError）向上抛（路由映射 422）。
    """
    payload = spec.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_BRIEF_SPEC_BYTES:
        raise ValueError(f"brief spec exceeds {MAX_BRIEF_SPEC_BYTES} bytes")
    # 原子乐观并发（评审 #P2-5）：条件 UPDATE 以 revision 为 CAS 令牌，
    # rowcount=0 即并发写入已推进 —— 先检查存在性再区分 404/409。
    # 空 title 保留行内现有标题（复审 N4：不回退字面量默认值）。
    from sqlalchemy import update

    current_title = await db.scalar(select(MapBrief.title).where(
        MapBrief.conversation_id == conv.id))
    result = await db.execute(
        update(MapBrief)
        .where(MapBrief.conversation_id == conv.id,
               MapBrief.revision == expected_revision)
        .values(spec=text,
                title=(spec.metadata.title or current_title or "地图简报")[:255],
                revision=expected_revision + 1)
        .execution_options(synchronize_session=False))
    if not result.rowcount:
        current = await db.scalar(select(MapBrief.revision).where(
            MapBrief.conversation_id == conv.id))
        if current is None:
            raise BriefNotFound("no brief for this session")
        raise RevisionConflict(
            f"brief revision changed: expected {expected_revision}, "
            f"current {current}")
    row = await brief_row(db, conv)
    return {"brief_id": row.id, "revision": row.revision, "title": row.title}


# ── 块级编辑操作（纯函数：spec → 新 spec；持久化由 save_brief 承担）──


def add_block(spec: MapBriefSpec, block: Mapping[str, Any]) -> MapBriefSpec:
    """新增块。order 缺省=末尾；不与现存块 ID 冲突。"""
    blocks = list(spec.blocks)
    new_block = BriefBlock.model_validate(dict(block))
    if any(b.id == new_block.id for b in blocks):
        raise ValueError(f"block id already exists: {new_block.id}")
    if len(blocks) + 1 > MAX_BRIEF_BLOCKS:
        raise ValueError(f"brief block budget exceeded ({MAX_BRIEF_BLOCKS})")
    if new_block.order == 0:
        max_order = max((b.order for b in blocks), default=0)
        new_block = new_block.model_copy(update={"order": max_order + 1})
    blocks.append(new_block)
    return spec.model_copy(update={"blocks": blocks})


def update_block(
    spec: MapBriefSpec,
    block_id: str,
    patch: Mapping[str, Any],
) -> MapBriefSpec:
    """更新块。locked 块的 text/kind/fact_id/widget_id/map_view 不可改
    （锁定即冻结内容；解锁后可改）。origin=manual 的块不会被重生成覆盖，
    但用户显式编辑仍允许。"""
    editable = {"text", "locked", "order", "chapter_id", "origin",
                "map_view", "fact_id", "widget_id", "binding"}
    unknown = set(patch) - editable
    if unknown:
        raise ValueError(f"non-editable block fields: {sorted(unknown)}")
    blocks = list(spec.blocks)
    for i, block in enumerate(blocks):
        if block.id != block_id:
            continue
        locked_fields = {"text", "fact_id", "widget_id", "map_view"}
        if block.locked and locked_fields & set(patch):
            raise ValueError(
                f"block {block_id} is locked; unlock before editing content")
        updates = {k: v for k, v in patch.items()}
        if "map_view" in updates and isinstance(updates["map_view"], Mapping):
            updates["map_view"] = dict(updates["map_view"])
        updated = block.model_copy(update=updates)
        blocks[i] = updated
        return spec.model_copy(update={"blocks": blocks})
    raise BriefNotFound(f"block {block_id} not found")


def delete_block(spec: MapBriefSpec, block_id: str) -> MapBriefSpec:
    blocks = [b for b in spec.blocks if b.id != block_id]
    if len(blocks) == len(spec.blocks):
        raise BriefNotFound(f"block {block_id} not found")
    return spec.model_copy(update={"blocks": blocks})


def add_chapter(
    spec: MapBriefSpec, chapter: Mapping[str, Any]
) -> MapBriefSpec:
    """新增章节（含可选 keyframe）。"""
    from app.lib.storymap.spec import CameraKeyframe, StoryChapter

    chapters = list(spec.chapters)
    new_chapter = StoryChapter.model_validate(dict(chapter))
    if any(c.id == new_chapter.id for c in chapters):
        raise ValueError(f"chapter id already exists: {new_chapter.id}")
    chapters.append(new_chapter)
    keyframes = list(spec.camera_keyframes)
    kf = dict(chapter.get("keyframe") or {})
    if kf:
        kf["chapter_id"] = new_chapter.id
        keyframes.append(CameraKeyframe.model_validate(kf))
    return spec.model_copy(update={
        "chapters": chapters, "camera_keyframes": keyframes})


def update_chapter(
    spec: MapBriefSpec, chapter_id: str, patch: Mapping[str, Any]
) -> MapBriefSpec:
    """更新章节（title/narrative/arc_role/顺序 via order map）。"""
    editable = {"title", "narrative", "arc_role", "order"}
    unknown = set(patch) - editable
    if unknown:
        raise ValueError(f"non-editable chapter fields: {sorted(unknown)}")
    chapters = list(spec.chapters)
    for i, chapter in enumerate(chapters):
        if chapter.id == chapter_id:
            updates = {k: v for k, v in patch.items() if k != "order"}
            chapters[i] = chapter.model_copy(update=updates)
            break
    else:
        raise BriefNotFound(f"chapter {chapter_id} not found")
    # order：显式给出全量顺序映射 {chapter_id: 位置}
    order_map = patch.get("order")
    if isinstance(order_map, Mapping):
        def _key(c):
            return int(order_map.get(c.id, len(chapters)))
        chapters.sort(key=_key)
    return spec.model_copy(update={"chapters": chapters})


def delete_chapter(spec: MapBriefSpec, chapter_id: str) -> MapBriefSpec:
    chapters = [c for c in spec.chapters if c.id != chapter_id]
    if not chapters:
        raise ValueError("cannot delete the last chapter")
    if len(chapters) == len(spec.chapters):
        raise BriefNotFound(f"chapter {chapter_id} not found")
    blocks = [b for b in spec.blocks if b.chapter_id != chapter_id]
    keyframes = [k for k in spec.camera_keyframes
                 if k.chapter_id != chapter_id]
    return spec.model_copy(update={
        "chapters": chapters, "blocks": blocks,
        "camera_keyframes": keyframes})


def merge_regenerated(
    current: MapBriefSpec, fresh: MapBriefSpec,
    *, mode: str = "metrics_only",
) -> MapBriefSpec:
    """重生成合并（BRF-O03/O11）：manual 块与 locked 块永不覆盖。

    mode:
    - ``metrics_only``：只刷新 auto 未锁 metric/chart/table/map_view 块
      与 facts（人工文字/结构原样保留）；
    - ``unlocked_text_only``：只刷新 auto 未锁 text/method/limitation 块
      （指标不动）。
    手工删除的块不复活：合并以 current 的块清单为基准，fresh 只提供
    同 ID/同角色的新内容。
    """
    content_kinds = {
        "metrics_only": {"metric", "chart", "table", "map_view"},
        "unlocked_text_only": {"text", "method", "limitation"},
    }.get(mode)
    if content_kinds is None:
        raise ValueError(f"unknown regenerate mode: {mode}")

    fresh_blocks = {b.id: b for b in fresh.blocks}
    merged_blocks: List[BriefBlock] = []
    for block in current.blocks:
        if (block.origin == "manual" or block.locked
                or block.kind not in content_kinds):
            merged_blocks.append(block)
            continue
        replacement = fresh_blocks.get(block.id)
        if replacement is None or replacement.origin == "manual":
            # fresh 里没有同 ID 的自动块（结构变化）→ 保留现块并标注
            # 待更新（stale 由 stale 计算披露，不在此改内容）。
            merged_blocks.append(block)
            continue
        merged_blocks.append(replacement.model_copy(update={
            "locked": False, "origin": "auto",
        }))

    # facts：manual 事实保留；auto 事实以 fresh 的核验结果替换（同 ID）。
    fresh_facts = {f.fact_id: f for f in fresh.facts}
    merged_facts = []
    for fact in current.facts:
        if fact.origin == "manual":
            merged_facts.append(fact)
            continue
        replacement = fresh_facts.get(fact.fact_id)
        merged_facts.append(replacement if replacement is not None else fact)

    return current.model_copy(update={
        "blocks": merged_blocks, "facts": merged_facts,
        "provenance": fresh.provenance,
    })


def compute_stale(
    spec: MapBriefSpec,
    *,
    current_records: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """BRF-07：产物更新后哪些自动块/事实需要更新（标记，不改内容）。"""
    stale: List[Dict[str, Any]] = []
    for block in spec.blocks:
        binding = block.binding
        if block.origin != "auto" or block.locked or binding is None:
            continue
        ref = binding.artifact_ref
        if not ref:
            continue
        record = current_records.get(ref)
        if record is None:
            stale.append({"block_id": block.id, "reason": "artifact_missing"})
            continue
        import hashlib as _h

        digest = _h.sha256(json.dumps(
            record, ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
        if binding.artifact_sha256 and digest != binding.artifact_sha256:
            stale.append({"block_id": block.id,
                          "reason": "artifact_updated"})
    for fact in spec.facts:
        if fact.origin != "auto":
            continue
        ref = fact.basis.artifact_ref
        record = current_records.get(ref) if ref else None
        if ref and record is None:
            stale.append({"fact_id": fact.fact_id,
                          "reason": "artifact_missing"})
    return stale


__all__ = [
    "MAX_BRIEF_SPEC_BYTES",
    "BriefNotFound",
    "RevisionConflict",
    "BriefCorrupt",
    "create_brief",
    "load_brief",
    "brief_row",
    "save_brief",
    "add_block",
    "update_block",
    "delete_block",
    "add_chapter",
    "update_chapter",
    "delete_chapter",
    "merge_regenerated",
    "compute_stale",
]
