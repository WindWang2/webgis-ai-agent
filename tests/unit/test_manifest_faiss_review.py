"""S99-002/003/004: complete input identity and failed-write isolation."""
import json
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

from app.services.provenance.manifest import build_run_manifest, compute_run_fingerprint
from app.services.rag.faiss_store import FaissVectorStore, RagPersistenceError


def _manifest(inputs):
    return build_run_manifest(workflow_revision_id="v1", graph_fingerprint="g1",
                              input_bindings=inputs, input_dataset_fingerprints={},
                              steps=[], tool_versions={}, artifacts=[])


@pytest.mark.parametrize("left,right", [
    ({"aoi": {"geometry": {"coordinates": [1, 2]}}}, {"aoi": {"geometry": {"coordinates": [100, 50]}}}),
    ({"text": "x" * 700 + "a"}, {"text": "x" * 700 + "b"}),
    ({"values": list(range(80))}, {"values": list(range(79)) + [999]}),
    ({"data": {"features": [{"id": 1}]}}, {"data": {"features": [{"id": 2}]}}),
])
def test_trimmed_input_differences_still_change_fingerprint(left, right):
    assert compute_run_fingerprint(_manifest(left)) != compute_run_fingerprint(_manifest(right))


def test_input_digest_is_canonical_and_storage_redacted_bounded():
    inputs = {"api_key": "SECRET", "nested": {"password": "PASSWORD"}, "aoi": {"geometry": {"coordinates": [1, 2]}}, "text": "x" * 10000}
    manifest = _manifest(inputs)
    stored = json.dumps(manifest)
    assert "SECRET" not in stored and "PASSWORD" not in stored
    assert "coordinates" not in stored
    assert len(stored) < 4096
    assert manifest["inputs_digest"] == _manifest(dict(reversed(list(inputs.items()))))["inputs_digest"]
    assert inputs["api_key"] == "SECRET"


def test_failed_add_never_revives_on_subsequent_add(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store = FaissVectorStore(str(tmp_path))
    vectors = np.eye(3, dtype=np.float32)
    old = {"document_id": "old", "content": "old", "user_id": "alice"}
    store.add_vectors(vectors[:1], [old])
    cached = store.load_metadata()
    failed = {"document_id": "failed", "content": "MUST NOT REAPPEAR", "user_id": "alice"}
    write_json = mod._atomic_write_json
    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RagPersistenceError):
            store.add_vectors(vectors[1:2], [failed])
    assert "index" not in failed and "index" not in old
    assert [c["document_id"] for c in cached["chunks"]] == ["old"]
    assert json.loads((tmp_path / "metadata.json").read_text())["chunks"][0]["document_id"] == "old"
    assert mod._atomic_write_json is write_json
    store.add_vectors(vectors[2:], [{"document_id": "new", "content": "new", "user_id": "alice"}])
    for reader in (store, FaissVectorStore(str(tmp_path))):
        results = reader.search(vectors[1:2], top_k=10, user_id="alice")
        assert {r["document_id"] for r in results} == {"old", "new"}
        assert all(c.get("document_id") != "failed" for c in reader.load_metadata()["chunks"])


