"""
RAG vector store durability tests (REVIEW-P0-PROMOTED, faiss_store).

The pre-fix code:
  - had non-atomic dual-file writes (metadata + index, no ordering, no
    rename-via-tempfile), so a crash between the two left them
    permanently divergent
  - swallowed every exception in save_metadata / save_index to a
    logger.warning line, so add_vectors returned success when nothing
    reached disk
  - wrote renumbered metadata BEFORE rebuilding the index in compact(),
    so a crash left positions misaligned and search returned
    wrong-chunk content

The post-fix code:
  - atomic-writes both files (tempfile in same dir + fsync + os.replace)
  - holds a sidecar write lock for the whole critical section
  - propagates every write failure as RagPersistenceError
  - writes the index first in both add_vectors and compact, so a crash
    leaves "stale metadata" (recoverable by _recover_index_metadata_consistency)
  - on load, cross-checks index ntotal against metadata chunk count and
    drops orphaned metadata entries (trust the index)

#1704: the index-ahead direction of that cross-check (ntotal > len(chunks),
the crash window between add_vectors' index write and metadata write) used
to log a warning and nothing else, so the next add_vectors numbered its new
chunks from the short metadata and wrote them into slots already held by
orphan vectors — permanent, silent metadata↔vector misalignment. Recovery
now pads the metadata to ntotal with explicit unavailable placeholders,
search skips them, and add_vectors reconciles before numbering (and refuses
to write when reconciliation cannot be persisted).
"""
import json
import os
import tempfile

import numpy as np
import pytest

from app.services.rag.faiss_store import (
    FaissVectorStore,
    RagPersistenceError,
    _atomic_write_json,
)


# ── helpers ─────────────────────────────────────────────────────────


def _tmp_store() -> tuple[FaissVectorStore, str]:
    """Build a store in a fresh temp dir; caller is responsible for cleanup."""
    tmpdir = tempfile.mkdtemp(prefix="rag-durability-")
    return FaissVectorStore(index_dir=tmpdir), tmpdir


def _fake_vectors(n: int, dim: int = 8) -> np.ndarray:
    """Deterministic unit-ish vectors so we don't need the real embed model."""
    rng = np.random.default_rng(seed=42)
    return (rng.random((n, dim))).astype(np.float32)


@pytest.fixture
def patch_embed(monkeypatch):
    """Replace FaissVectorStore.embed_texts with a deterministic stub.

    compact() calls embed_texts to re-encode the kept chunks; without
    this stub the test would try to download the real SentenceTransformer
    model, which is both slow and offline-broken.
    """
    counter = {"calls": 0}

    def fake(self, texts):
        counter["calls"] += 1
        return _fake_vectors(len(texts))

    monkeypatch.setattr(FaissVectorStore, "embed_texts", fake)
    return counter


# ── add_vectors: error propagation ──────────────────────────────────


def test_add_vectors_propagates_save_index_failure(monkeypatch, tmp_path):
    """REVIEW-P0-PROMOTED, fix A: add_vectors must raise when the underlying
    save fails. The pre-fix code logged a warning and returned None;
    callers (engine.index_document) treated that as success.
    """
    store, dir_ = _tmp_store()
    try:
        # First call needs to bootstrap the in-memory index; do that
        # with a real call so the index is dim 8.
        store.add_vectors(_fake_vectors(1), [{"document_id": "doc_init", "content": "x"}])
        # Now monkeypatch _atomic_write_faiss to fail.
        import app.services.rag.faiss_store as mod

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(mod, "_atomic_write_faiss", boom)
        with pytest.raises(RagPersistenceError):
            store.add_vectors(_fake_vectors(1), [{"document_id": "doc_2", "content": "y"}])
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_add_vectors_propagates_save_metadata_failure(monkeypatch):
    """Same shape, but for the metadata side."""
    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(1), [{"document_id": "doc_init", "content": "x"}])
        import app.services.rag.faiss_store as mod

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(mod, "_atomic_write_json", boom)
        with pytest.raises(RagPersistenceError):
            store.add_vectors(_fake_vectors(1), [{"document_id": "doc_2", "content": "y"}])
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── atomic_write_json: no temp file leak ───────────────────────────


