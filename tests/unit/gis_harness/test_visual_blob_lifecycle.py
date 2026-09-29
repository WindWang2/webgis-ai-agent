"""C13 截图 blob lifecycle 回归（refcount / 会话绑定 / GC）。

不变式：
1. refcount：FIFO 淘汰 / 会话清理只释放**本会话**引用；他会话仍引用时
   字节存活（F15 review P2-1 的跨会话误删窗口收口）；
2. 会话绑定：vref 在场而请求会话无引用 → resolve 诚实缺席（跨会话猜
   ref 不可行；P2-5）；vref 缺席 = 旧数据放行；
3. GC：最后引用消失 → 字节 + vref 一起回收；sweep 只清「无引用且超龄」；
4. 同会话同 sha 重复注册不重复计数（去重路径）。
"""

from __future__ import annotations

import io
import os
import time
import uuid

import pytest

from app.services.durable_blob_store import (
    get_filesystem_blob_store,
    sha256_of_bytes,
)
from app.services.gis_harness.visual_observation.store import (
    MAX_SCREENSHOTS_PER_SESSION,
    blob_key_for,
    load_screenshot_index,
    register_visual_screenshot,
    resolve_visual_screenshot,
)
from app.services.gis_harness.visual_observation.blob_refs import (
    add_blob_ref,
    blob_exists_with_refs,
    blob_has_live_ref,
    ref_key_for,
    release_blob_ref,
    release_session_screenshots,
    sweep_orphan_screenshots,
)
from app.services.session_data import session_data_manager


def _png(tag: int = 0) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (tag % 255, 3, 7)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def sid_a():
    return f"bref-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def sid_b():
    return f"bref-b-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _clean(sid_a, sid_b):
    await session_data_manager.clear_session(sid_a)
    await session_data_manager.clear_session(sid_b)
    yield
    await session_data_manager.clear_session(sid_a)
    await session_data_manager.clear_session(sid_b)


# ── refcount + 共享 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_creates_ref_and_resolve_is_bound(sid_a, sid_b):
    entry = await register_visual_screenshot(sid_a, _png(1), mapspec_revision=3)
    # 本会话解析可用。
    assert resolve_visual_screenshot(entry, session_id=sid_a) is not None
    # 他会话猜同一 ref → 护栏拒绝（vref 在场且不含 sid_b）。
    assert resolve_visual_screenshot(entry, session_id=sid_b) is None
    # vref 缺席（旧数据形状）→ 放行不惩罚历史。
    assert blob_has_live_ref(entry.sha256, sid_a) is True
    store = get_filesystem_blob_store()
    store.delete_blob(ref_key_for(entry.sha256))
    assert blob_has_live_ref(entry.sha256, sid_b) is True


@pytest.mark.asyncio
async def test_eviction_does_not_delete_shared_blob(sid_a, sid_b):
    """F15 review P2-1 窗口收口：A 会话 FIFO 淘汰不删 B 仍引用的字节。"""
    shared = _png(9)
    entry_a = await register_visual_screenshot(sid_a, shared, mapspec_revision=1)
    await register_visual_screenshot(sid_b, shared, mapspec_revision=1)
    # A 连推 8 张新图 → shared 成为最老被淘汰。
    for i in range(MAX_SCREENSHOTS_PER_SESSION):
        await register_visual_screenshot(
            sid_a, _png(20 + i), mapspec_revision=2 + i)
    index_a = await load_screenshot_index(sid_a)
    assert all(e.sha256 != entry_a.sha256 for e in index_a), "evicted from A"
    # B 仍可解析（字节存活）。
    assert resolve_visual_screenshot(entry_a, session_id=sid_b) is not None
    assert blob_exists_with_refs(entry_a.sha256) is True


@pytest.mark.asyncio
async def test_last_release_deletes_blob(sid_a, sid_b):
    shared = _png(30)
    entry = await register_visual_screenshot(sid_a, shared, mapspec_revision=1)
    await register_visual_screenshot(sid_b, shared, mapspec_revision=1)
    store = get_filesystem_blob_store()
    assert store.exists(blob_key_for(entry.sha256))
    # A 释放（会话回收）→ B 仍在 → 字节存活。
    await release_session_screenshots(sid_a)
    assert store.exists(blob_key_for(entry.sha256))
    # B 释放 → 最后引用消失 → 字节 + vref 回收。
    await release_session_screenshots(sid_b)
    assert not store.exists(blob_key_for(entry.sha256))
    assert blob_exists_with_refs(entry.sha256) is False


