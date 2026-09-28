"""截图存储与 ref-only 纪律（F15/ADR-0214 决策六 + C13 blob lifecycle）。

PNG 魔数 + ≤4 MiB 校验 → sha256 → 内容寻址 blob（``vshot-<sha>``，复用
晋升内容库根，天然去重）；会话索引 ``map_state["_visual_screenshots"]``
（≤8 FIFO）是 ref/sha/revision 级摘要——trace/journal/map_product 只允许
这一级细节，字节只在 :func:`resolve_visual_screenshot` 的评估瞬间进内存。

C13 生命周期硬化（F15 review P2-1/P2-2/P2-5 收口）：

- **refcount**：引用计数见 :mod:`blob_refs`（``vref-<sha>``）—— FIFO 淘汰
  / 会话清理只释放**本会话**引用，字节在最后一个引用消失时才删除；
- **会话绑定**：resolve 可选携带请求会话，vref 在场且不含该会话 → 诚实
  缺席（跨会话猜 ref 读取不可行）；
- **stale 硬门**：:func:`latest_screenshot_for` 严格匹配 revision + 指纹。
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
    # C13：截图归属的 desired-state 指纹（stale 硬门的第二把尺；additive
    # —— 旧索引条目缺省空串，按「指纹未知」处理）。
    mapspec_fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref[:96],
            "sha256": self.sha256[:64],
            "size": int(self.size),
            "width": int(self.width),
            "height": int(self.height),
            "mapspec_revision": int(self.mapspec_revision),
            "mapspec_fingerprint": self.mapspec_fingerprint[:96],
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
                mapspec_fingerprint=str(raw.get("mapspec_fingerprint") or "")[:96],
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
    mapspec_fingerprint: str = "",
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
        mapspec_fingerprint=str(mapspec_fingerprint or "")[:96],
    )
    index = await load_screenshot_index(session_id)
    # 内容寻址去重：同 sha 刷新位置（最新优先），不重复占 FIFO 槽；
    # refcount 只在**首次**入库时 +1（刷新路径不重复计数）。
    had_same_sha = any(e.sha256 == sha for e in index)
    entries = [e for e in index if e.sha256 != sha]
    entries.append(entry)
    evicted: List[ScreenshotEntry] = []
    while len(entries) > MAX_SCREENSHOTS_PER_SESSION:
        evicted.append(entries.pop(0))
    await session_data_manager.set_map_state(
        session_id, SCREENSHOT_INDEX_KEY,
        [e.to_dict() for e in entries],
    )
    if not had_same_sha:
        try:
            from app.services.gis_harness.visual_observation.blob_refs import (
                add_blob_ref,
            )

            add_blob_ref(sha, session_id)
        except Exception:  # noqa: BLE001 — 记账失败不阻断入库（sweep 兜底）
            logger.debug("[VisualStore] refcount add failed sha=%s",
                         sha[:12], exc_info=True)
    for old in evicted:
        # C13：淘汰 = 释放**本会话**引用；他会话仍引用时字节存活。
        try:
            from app.services.gis_harness.visual_observation.blob_refs import (
                release_blob_ref,
            )

            release_blob_ref(old.sha256, session_id)
        except Exception:  # noqa: BLE001 — 清理失败不影响主流程
            logger.debug("[VisualStore] refcount release failed ref=%s",
                         old.ref[:24], exc_info=True)
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
    session_id: str,
    mapspec_revision: int,
    mapspec_fingerprint: str = "",
) -> Optional[ScreenshotEntry]:
    """当前 desired state 的截图（stale 硬门：**严格匹配，绝不回退**）。

    C13 不变式：旧观测不得驱动新地图。revision 不一致 → None；指纹双方
    非空且不一致 → None（revision 相同但指纹漂移 = 状态被替换/回滚）。
    评估面拿到 None 即诚实缺席（``no_screenshot``）—— 宁可少评一轮，
    不可拿旧像素归因新 spec（F15 的「滞后一拍」回退正是跨代误修复入口）。
    """
    entries = await load_screenshot_index(session_id)
    if not entries:
        return None
    wanted_fp = str(mapspec_fingerprint or "")
    for entry in reversed(entries):
        if entry.mapspec_revision != int(mapspec_revision):
            continue
        if wanted_fp and entry.mapspec_fingerprint \
                and entry.mapspec_fingerprint != wanted_fp:
            continue
        return entry
    return None


def resolve_visual_screenshot(
    entry: ScreenshotEntry, *, session_id: str = "",
) -> Optional[bytes]:
    """ref → 字节（评估瞬间；sha 校验 + 大小护栏；失败 → None 诚实缺席）。

    C13 会话绑定护栏：``session_id`` 非空且 vref 索引在场但不含该会话 →
    None（跨会话猜 ref 读取不可行；vref 缺席 = 旧数据放行，不惩罚历史）。
    """
    from app.services.durable_blob_store import get_filesystem_blob_store

    if session_id:
        try:
            from app.services.gis_harness.visual_observation.blob_refs import (
                blob_has_live_ref,
            )

            if not blob_has_live_ref(entry.sha256, session_id):
                logger.info(
                    "[VisualStore] cross-session ref refused sha=%s sid=%s",
                    entry.sha256[:12], str(session_id)[:24])
                return None
        except Exception:  # noqa: BLE001 — 护栏失败按放行（旧数据兼容）
            pass
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