def test_atomic_write_json_no_temp_leak_on_failure(monkeypatch, tmp_path):
    """If the rename itself fails, the temp file must be cleaned up so
    a failed write doesn't accumulate .tmp files in the data dir.
    """
    target = tmp_path / "metadata.json"

    def fail_rename(*a, **kw):
        raise OSError("rename failed")

    # Patch os.replace on the module's namespace to fail.
    import app.services.rag.faiss_store as mod
    monkeypatch.setattr(mod.os, "replace", fail_rename)
    with pytest.raises(OSError):
        _atomic_write_json(str(target), {"chunks": []})

    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("metadata.json") and p.name.endswith(".tmp")]
    assert leftovers == [], f"temp file leaked: {leftovers}"


def test_atomic_write_json_round_trip(tmp_path):
    target = tmp_path / "metadata.json"
    _atomic_write_json(str(target), {"chunks": [{"id": 1}]})
    assert json.loads(target.read_text()) == {"chunks": [{"id": 1}]}


# ── add_vectors: order — index written before metadata ──────────────


def test_add_vectors_writes_index_before_metadata(monkeypatch):
    """REVIEW-P0-PROMOTED, fix B+C: a crash between the two writes
    must leave the index in the OLD state (and the new metadata be
    dropped on recovery), not the other way around. Verify by
    observing the order of writes.
    """
    store, dir_ = _tmp_store()
    try:
        order: list[str] = []
        import app.services.rag.faiss_store as mod
        original_index = mod._atomic_write_faiss
        original_meta = mod._atomic_write_json

        def tracked_index(*a, **kw):
            order.append("index")
            return original_index(*a, **kw)

        def tracked_meta(*a, **kw):
            order.append("meta")
            return original_meta(*a, **kw)

        monkeypatch.setattr(mod, "_atomic_write_faiss", tracked_index)
        monkeypatch.setattr(mod, "_atomic_write_json", tracked_meta)

        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "a"},
            {"document_id": "d2", "content": "b"},
        ])

        assert order == ["index", "meta"], (
            f"add_vectors must write index first, then metadata; got {order}. "
            "A crash in between would otherwise leave stale metadata "
            "claiming chunks the (old) index doesn't have, with no way to recover."
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── recovery on load: trust index, drop orphaned metadata ──────────


def test_recovery_drops_orphaned_metadata_when_index_is_behind(tmp_path):
    """Simulate a crash between the index write and the metadata write:
    index is older (smaller ntotal) than the metadata. On load, the
    store must detect the divergence and truncate the metadata to
    match the index, so positions are coherent.
    """
    from app.services.rag.faiss_store import _atomic_write_faiss

    # First call writes a 2-vector index and matching 2-chunk metadata.
    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "a"},
            {"document_id": "d2", "content": "b"},
        ])

        # Simulate the crash: rewrite the index to a smaller one
        # (2 -> 1) WITHOUT updating the metadata. This represents
        # "metadata was written, then index write was rolled back."
        smaller = type(store._index)(8)
        smaller.add(_fake_vectors(1))
        _atomic_write_faiss(
            os.path.join(dir_, "index.faiss"), smaller
        )
        # Drop the in-memory index so the next op reloads.
        store._index = None

        # Trigger load (via get_stats, which calls _get_index).
        stats = store.get_stats()
        # index says 1; recovery should have truncated metadata to 1.
        assert stats["total_vectors"] == 1
        assert stats["total_chunks"] == 1, (
            f"expected recovery to truncate metadata to match index, "
            f"got total_chunks={stats['total_chunks']}"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_recovery_does_not_delete_when_files_are_consistent(tmp_path):
    """Sanity: when index ntotal == len(metadata.chunks), the recovery
    must be a no-op (no log warning, no rewrite).
    """
    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "a"},
            {"document_id": "d2", "content": "b"},
        ])
        meta_before = store.load_metadata()
        chunks_before = list(meta_before["chunks"])
        store._index = None  # force reload

        # Reload via stats.
        store.get_stats()
        meta_after = store.load_metadata()
        # No truncation, no log-warning rename (the file mtime would
        # change if recovery had rewritten it).
        assert [c["document_id"] for c in meta_after["chunks"]] == [
            c["document_id"] for c in chunks_before
        ]
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── compact: reordering (index before metadata) and rebuildable filter