def test_failed_index_write_does_not_advance_live_index(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store = FaissVectorStore(str(tmp_path))
    vec = np.ones((1, 3), dtype=np.float32)
    store.add_vectors(vec, [{"document_id": "old", "content": "old"}])
    live = store._index
    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_faiss", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RagPersistenceError):
            store.add_vectors(vec, [{"document_id": "failed", "content": "bad"}])
    assert live.ntotal == 1
    assert store._get_index(3).ntotal == 1


def test_failed_delete_does_not_poison_cache_or_lose_owner_guard(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store = FaissVectorStore(str(tmp_path))
    vec = np.ones((1, 3), dtype=np.float32)
    store.add_vectors(vec, [{"document_id": "old", "content": "old", "user_id": "alice", "org_id": "one"}])
    cached = store.load_metadata()
    assert store.mark_deleted("old", user_id="bob", org_id="one", enforce_tenant=True) is False
    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RagPersistenceError):
            store.mark_deleted("old", user_id="alice", org_id="one", enforce_tenant=True)
    assert not cached["chunks"][0].get("deleted")
    assert store.search(vec, user_id="alice", org_id="one")[0]["document_id"] == "old"
    assert store.mark_deleted("old", user_id="alice", org_id="one", enforce_tenant=True) is True
    assert store.search(vec, user_id="alice", org_id="one") == []


def _compaction_store(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    vectors = np.eye(3, dtype=np.float32)
    store.add_vectors(vectors, [{"document_id": name, "content": name, "user_id": "alice"} for name in ("deleted", "B", "C")])
    store.mark_deleted("deleted")
    store.embed_texts = lambda texts: vectors[1:]
    return store, vectors


def test_failed_compaction_restores_both_files_and_cached_rows(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store, vectors = _compaction_store(tmp_path)
    cached = store.load_metadata()
    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RagPersistenceError):
            store.compact()
    assert [c["index"] for c in cached["chunks"]] == [0, 1, 2]
    for reader in (store, FaissVectorStore(str(tmp_path))):
        for position, name in ((1, "B"), (2, "C")):
            assert reader.search(vectors[position:position+1], user_id="alice")[0]["document_id"] == name
    assert not (tmp_path / ".rag-pair.pending").exists()
    assert store.compact() == {"purged": 1, "total": 2}
    assert store.search(vectors[2:], user_id="alice")[0]["document_id"] == "C"


@pytest.mark.skipif(os.name != "posix", reason="production cross-process flock contract is POSIX")
def test_process_death_mid_compaction_recovers_pair_for_warm_and_cold_readers(tmp_path):
    store, vectors = _compaction_store(tmp_path)
    store.search(vectors[2:], user_id="alice")  # Populate the parent's caches.

    script = """
import os, sys
from pathlib import Path
import numpy as np
import app.services.rag.faiss_store as mod
child = mod.FaissVectorStore(sys.argv[1])
child.embed_texts = lambda texts: np.eye(3, dtype=np.float32)[1:]
original = mod._atomic_write_json
def die_on_metadata(path, payload):
    if Path(path).name == 'metadata.json':
        os._exit(71)
    return original(path, payload)
mod._atomic_write_json = die_on_metadata
child.compact()
"""
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10, check=False)
    assert process.returncode == 71
    assert (tmp_path / ".rag-pair.pending").exists()
    for reader in (store, FaissVectorStore(str(tmp_path))):
        assert reader.search(vectors[2:], user_id="alice")[0]["document_id"] == "C"
        assert reader.search(vectors[1:2], user_id="alice")[0]["document_id"] == "B"
    assert not (tmp_path / ".rag-pair.pending").exists()


def test_missing_rollback_backup_fails_closed_before_any_search(tmp_path, monkeypatch):
    import app.services.rag.pair_transaction as transaction
    store, vectors = _compaction_store(tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(transaction, "recover_pending_pair", lambda directory: False)
        with pytest.raises(RuntimeError, match="crash"):
            with transaction.pair_transaction(str(tmp_path)):
                raise RuntimeError("crash")
    # Simulate damaged recovery state: never fall through to row-count repair.
    (tmp_path / ".rag-pair.rollback" / "index.faiss").unlink()
    with pytest.raises(RagPersistenceError, match="recovery failed"):
        store.search(vectors[2:], user_id="alice")
    with pytest.raises(RagPersistenceError, match="recovery failed"):
        FaissVectorStore(str(tmp_path)).load_metadata()


def test_search_cannot_observe_half_replaced_compaction_pair(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store, vectors = _compaction_store(tmp_path)
    reader = FaissVectorStore(str(tmp_path))
    reader.search(vectors[2:], user_id="alice")
    paused, resume, reading, finished = (threading.Event() for _ in range(4))
    failures, results = [], []
    original = mod._atomic_write_json

    def gated_metadata(path, payload):
        paused.set()
        assert resume.wait(5)
        return original(path, payload)

    def compact():
        try:
            store.compact()
        except Exception as exc:
            failures.append(exc)

    def query():
        reading.set()
        try:
            results.extend(reader.search(vectors[2:], user_id="alice"))
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(mod, "_atomic_write_json", gated_metadata)
    writer = threading.Thread(target=compact)
    writer.start()
    assert paused.wait(5)
    querying = threading.Thread(target=query)
    querying.start()
    try:
        assert reading.wait(5)
        assert not finished.wait(0.05)
    finally:
        resume.set()
        writer.join(5)
        querying.join(5)
    assert not writer.is_alive() and not querying.is_alive()
    assert failures == []
    assert results[0]["document_id"] == "C"


def test_interrupted_rollback_keeps_both_backups_for_retry(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    import app.services.rag.pair_transaction as transaction
    store, vectors = _compaction_store(tmp_path)
    replace = transaction.os.replace

    def fail_restoring_second_file(source, destination):
        if str(source).endswith(".rag-restore-metadata.json"):
            raise OSError("rollback temporarily unavailable")
        return replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        scoped.setattr(transaction.os, "replace", fail_restoring_second_file)
        with pytest.raises(RagPersistenceError):
            store.compact()
    assert (tmp_path / ".rag-pair.pending").exists()
    assert (tmp_path / ".rag-pair.rollback" / "index.faiss").exists()
    assert (tmp_path / ".rag-pair.rollback" / "metadata.json").exists()
    assert store.search(vectors[2:], user_id="alice")[0]["document_id"] == "C"
    assert not (tmp_path / ".rag-pair.pending").exists()


def test_empty_compaction_metadata_failure_restores_deleted_index(tmp_path, monkeypatch):
    import app.services.rag.faiss_store as mod
    store, _ = _compaction_store(tmp_path)
    store.mark_deleted("B")
    store.mark_deleted("C")
    original_index = (tmp_path / "index.faiss").read_bytes()
    original_meta = (tmp_path / "metadata.json").read_bytes()
    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "_atomic_write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(RagPersistenceError):
            store.compact()
    assert (tmp_path / "index.faiss").read_bytes() == original_index
    assert (tmp_path / "metadata.json").read_bytes() == original_meta
