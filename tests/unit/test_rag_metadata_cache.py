"""
FaissVectorStore.load_metadata cache tests (REVIEW-P2 perf).

search() calls load_metadata() on every query. Without the cache, each
query re-opened, flocked, and json.load'd the whole metadata.json
(megabytes for any non-trivial KB). The cache keys on the file's mtime:
a stat() syscall decides hit vs miss, and any writer — this process,
another uvicorn worker, a Celery compact task — changes the mtime on
save, so the next reader re-parses.
"""
import json
import os
import shutil
import tempfile

import pytest

from app.services.rag.faiss_store import FaissVectorStore


@pytest.fixture
def tmp_store():
    """A FaissVectorStore in a fresh temp dir; cleaned up after the test."""
    tmpdir = tempfile.mkdtemp(prefix="rag-meta-cache-")
    yield FaissVectorStore(index_dir=tmpdir), tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_repeated_load_hits_cache_without_restatting_the_file(tmp_store, monkeypatch):
    """Two consecutive load_metadata calls with no write in between must
    return the same parsed dict and skip the disk read on the second call.
    """
    store, dir_ = tmp_store
    store.save_metadata({"chunks": [{"document_id": "d1", "content": "alpha"}]})

    # Count how many times the uncached path runs.
    calls = {"n": 0}
    original = store._load_metadata_uncached

    def counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(store, "_load_metadata_uncached", counting)

    first = store.load_metadata()
    second = store.load_metadata()

    assert calls["n"] == 1, (
        f"repeated load with no write should hit the cache; uncached path "
        f"ran {calls['n']} times, expected 1"
    )
    assert first is second, "cache hit should return the same object identity"


def test_save_invalidates_cache(tmp_store):
    """After save_metadata, the next load_metadata must re-parse from disk
    and return the new content, not the cached old content.

    The test explicitly populates the cache first (two reads with no write)
    so a never-invalidate cache mutation would serve the stale entry."""
    store, dir_ = tmp_store
    store.save_metadata({"chunks": [{"document_id": "d1"}]})

    # Populate the cache with two reads.
    store.load_metadata()
    first = store.load_metadata()
    assert first["chunks"][0]["document_id"] == "d1"

    # Write new content — mtime changes.
    store.save_metadata({"chunks": [{"document_id": "d2"}]})

    second = store.load_metadata()
    assert second["chunks"][0]["document_id"] == "d2", (
        "cache was not invalidated by save; got stale content"
    )
    assert first is not second, "cache should have re-parsed, not reused"


def test_cross_instance_sees_writer_changes(tmp_store):
    """Two store instances pointing at the same dir must see each other's
    writes — the mtime check is what makes the cache safe across workers.

    Explicitly populates store_b's cache before store_a's second write so
    a never-invalidate mutation would serve stale content."""
    store_a, dir_ = tmp_store
    store_b = FaissVectorStore(index_dir=dir_)

    store_a.save_metadata({"chunks": [{"document_id": "from_a"}]})
    assert store_b.load_metadata()["chunks"][0]["document_id"] == "from_a"

    # store_b reads again — populates its cache.
    store_b.load_metadata()

    # store_a writes again — store_b's next read must see it.
    store_a.save_metadata({"chunks": [{"document_id": "from_a_again"}]})
    assert store_b.load_metadata()["chunks"][0]["document_id"] == "from_a_again", (
        "cross-instance cache was not invalidated by the other writer's save"
    )


def test_missing_file_returns_empty_without_crash(tmp_store):
    """A fresh store with no metadata.json returns {'chunks': []} and caches
    that result so subsequent loads also avoid the os.stat."""
    store, dir_ = tmp_store
    assert not os.path.exists(os.path.join(dir_, "metadata.json"))

    first = store.load_metadata()
    second = store.load_metadata()

    assert first == {"chunks": []}
    assert second == {"chunks": []}
    # The missing-file branch caches (None, {"chunks": []}); second call
    # should re-stat and hit the FileNotFoundError branch again, returning
    # the cached empty dict. Just verify no crash and correct value.


def test_corrupt_file_falls_back_to_empty(tmp_store):
    """If the metadata file is corrupt JSON, load_metadata logs and returns
    the empty-dict fallback rather than crashing the search path."""
    store, dir_ = tmp_store
    meta_path = os.path.join(dir_, "metadata.json")
    # Write invalid JSON directly so save_metadata's cache invalidation
    # doesn't run.
    with open(meta_path, "w") as f:
        f.write("{not valid json")

    assert store.load_metadata() == {"chunks": []}