def test_compact_writes_index_before_metadata(monkeypatch, patch_embed):
    """REVIEW-P0-PROMOTED, fix B: compact used to renumber and save
    metadata first, then rebuild the index. A crash between would
    leave positions misaligned. New code rebuilds the index FIRST
    in memory, writes it atomically, then writes the metadata.
    """
    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
        ])
        # Soft-delete one chunk so compact has work to do.
        store.mark_deleted("d1")

        order: list[str] = []
        import app.services.rag.faiss_store as mod
        original_index = mod._atomic_write_faiss
        original_meta = mod._atomic_write_json

        monkeypatch.setattr(
            mod,
            "_atomic_write_faiss",
            lambda *a, **kw: (order.append("index"), original_index(*a, **kw))[1],
        )
        monkeypatch.setattr(
            mod,
            "_atomic_write_json",
            lambda *a, **kw: (order.append("meta"), original_meta(*a, **kw))[1],
        )

        store.compact()

        assert order == ["index", "meta"], (
            f"compact must write index first, then metadata; got {order}"
        )

        # After compact, only the active (d2) chunk remains.
        meta = store.load_metadata()
        assert len(meta["chunks"]) == 1
        assert meta["chunks"][0]["document_id"] == "d2"
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_compact_drops_contentless_chunks(patch_embed):
    """REVIEW-P0-PROMOTED, fix B part 2: the old code filtered on
    ch.get("content") or ch.get("text") when rebuilding, leaving
    content-less chunks in the renumbered metadata and out of the
    index — permanently breaking alignment. New code drops
    content-less chunks from both index and metadata.
    """
    store, dir_ = _tmp_store()
    try:
        # Add a chunk with content, then a chunk with no content at all.
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "real"},
            {"document_id": "d_broken"},  # no content, no text
        ])
        # Mark the broken one for purge (its position must be skipped).
        store.mark_deleted("d1")

        result = store.compact()

        # Only the broken chunk is active (d1 was deleted, d_broken
        # has no content to rebuild). New behavior: drops content-less
        # chunks entirely, so the index and metadata end up empty.
        assert result["purged"] == 1  # d1
        # The "kept" count after dropping content-less chunks is 0.
        assert result["total"] == 0
        meta = store.load_metadata()
        assert meta["chunks"] == []
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── sidecar lock is exclusive ──────────────────────────────────────