@pytest.mark.asyncio
async def test_clear_session_hook_releases_refs(sid_a, sid_b):
    entry = await register_visual_screenshot(sid_a, _png(40), mapspec_revision=1)
    await register_visual_screenshot(sid_b, _png(41), mapspec_revision=1)
    await session_data_manager.clear_session(sid_a)
    store = get_filesystem_blob_store()
    assert not store.exists(blob_key_for(entry.sha256))
    index = await load_screenshot_index(sid_a)
    assert index == []


@pytest.mark.asyncio
async def test_duplicate_registration_counts_once(sid_a):
    data = _png(50)
    await register_visual_screenshot(sid_a, data, mapspec_revision=1)
    await register_visual_screenshot(sid_a, data, mapspec_revision=2)
    index = await load_screenshot_index(sid_a)
    assert len(index) == 1 and index[0].mapspec_revision == 2
    # 只有一个引用：一次释放即回收。
    await release_session_screenshots(sid_a)
    store = get_filesystem_blob_store()
    assert not store.exists(blob_key_for(sha256_of_bytes(data)))


# ── GC sweep ──────────────────────────────────────────────────────────────

def test_sweep_removes_only_old_unreferenced_blobs(sid_a):
    store = get_filesystem_blob_store()
    # 孤儿：入库但不建 vref，再把 mtime 拨老。
    orphan = _png(60)
    orphan_sha = sha256_of_bytes(orphan)
    store.put_blob(blob_key_for(orphan_sha), orphan, "binary")
    old_ts = time.time() - 30 * 24 * 3600
    os.utime(store.primary_path(blob_key_for(orphan_sha), "binary"), (old_ts, old_ts))
    # 受引用 + 新鲜：两者都必须存活。
    refed = _png(61)
    refed_sha = sha256_of_bytes(refed)
    store.put_blob(blob_key_for(refed_sha), refed, "binary")
    add_blob_ref(refed_sha, sid_a)

    report = sweep_orphan_screenshots(max_age_s=7 * 24 * 3600)

    assert not store.exists(blob_key_for(orphan_sha))
    assert store.exists(blob_key_for(refed_sha))
    assert report["swept"] >= 1
    assert report["kept_refed"] >= 1


def test_sweep_recovers_stale_lease(sid_a):
    """租约 GC：引用非空但 vref 与字节双双超龄 → 按陈旧租约回收
    （并发丢减量的泄漏面；C13 review P2-1）。"""
    import json as _json

    store = get_filesystem_blob_store()
    data = _png(80)
    sha = sha256_of_bytes(data)
    store.put_blob(blob_key_for(sha), data, "binary")
    add_blob_ref(sha, sid_a)
    # 拨老：字节与 vref（写入时带 ts）都到 30 天前。
    old_ts = time.time() - 30 * 24 * 3600
    os.utime(store.primary_path(blob_key_for(sha), "binary"), (old_ts, old_ts))
    ref_raw = store.get_blob(ref_key_for(sha))
    assert ref_raw
    entry = _json.loads(ref_raw.decode())
    entry["ts"] = int(old_ts)
    store.put_blob(ref_key_for(sha), _json.dumps(entry).encode(), "json")

    report = sweep_orphan_screenshots(max_age_s=7 * 24 * 3600)

    assert not store.exists(blob_key_for(sha))
    assert blob_exists_with_refs(sha) is False
    assert report["swept"] >= 1


def test_sweep_keeps_young_orphans(sid_a):
    store = get_filesystem_blob_store()
    young = _png(70)
    young_sha = sha256_of_bytes(young)
    store.put_blob(blob_key_for(young_sha), young, "binary")
    sweep_orphan_screenshots(max_age_s=7 * 24 * 3600)
    assert store.exists(blob_key_for(young_sha))
    store.delete_blob(blob_key_for(young_sha))


# ── 释放原语 ──────────────────────────────────────────────────────────────

def test_release_unknown_ref_is_noop(sid_a):
    assert release_blob_ref("ff" * 32, sid_a) is False
    assert add_blob_ref("", sid_a) is False
    assert add_blob_ref("ff" * 32, "") is False
