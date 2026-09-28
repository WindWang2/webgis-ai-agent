"""截图 blob 引用计数 / 会话绑定 / GC（C13 blob lifecycle）。

F15 遗留窗口（其 review P2-1/P2-2/P2-5）在此收口：

- **refcount**：内容寻址 blob（``vshot-<sha>``）全局共享，引用索引以
  ``vref-<sha>`` JSON（``{"v":1,"refs":{session:count}}``，≤64 会话/blob）
  存放在同一 BlobStore 协议里 —— 经协议读写即获得可插拔后端（本地文件
  实现 hermetic；S3 适配器 = 换协议实现 + 自带 sweep，无需改本模块）。
- **会话绑定**：``resolve`` 的护栏面 —— 请求会话不在 live refs 里 →
  诚实缺席（跨会话猜 ref 读取在结构上不可行；无按 ref 读字节的公开
  端点，唯一消费方是 provider 评估瞬间）。
- **GC**：FIFO 淘汰 / 会话清理 → ``release_blob_ref``；计数归 0 才删
  字节；``sweep_orphan_screenshots`` 清扫无引用且超龄的 vshot（维护面）。

并发方向安全：vref 读改写用 put_blob 原子替换（last-writer-wins），并发
释放最坏丢一次减量 → 泄漏（sweep 可回收），**绝不提前删除**仍被引用的
字节。全部操作 fail-open（失败记日志不抛）—— GC 是增值面，绝不阻断
观察/评估/修复主链。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 单 blob 最多绑定的会话数（有界纪律；超出按注册失败处理 = 不入库）。
MAX_SESSIONS_PER_BLOB = 64
#: vref 条目 schema 版本。
_REF_SCHEMA_VERSION = 1
#: vshot blob 键前缀（与 store.blob_key_for 同源约定）。
_SHOT_PREFIX = "vshot-"
_REF_PREFIX = "vref-"
#: sweep 默认 TTL：无引用截图保留 7 天（防「刚删完又要」的抖动）。
DEFAULT_SWEEP_MAX_AGE_S = 7 * 24 * 3600


def _sha_of_ref(ref: str) -> str:
    """``vshot-<sha>`` → sha（无前缀 = 原样，兼容测试内手造键）。"""
    ref = str(ref or "")
    return ref[len(_SHOT_PREFIX):] if ref.startswith(_SHOT_PREFIX) else ref


def ref_key_for(sha256: str) -> str:
    return f"{_REF_PREFIX}{str(sha256 or '').strip()[:64]}"


def _blob_store():
    from app.services.durable_blob_store import get_filesystem_blob_store

    return get_filesystem_blob_store()


def _load_refs(sha256: str) -> Dict[str, int]:
    try:
        raw = _blob_store().get_blob(ref_key_for(sha256))
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        refs = data.get("refs") if isinstance(data, dict) else None
        if not isinstance(refs, dict):
            return {}
        out: Dict[str, int] = {}
        for sid, cnt in list(refs.items())[:MAX_SESSIONS_PER_BLOB]:
            try:
                c = int(cnt)
            except (TypeError, ValueError):
                continue
            if c > 0:
                out[str(sid)[:64]] = c
        return out
    except Exception:  # noqa: BLE001 — 索引缺席/损坏 = 无引用（保守不删）
        return {}


def _save_refs(sha256: str, refs: Dict[str, int]) -> bool:
    payload = json.dumps(
        {"v": _REF_SCHEMA_VERSION,
         "refs": {str(k)[:64]: int(v) for k, v in
                  list(refs.items())[:MAX_SESSIONS_PER_BLOB]}},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    try:
        _blob_store().put_blob(ref_key_for(sha256), payload, "json")
        return True
    except Exception:  # noqa: BLE001 — 记账失败不阻断入库
        logger.warning("[VisualBlobRefs] save refs failed sha=%s",
                       str(sha256)[:12], exc_info=True)
        return False


def add_blob_ref(sha256: str, session_id: str) -> bool:
    """会话 → blob 引用 +1（幂等递增；同会话同 sha 只在入库时调用一次）。"""
    sha = _sha_of_ref(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return False
    refs = _load_refs(sha)
    if sid not in refs and len(refs) >= MAX_SESSIONS_PER_BLOB:
        logger.warning(
            "[VisualBlobRefs] ref cap reached sha=%s — refusing new ref",
            sha[:12])
        return False
    refs[sid] = refs.get(sid, 0) + 1
    return _save_refs(sha, refs)


def release_blob_ref(sha256: str, session_id: str) -> bool:
    """会话引用 -1；归零删字节（返回 True = 字节已删除）。"""
    sha = _sha_of_ref(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return False
    refs = _load_refs(sha)
    if sid not in refs:
        return False
    remaining = refs[sid] - 1
    if remaining > 0:
        refs[sid] = remaining
        _save_refs(sha, refs)
        return False
    refs.pop(sid, None)
    if refs:
        _save_refs(sha, refs)
        return False
    # 引用清零：索引与字节一起回收（fail-open）。
    try:
        _blob_store().delete_blob(ref_key_for(sha))
    except Exception:  # noqa: BLE001
        logger.debug("[VisualBlobRefs] vref delete failed sha=%s",
                     sha[:12], exc_info=True)
    try:
        from app.services.gis_harness.visual_observation.store import (
            blob_key_for,
        )

        return bool(_blob_store().delete_blob(blob_key_for(sha)))
    except Exception:  # noqa: BLE001 — 字节删除失败留给 sweep
        logger.debug("[VisualBlobRefs] blob delete failed sha=%s",
                     sha[:12], exc_info=True)
        return False


def blob_has_live_ref(sha256: str, session_id: str) -> bool:
    """会话绑定护栏：请求会话是否持有 live ref（vref 缺席 = 旧数据放行）。"""
    sha = _sha_of_ref(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return True
    refs = _load_refs(sha)
    if not refs:
        return True          # 旧数据 / vref 损坏：不惩罚历史
    return sid in refs


def blob_exists_with_refs(sha256: str) -> bool:
    """诊断面：blob 是否仍有任何 live ref。"""
    return bool(_load_refs(_sha_of_ref(str(sha256 or ""))))


async def release_session_screenshots(session_id: str) -> int:
    """会话终点回收：按索引释放全部引用 + 清空索引（clear_session 挂点）。

    返回释放的引用条数（披露面；fail-open）。
    """
    from app.services.gis_harness.visual_observation.store import (
        SCREENSHOT_INDEX_KEY,
        load_screenshot_index,
    )
    from app.services.session_data import session_data_manager

    sid = str(session_id or "")
    if not sid:
        return 0
    released = 0
    try:
        entries = await load_screenshot_index(sid)
        for entry in entries:
            try:
                release_blob_ref(entry.sha256, sid)
                released += 1
            except Exception:  # noqa: BLE001 — 单条失败继续
                logger.debug("[VisualBlobRefs] release failed sha=%s",
                             entry.sha256[:12], exc_info=True)
        await session_data_manager.set_map_state(
            sid, SCREENSHOT_INDEX_KEY, [])
    except Exception:  # noqa: BLE001 — 回收失败不阻断会话清理
        logger.warning("[VisualBlobRefs] session release failed sid=%s",
                       sid[:24], exc_info=True)
    return released


def sweep_orphan_screenshots(
    max_age_s: int = DEFAULT_SWEEP_MAX_AGE_S,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """清扫无引用且超龄的 vshot blob（文件系统实现；维护面）。

    只对 FilesystemBlobStore 有意义 —— 布局遍历属于本地适配器；对象存储
    适配器实现同名维护任务（接口说明，不引入网络依赖）。返回统计回执。
    """
    import os

    store = _blob_store()
    root = getattr(store, "root", None)
    if root is None:
        return {"swept": 0, "scanned": 0, "reason": "non_filesystem_backend"}
    ts = float(now if now is not None else time.time())
    swept = scanned = kept_refed = kept_young = 0
    try:
        shards = os.listdir(root)
    except OSError:
        return {"swept": 0, "scanned": 0, "reason": "root_unreadable"}
    for shard in shards:
        shard_dir = root / str(shard)
        if not shard_dir.is_dir():
            continue
        try:
            names = os.listdir(shard_dir)
        except OSError:
            continue
        for name in names:
            if not name.startswith(_SHOT_PREFIX) or not name.endswith(".bin"):
                continue
            sha = name[len(_SHOT_PREFIX):-len(".bin")]
            scanned += 1
            path = shard_dir / name
            try:
                age = ts - path.stat().st_mtime
            except OSError:
                continue
            refs = _load_refs(sha)
            if refs:
                kept_refed += 1
                continue
            if age < max_age_s:
                kept_young += 1
                continue
            try:
                store.delete_blob(ref_key_for(sha))
            except Exception:  # noqa: BLE001
                pass
            try:
                store.delete_blob(f"{_SHOT_PREFIX}{sha}")
                swept += 1
            except Exception:  # noqa: BLE001 — 单条失败继续
                logger.debug("[VisualBlobRefs] sweep delete failed sha=%s",
                             sha[:12], exc_info=True)
    return {"swept": swept, "scanned": scanned,
            "kept_refed": kept_refed, "kept_young": kept_young}


__all__ = [
    "MAX_SESSIONS_PER_BLOB",
    "DEFAULT_SWEEP_MAX_AGE_S",
    "add_blob_ref",
    "release_blob_ref",
    "blob_has_live_ref",
    "blob_exists_with_refs",
    "release_session_screenshots",
    "sweep_orphan_screenshots",
    "ref_key_for",
]