def test_write_lock_is_reentrant_within_a_thread():
    """Regression for a deadlock found while mutation-testing this fix.

    save_metadata() and save_index() each acquire _write_lock. add_vectors
    and compact also hold it for their whole critical section. fcntl.flock
    on a SECOND fd for the same file does not re-enter — it blocks
    forever. So any future caller that holds the lock and then calls
    save_metadata()/save_index() would hang the worker permanently.

    The lock now tracks nesting depth in thread-local state, so nested
    acquisitions are no-ops. Without that, this test hangs (and the
    pytest timeout kills the run).
    """
    from app.services.rag.faiss_store import _write_lock

    store, dir_ = _tmp_store()
    try:
        with _write_lock(dir_):
            # Nested acquisition of the same lock must not block.
            with _write_lock(dir_):
                pass
            # And a store method that takes the lock internally must
            # also be safe to call while we hold it.
            store.save_metadata({"chunks": []})
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_write_lock_blocks_concurrent_writers():
    """Two add_vectors calls on separate store instances (the way two
    uvicorn workers would) must serialize on the sidecar lock, not
    race on the metadata file.
    """
    import threading

    store1, dir_ = _tmp_store()
    try:
        store2 = FaissVectorStore(index_dir=dir_)  # same dir = same lock

        # Both stores will compute base_idx = len(metadata["chunks"])
        # before either has appended. Without the lock, both would
        # compute 0, and the second add would clobber the first.
        results: list[Exception | None] = [None, None]

        def run(idx: int):
            try:
                getattr(store1 if idx == 0 else store2, "add_vectors")(
                    _fake_vectors(1),
                    [{"document_id": f"racer_{idx}", "content": f"x{idx}"}],
                )
            except Exception as e:
                results[idx] = e

        t1 = threading.Thread(target=run, args=(0,))
        t2 = threading.Thread(target=run, args=(1,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results == [None, None], f"one of the concurrent writers failed: {results}"

        # The two documents must have *distinct* base_idx values,
        # i.e. the second writer waited for the first to finish.
        meta = store1.load_metadata()
        indices = sorted(c["index"] for c in meta["chunks"])
        assert indices == [0, 1], (
            f"concurrent writers clobbered each other's base_idx; "
            f"got indices={indices}, expected [0, 1]"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── #601: mark_deleted RMW must hold _write_lock ──────────────────────


def test_mark_deleted_locked_against_concurrent_add(monkeypatch):
    """Issue #601: mark_deleted used to read metadata OUTSIDE _write_lock
    and only wrapped the save. A concurrent add_vectors (another worker,
    or a compact on a worker thread) could interleave between the read and
    the save; the stale snapshot then overwrote the add's fresh chunk
    index — the vector landed in index.faiss but its metadata entry
    vanished, so search silently skipped it (index ntotal > len(chunks)).

    The fix moves the whole load+modify+save under _write_lock (same RMW
    discipline as add_vectors/compact). Choreographed deterministically,
    with the add thread parked mid-write while still holding the lock:

      1. T_add acquires the lock, computes base_idx from current metadata,
         appends chunk B in memory, then blocks inside the gated index
         write while still holding the lock.
      2. T_mark starts mark_deleted("A") — with the fix it must block at
         lock ACQUISITION (before its read), then see the post-add
         metadata; without it, it reads the pre-add disk state and later
         overwrites B with its stale snapshot.
      3. Release T_add; both finish. Final state must contain both A
         (deleted) and B, with index ntotal == len(chunks).
    """
    import threading

    import app.services.rag.faiss_store as mod

    store_add, dir_ = _tmp_store()
    try:
        store_add.add_vectors(_fake_vectors(1), [{"document_id": "A", "content": "alpha"}])
        store_mark = FaissVectorStore(index_dir=dir_)

        add_in_flight = threading.Event()
        allow_add_finish = threading.Event()
        original_faiss_write = mod._atomic_write_faiss

        def gated_faiss_write(*a, **kw):
            # Only the add thread hits this gate; mark_deleted never writes
            # the index. Park the add thread while it holds the lock.
            add_in_flight.set()
            assert allow_add_finish.wait(timeout=10), (
                "main thread never released the parked add_vectors"
            )
            return original_faiss_write(*a, **kw)

        monkeypatch.setattr(mod, "_atomic_write_faiss", gated_faiss_write)

        results: list[Exception | None] = [None, None]

        def run_add():
            try:
                store_add.add_vectors(
                    _fake_vectors(1), [{"document_id": "B", "content": "beta"}]
                )
            except Exception as e:  # pragma: no cover - failure path
                results[0] = e

        def run_mark():
            try:
                store_mark.mark_deleted("A")
            except Exception as e:  # pragma: no cover - failure path
                results[1] = e

        t_add = threading.Thread(target=run_add)
        t_mark = threading.Thread(target=run_mark)
        t_add.start()
        # T_add now holds the sidecar lock, parked mid add_vectors.
        assert add_in_flight.wait(timeout=10), (
            "add_vectors never reached the write barrier"
        )
        t_mark.start()
        # Let the add finish. With the fix, T_mark is blocked at lock
        # acquisition and will read the POST-add metadata; without it, T_mark
        # already captured the pre-add snapshot and will clobber B.
        allow_add_finish.set()
        t_add.join(timeout=10)
        t_mark.join(timeout=10)

        assert results == [None, None], f"a concurrent writer failed: {results}"
        assert not t_add.is_alive() and not t_mark.is_alive(), "worker threads hung"

        meta = store_mark.load_metadata()
        ids = [c["document_id"] for c in meta["chunks"]]
        assert ids == ["A", "B"], (
            "#601: mark_deleted's stale snapshot dropped the concurrent add; "
            f"metadata chunks={ids}"
        )
        assert meta["chunks"][0]["deleted"] is True, (
            "expected A to carry the deferred deleted flag"
        )
        assert store_mark._get_index().ntotal == len(meta["chunks"]) == 2, (
            "index ntotal and metadata chunks diverged after concurrent "
            "add + mark_deleted"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── #544: cross-process index staleness ─────────────────────────────────


def test_get_index_refreshes_when_file_mtime_changes(patch_embed):
    """#544: 另一实例重写 index.faiss（mtime 变化）后，已预加载实例的
    _get_index 必须重读磁盘，返回新的 ntotal，而不是永久的旧内存索引。"""
    store_a, dir_ = _tmp_store()
    try:
        store_b = FaissVectorStore(index_dir=dir_)

        store_a.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "a"},
            {"document_id": "d2", "content": "b"},
        ])
        assert store_b._get_index().ntotal == 2

        # A 追加 1 条 → index.faiss mtime 变化
        store_a.add_vectors(_fake_vectors(1), [{"document_id": "d3", "content": "c"}])

        assert store_b._get_index().ntotal == 3, (
            "mtime 变化后必须重读索引；旧代码永久缓存第一个加载的索引"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_cross_instance_search_refreshes_after_compact(patch_embed):
    """#544 动态复现（两实例）：A compact 后，B 的下一次 search 必须基于
    新索引 —— 只能返回保留语料 gamma/delta，不得返回错位内容或复活
    已删除文档（alpha/beta）的内容。旧行为按旧位置检索 → 返回 delta/复活
    已删内容。"""
    store_a, dir_ = _tmp_store()
    try:
        store_b = FaissVectorStore(index_dir=dir_)

        store_a.add_vectors(_fake_vectors(4), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
            {"document_id": "d3", "content": "gamma"},
            {"document_id": "d4", "content": "delta"},
        ])
        # B 冷加载 4 向量索引
        assert store_b._get_index().ntotal == 4

        # A 软删 d1/d2 并 compact → 磁盘 2 向量（gamma@0, delta@1）
        store_a.mark_deleted("d1")
        store_a.mark_deleted("d2")
        store_a.compact()
        assert [c["document_id"] for c in store_a.load_metadata()["chunks"]] == ["d3", "d4"]

        # B 的下一次 search 必须命中新索引：内容只可能是 gamma/delta，
        # 且位置与 metadata 对齐（最多 2 条）。
        results = store_b.search(_fake_vectors(1), top_k=5)
        contents = {r["content"] for r in results}
        assert contents <= {"gamma", "delta"}, (
            f"stale index surfaced wrong content: {contents}"
        )
        assert "alpha" not in contents and "beta" not in contents
        assert len(results) <= 2, (
            f"positions misaligned: index refreshed but metadata mismatch -> {len(results)} rows"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_stale_add_vectors_does_not_clobber_compacted_index(patch_embed):
    """#544: 持有 compact 前旧索引的实例 add_vectors，不得用旧索引覆盖
    磁盘（旧行为：4 向量旧索引 + 2 新向量写盘 → ntotal=6 而 metadata 只有
    4 chunks，位置永久错配，且复活已删除向量）。新行为：锁内按 mtime 重读
    compact 后的索引 → 最终磁盘 ntotal == len(chunks) == 4。"""
    import faiss

    store_a, dir_ = _tmp_store()
    try:
        store_b = FaissVectorStore(index_dir=dir_)

        store_a.add_vectors(_fake_vectors(4), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
            {"document_id": "d3", "content": "gamma"},
            {"document_id": "d4", "content": "delta"},
        ])
        assert store_b._get_index().ntotal == 4  # B 持有 compact 前索引

        store_a.mark_deleted("d1")
        store_a.mark_deleted("d2")
        store_a.compact()  # 磁盘：2 向量（gamma/delta）

        # B 带着旧内存索引写新向量 —— 必须先重读 compact 结果
        store_b.add_vectors(_fake_vectors(2), [
            {"document_id": "d5", "content": "epsilon"},
            {"document_id": "d6", "content": "zeta"},
        ])

        meta = store_b.load_metadata()
        assert [c["document_id"] for c in meta["chunks"]] == ["d3", "d4", "d5", "d6"], (
            f"metadata chunks wrong after stale add: {[c['document_id'] for c in meta['chunks']]}"
        )
        disk_idx = faiss.read_index(os.path.join(dir_, "index.faiss"))
        assert disk_idx.ntotal == len(meta["chunks"]) == 4, (
            f"stale add_vectors clobbered the compacted index: disk ntotal="
            f"{disk_idx.ntotal}, metadata chunks={len(meta['chunks'])}"
        )
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


# ── #1704: index ahead of metadata (crash between the two writes) ─────


def _crash_window_index_ahead(dir_: str, vectors: np.ndarray) -> None:
    """Simulate add_vectors' crash window: rewrite index.faiss with MORE
    vectors than metadata.json has chunks, leaving the metadata untouched
    (the metadata write never happened).
    """
    import faiss

    from app.services.rag.faiss_store import _atomic_write_faiss

    crashed = faiss.IndexFlatIP(vectors.shape[1])
    crashed.add(vectors)
    _atomic_write_faiss(os.path.join(dir_, "index.faiss"), crashed)


def test_recovery_pads_orphan_slots_when_index_is_ahead():
    """#1704: index.faiss ahead of metadata.json used to produce a log line
    and nothing else. Recovery must pad the metadata up to ntotal with
    explicit ``unavailable`` placeholders — persisted, not just in memory —
    so list position == vector slot again and search never surfaces an
    orphan slot as a hit.
    """
    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
        ])

        # Crash window: index gains 2 orphan vectors, metadata stays at 2.
        _crash_window_index_ahead(dir_, _fake_vectors(4))
        store._index = None  # force the next op to reload from disk

        stats = store.get_stats()  # triggers _get_index -> recovery
        assert stats["total_vectors"] == 4
        assert stats["total_chunks"] == 4, (
            f"recovery must pad metadata to ntotal; got total_chunks="
            f"{stats['total_chunks']}"
        )

        meta = store.load_metadata()
        # Real chunks untouched…
        assert [c["document_id"] for c in meta["chunks"][:2]] == ["d1", "d2"]
        assert [c["index"] for c in meta["chunks"][:2]] == [0, 1]
        # …orphan slots are explicit placeholders.
        for slot in (2, 3):
            ph = meta["chunks"][slot]
            assert ph.get("unavailable") is True, f"slot {slot} not a placeholder: {ph}"
            assert ph["index"] == slot
            assert ph.get("document_id") is None

        # The pad must be on DISK, not just in memory: a fresh instance
        # (cold cache) must see the reconciled state.
        fresh = FaissVectorStore(index_dir=dir_)
        on_disk = fresh.load_metadata()
        assert len(on_disk["chunks"]) == 4
        assert on_disk["chunks"][2].get("unavailable") is True

        # Search skips the orphan slots: a query matching the row-0 vector
        # hits both slot 0 (real) and slot 2 (orphan, same vector) — only
        # the real one may come back.
        results = fresh.search(_fake_vectors(1), top_k=10)
        assert {r["content"] for r in results} == {"alpha", "beta"}, (
            f"orphan slots leaked into search results: {[r.get('content') for r in results]}"
        )
        assert not any(r.get("unavailable") for r in results)
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_add_vectors_after_index_ahead_crash_keeps_slots_aligned():
    """#1704 end-to-end: after add_vectors crashed between its index.faiss
    write and its metadata.json write (index 1 vector ahead), the next
    add_vectors must number its new chunks onto REAL vector slots
    (base_idx == ntotal), not onto the slots the orphan vectors occupy —
    and retrieval must pair each vector with its own chunk's metadata.

    Geometry (seed 99, dim 8): v_orphan is a near-duplicate of v_alpha, so
    a v_orphan query ranks slot 2 (the orphan) strictly first and slot 0
    (alpha) second; a v_gamma query ranks slot 3 (gamma) strictly first.
    That makes the old mis-pairing observable: pre-fix, the orphan slot's
    hit was answered with the NEW document's metadata (张冠李戴).
    """
    import faiss

    rng = np.random.default_rng(99)
    v_alpha, v_beta, v_gamma = (rng.random((1, 8)).astype(np.float32) for _ in range(3))
    v_orphan = v_alpha + np.float32(0.01)  # nearest neighbour of itself

    store, dir_ = _tmp_store()
    try:
        store.add_vectors(np.vstack([v_alpha, v_beta]), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
        ])

        # Crash window: index.faiss lands with 1 extra (orphan) vector,
        # the metadata write never happens.
        _crash_window_index_ahead(dir_, np.vstack([v_alpha, v_beta, v_orphan]))
        store._index = None  # next op reloads the crashed index from disk

        # The upload that arrives after the crash.
        store.add_vectors(v_gamma, [{"document_id": "d3", "content": "gamma"}])

        meta = store.load_metadata()
        disk_idx = faiss.read_index(os.path.join(dir_, "index.faiss"))
        assert disk_idx.ntotal == len(meta["chunks"]) == 4, (
            f"index/metadata diverged after post-crash add: ntotal="
            f"{disk_idx.ntotal}, chunks={len(meta['chunks'])}"
        )
        # The new chunk must own vector slot 3 — NOT the orphan's slot 2.
        new_chunk = meta["chunks"][3]
        assert new_chunk["index"] == 3, (
            f"#1704: new chunk numbered onto a stale slot: {new_chunk}"
        )
        assert new_chunk["document_id"] == "d3" and new_chunk["content"] == "gamma"
        assert meta["chunks"][2].get("unavailable") is True, (
            f"orphan slot 2 was overwritten by the new chunk's metadata: "
            f"{meta['chunks'][2]}"
        )

        # Query the orphan vector: pre-fix its top hit (slot 2) was answered
        # with d3/gamma's metadata; post-fix the slot is skipped and the
        # top hit is the real nearest neighbour, alpha.
        top = store.search(v_orphan, top_k=1)
        assert len(top) == 1
        assert top[0]["content"] == "alpha", (
            f"#1704: orphan slot surfaced wrong metadata: {top[0]}"
        )
        assert top[0]["document_id"] == "d1"

        # Query gamma's own vector: must hit gamma's own metadata.
        top_gamma = store.search(v_gamma, top_k=1)
        assert top_gamma[0]["content"] == "gamma"
        assert top_gamma[0]["document_id"] == "d3"

        # No result may ever carry the placeholder marker.
        for r in store.search(v_orphan, top_k=10) + store.search(v_gamma, top_k=10):
            assert not r.get("unavailable")
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)


