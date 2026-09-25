"""截图存储与 ref-only 纪律（F15/ADR-0214 决策六）。

PNG 魔数 + ≤4 MiB 校验 → sha256 → 内容寻址 blob（``vshot-<sha>``，复用
晋升内容库根，天然去重）；会话索引 ``map_state["_visual_screenshots"]``
（≤8 FIFO）是 ref/sha/revision 级摘要——trace/journal/map_product 只允许
这一级细节，字节只在 :func:`resolve_visual_screenshot` 的评估瞬间进内存。

保留：索引 FIFO 淘汰即删 blob（fail-open——清理失败不影响评估/终验）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 会话索引 map_state 键（旧读者忽略的 `_` 前缀私有键，零漂移）。
SCREENSHOT_INDEX_KEY = "_visual_screenshots"

#: 有界纪律：单会话保留截图数（FIFO）；大小上限与 ADR-0158 证据图同宽。
MAX_SCREENSHOTS_PER_SESSION = 8
MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ScreenshotRejected(ValueError):
    """截图入库前的确定性初筛失败（typed；路由层映射 400）。"""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(f"visual screenshot rejected: {self.reason}")


@dataclass(frozen=True)
class ScreenshotEntry:
    """索引条目（ref-only 摘要；可序列化）。"""

    ref: str
    sha256: str
    size: int
    width: int = 0
    height: int = 0
    mapspec_revision: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref[:96],
            "sha256": self.sha256[:64],
            "size": int(self.size),
            "width": int(self.width),
            "height": int(self.height),
            "mapspec_revision": int(self.mapspec_revision),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["ScreenshotEntry"]:
        if not isinstance(raw, dict):
            return None
        ref = str(raw.get("ref") or "")
        sha = str(raw.get("sha256") or "")
        if not ref or not sha:
            return None
        try:
            return cls(
                ref=ref[:96], sha256=sha[:64], size=int(raw.get("size") or 0),
                width=int(raw.get("width") or 0),
                height=int(raw.get("height") or 0),
                mapspec_revision=int(raw.get("mapspec_revision") or 0),
            )
        except (TypeError, ValueError):
            return None


def validate_screenshot_bytes(data: bytes) -> Tuple[int, int]:
    """确定性初筛：PNG 魔数 + 大小上限。返回 (size, 0)（宽高由解码面回填）。

    与 ADR-0185 SnapshotExtractor 的差别：这里是**入库前**的廉价门（无
    Pillow 依赖、零解码）；解码/像素初筛归评估器。
    """
    size = len(data)
    if size == 0:
        raise ScreenshotRejected("empty")
    if size > MAX_SCREENSHOT_BYTES:
        raise ScreenshotRejected("oversized")
    if not data.startswith(_PNG_MAGIC):
        raise ScreenshotRejected("not_png")
    return size, 0


def blob_key_for(sha256: str) -> str:
    """内容寻址 blob 键（``safe_blob_key`` 合法：无路径分隔符）。"""
    return f"vshot-{str(sha256 or '').strip()[:64]}"


async def register_visual_screenshot(
    session_id: str,
    data: bytes,
    *,
    mapspec_revision: int = 0,
    width: int = 0,
    height: int = 0,
) -> ScreenshotEntry:
    """入库（内容寻址）+ 会话索引 FIFO 推进；返回 ref-only 条目。"""
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.durable_blob_store import sha256_of_bytes
    from app.services.session_data import session_data_manager

    size, _ = validate_screenshot_bytes(data)
    sha = sha256_of_bytes(data)
    key = blob_key_for(sha)
    store = get_filesystem_blob_store()
    if not store.exists(key):
        store.put_blob(key, data, "binary")

    entry = ScreenshotEntry(
        ref=key, sha256=sha, size=size, width=int(width),
        height=int(height), mapspec_revision=int(mapspec_revision),
    )
    index = await load_screenshot_index(session_id)
    # 内容寻址去重：同 sha 刷新位置（最新优先），不重复占 FIFO 槽。
    entries = [e for e in index if e.sha256 != sha]
    entries.append(entry)
    evicted: List[ScreenshotEntry] = []
    while len(entries) > MAX_SCREENSHOTS_PER_SESSION:
        evicted.append(entries.pop(0))
    await session_data_manager.set_map_state(
        session_id, SCREENSHOT_INDEX_KEY,
        [e.to_dict() for e in entries],
    )
    for old in evicted:
        _prune_blob(old.ref, keep=entry.ref)
    return entry


async def load_screenshot_index(session_id: str) -> List[ScreenshotEntry]:
    """读会话索引（畸形条目诚实跳过；异常 → 空索引）。"""
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        entries = (raw or {}).get(SCREENSHOT_INDEX_KEY)
        if not isinstance(entries, list):
            return []
        out = []
        for item in entries[:MAX_SCREENSHOTS_PER_SESSION * 2]:
            e = ScreenshotEntry.from_dict(item)
            if e is not None:
                out.append(e)
        return out
    except Exception:  # noqa: BLE001 — 索引缺席 = 无截图（诚实缺席）
        return []


async def latest_screenshot_for(
    session_id: str, mapspec_revision: int
) -> Optional[ScreenshotEntry]:
    """revision 精确优先，退回最新一条（观察可滞后 revision 一拍）。"""
    entries = await load_screenshot_index(session_id)
    if not entries:
        return None
    for entry in reversed(entries):
        if entry.mapspec_revision == int(mapspec_revision):
            return entry
    return entries[-1]


def resolve_visual_screenshot(entry: ScreenshotEntry) -> Optional[bytes]:
    """ref → 字节（评估瞬间；sha 校验 + 大小护栏；失败 → None 诚实缺席）。"""
    from app.services.durable_blob_store import get_filesystem_blob_store

    try:
        store = get_filesystem_blob_store()
        if not store.exists(entry.ref):
            return None
        data = store.get_blob(entry.ref, expected_sha256=entry.sha256 or None)
        if data is None or len(data) > MAX_SCREENSHOT_BYTES:
            return None
        return data
    except Exception:  # noqa: BLE001 — 解析失败 = 无截图（不阻断评估链）
        logger.warning("[VisualStore] screenshot resolve failed ref=%s",
                       entry.ref[:24], exc_info=True)
        return None


def _prune_blob(ref: str, *, keep: str = "") -> None:
    """淘汰 blob（fail-open；内容寻址下同 sha 复用不删）。"""
    if not ref or ref == keep:
        return
    try:
        from app.services.durable_blob_store import get_filesystem_blob_store

        get_filesystem_blob_store().delete_blob(ref)
    except Exception:  # noqa: BLE001 — 清理失败不影响主流程
        logger.debug("[VisualStore] prune failed ref=%s", ref[:24],
                     exc_info=True)


__all__ = [
    "SCREENSHOT_INDEX_KEY",
    "MAX_SCREENSHOTS_PER_SESSION",
    "MAX_SCREENSHOT_BYTES",
    "ScreenshotRejected",
    "ScreenshotEntry",
    "validate_screenshot_bytes",
    "blob_key_for",
    "register_visual_screenshot",
    "load_screenshot_index",
    "latest_screenshot_for",
    "resolve_visual_screenshot",
]
