"""视觉证据 blob 引用计数（G36 / ADR-0291；仿 ``visual_observation.blob_refs``）。

- 内容寻址 blob ``vevimg-<sha>``（derived 字节）全局共享；引用索引
  ``vevr-<sha>`` JSON ``{"v":1,"ts":…,"refs":{session:count}}``（≤64 会话）。
- 与 critic 的 ``vshot-``/``vref-`` 完全独立前缀 —— 两套生命周期互不
  淘汰（V19：critic FIFO 淘汰不影响已发送聊天附件）。
- 并发语义同 blob_refs：文件系统后端按内容哈希 flock；未知所有权
  fail-closed；GC 失败记日志不抛。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from app.services.gis_harness.visual_observation.blob_lock import blob_ref_lock

logger = logging.getLogger(__name__)

MAX_SESSIONS_PER_BLOB = 64
_REF_SCHEMA_VERSION = 1
_IMG_PREFIX = "vevimg-"
_REF_PREFIX = "vevr-"
#: 孤儿清扫 TTL：无引用证据字节保留 7 天。
DEFAULT_SWEEP_MAX_AGE_S = 7 * 24 * 3600


def _sha_of(key: str) -> str:
    key = str(key or "")
    return key[len(_IMG_PREFIX):] if key.startswith(_IMG_PREFIX) else key


def img_key_for(sha256: str) -> str:
    return f"{_IMG_PREFIX}{str(sha256 or '').strip()[:64]}"


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
         "ts": int(time.time()),
         "refs": {str(k)[:64]: int(v) for k, v in
                  list(refs.items())[:MAX_SESSIONS_PER_BLOB]}},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    try:
        _blob_store().put_blob(ref_key_for(sha256), payload, "json")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[VisualContextRefs] save refs failed sha=%s",
                       str(sha256)[:12], exc_info=True)
        return False


def _add_ref_locked(sha: str, sid: str) -> bool:
    refs = _load_refs(sha)
    if sid not in refs and len(refs) >= MAX_SESSIONS_PER_BLOB:
        logger.warning("[VisualContextRefs] ref cap reached sha=%s", sha[:12])
        return False
    refs[sid] = refs.get(sid, 0) + 1
    return _save_refs(sha, refs)


def store_evidence_ref(sha256: str, session_id: str, data: bytes) -> bool:
    """入库 + 会话引用 +1（blob 已存在则共享字节；V17 共享不错删）。"""
    sha = _sha_of(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return False
    store = _blob_store()
    with blob_ref_lock(store, sha):
        key = img_key_for(sha)
        if not store.exists(key):
            store.put_blob(key, data, "binary")
        return _add_ref_locked(sha, sid)


def release_evidence_ref(sha256: str, session_id: str) -> bool:
    """会话引用 -1；全局归零才删字节（同会话多证据共享同 blob 时安全）。"""
    sha = _sha_of(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return False
    with blob_ref_lock(_blob_store(), sha):
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
        try:
            _blob_store().delete_blob(ref_key_for(sha))
        except Exception:  # noqa: BLE001
            logger.debug("[VisualContextRefs] vevr delete failed sha=%s",
                         sha[:12], exc_info=True)
        try:
            return bool(_blob_store().delete_blob(img_key_for(sha)))
        except Exception:  # noqa: BLE001 — 字节删除失败留给 sweep
            logger.debug("[VisualContextRefs] vevimg delete failed sha=%s",
                         sha[:12], exc_info=True)
            return False


def blob_has_live_ref(sha256: str, session_id: str) -> bool:
    """会话绑定护栏：无所有权证据即拒绝（V16 跨会话猜 ref）。"""
    sha = _sha_of(str(sha256 or ""))
    sid = str(session_id or "")[:64]
    if not sha or not sid:
        return False
    refs = _load_refs(sha)
    return bool(refs) and sid in refs


def read_evidence_bytes(sha256: str, session_id: str, max_bytes: int) -> bytes | None:
    """受权读取 + sha 校验 + 大小护栏；失败 → None（诚实缺席，V28）。

    授权先行：会话必须在 live refs 里（fail-closed；vref 缺席 = 拒绝，
    不惩罚历史的前提不适用于本新链路 —— 新链路从第一天起要求引用在场）。
    """
    sha = _sha_of(str(sha256 or ""))
    if not sha or not session_id:
        return None
    if not blob_has_live_ref(sha, session_id):
        return None
    try:
        store = _blob_store()
        key = img_key_for(sha)
        if not store.exists(key):
            return None
        data = store.get_blob(key, expected_sha256=sha or None)
        if data is None or len(data) > max_bytes:
            return None
        return data
    except Exception:  # noqa: BLE001
        logger.warning("[VisualContextRefs] evidence resolve failed sha=%s",
                       sha[:12], exc_info=True)
        return None


def sweep_orphan_evidence_blobs(
    max_age_s: int = DEFAULT_SWEEP_MAX_AGE_S,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """清扫无引用且超龄的 vevimg/vevr（G36 blob lifecycle 生产接线）。

    与 ``blob_refs.sweep_orphan_screenshots`` 同款语义（review P2#12）：
    release 失败/异常终止窗口泄漏的证据字节由此兜底回收；只对
    FilesystemBlobStore 有意义（对象存储适配器实现同名维护任务）。
    """
    import os
    import time as _time

    store = _blob_store()
    root = getattr(store, "root", None)
    if root is None:
        return {"swept": 0, "scanned": 0, "reason": "non_filesystem_backend"}
    ts = float(now if now is not None else _time.time())
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
            if not name.startswith(_IMG_PREFIX) or not name.endswith(".bin"):
                continue
            sha = name[len(_IMG_PREFIX):-len(".bin")]
            scanned += 1
            path = shard_dir / name
            try:
                age = ts - path.stat().st_mtime
            except OSError:
                continue
            with blob_ref_lock(store, sha):
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
                    store.delete_blob(img_key_for(sha))
                    swept += 1
                except Exception:  # noqa: BLE001 — 单条失败继续
                    logger.debug("[VisualContextRefs] sweep delete failed sha=%s",
                                 sha[:12], exc_info=True)
    return {"swept": swept, "scanned": scanned,
            "kept_refed": kept_refed, "kept_young": kept_young}