def test_add_vectors_refuses_when_recovery_cannot_persist(monkeypatch):
    """#1704 belt-and-braces: if recovery itself cannot persist the repair
    (disk full / permission denied on metadata.json), add_vectors must
    refuse to write instead of numbering new chunks from the still-divergent
    metadata — which would silently overwrite orphan vector slots.
    """
    import app.services.rag.faiss_store as mod

    store, dir_ = _tmp_store()
    try:
        store.add_vectors(_fake_vectors(2), [
            {"document_id": "d1", "content": "alpha"},
            {"document_id": "d2", "content": "beta"},
        ])

        # Crash window: index 1 ahead of metadata.
        _crash_window_index_ahead(dir_, _fake_vectors(3))
        store._index = None

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(mod, "_atomic_write_json", boom)

        with pytest.raises(RagPersistenceError):
            store.add_vectors(_fake_vectors(1), [{"document_id": "d3", "content": "gamma"}])

        # Nothing was written: metadata still 2 chunks, index still the
        # crashed 3-vector one (the refused add left no trace).
        fresh = FaissVectorStore(index_dir=dir_)
        assert [c["document_id"] for c in fresh.load_metadata()["chunks"]] == ["d1", "d2"]
        import faiss

        assert faiss.read_index(os.path.join(dir_, "index.faiss")).ntotal == 3
    finally:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)
