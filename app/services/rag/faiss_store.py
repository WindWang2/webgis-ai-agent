"""
FAISS Vector Store implementation of VectorStoreProtocol.
"""
import contextlib
import fcntl
import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Iterator, List, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Thread-local re-entrancy depth for _write_lock (see its docstring).
_lock_state = threading.local()

INDEX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "vectors_store",
)


class RagPersistenceError(RuntimeError):
    """Raised when the on-disk index or metadata fails to persist.

    Callers (rag_service adapter) catch this and surface a real error to
    the user rather than reporting success for an upload that was lost on
    its way to disk (REVIEW-P0-PROMOTED, faiss_store.add_vectors).
    """


@contextlib.contextmanager
def _write_lock(index_dir: str) -> Iterator[None]:
    """Sidecar lock file that covers BOTH index.faiss and metadata.json.

    The previous fcntl.LOCK_EX on metadata.json alone left index.faiss
    uncovered — concurrent workers (multi-uvicorn or uvicorn + Celery
    compact task) could tear the file mid-write. A single sidecar file
    whose flock spans the whole critical section is the standard fix.

    Re-entrant within a thread. fcntl.flock() on a *second* fd for the
    same file does NOT re-enter — it blocks forever. So a method that
    holds this lock and then calls save_metadata()/save_index() (which
    also take it) would deadlock. Guard with a thread-local depth
    counter: nested acquisitions are no-ops, the outermost one owns the
    real flock. Found by mutating add_vectors to call save_metadata
    while holding the lock, which hung the test suite.
    """
    depth = getattr(_lock_state, "depth", 0)
    if depth > 0:
        # Already held by this thread — nested acquisition is a no-op.
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return

    os.makedirs(index_dir, exist_ok=True)
    lock_path = os.path.join(index_dir, ".rag-write.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _lock_state.depth = 1
        yield
    finally:
        _lock_state.depth = 0
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write_json(target_path: str, payload: dict) -> None:
    """Write payload to target_path atomically.

    Pattern: NamedTemporaryFile in the same directory (so the rename is
    on the same filesystem, required for atomicity), write + fsync, then
    os.replace() which is atomic on POSIX. If the process dies between
    write and rename, the temp file is orphaned and target_path is
    untouched — the previous consistent state survives.
    """
    directory = os.path.dirname(target_path) or "."
    os.makedirs(directory, exist_ok=True)
    # delete=False + manual close so we can os.replace() on Windows too.
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(target_path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        # Best-effort cleanup of orphaned temp file.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _atomic_write_faiss(index_path: str, faiss_index: Any) -> None:
    """Atomic counterpart to faiss.write_index()."""
    directory = os.path.dirname(index_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(index_path) + ".", suffix=".tmp", dir=directory
    )
    os.close(fd)
    try:
        import faiss

        faiss.write_index(faiss_index, tmp_path)
        # fsync the file so the bytes are durable before the rename.
        with contextlib.suppress(OSError):
            with open(tmp_path, "rb") as f:
                os.fsync(f.fileno())
        os.replace(tmp_path, index_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


class FaissVectorStore:
    """Encapsulates FAISS index management, metadata storage, and file locks."""

    def __init__(self, index_dir: str = INDEX_DIR):
        self.index_dir = index_dir
        self._index = None
        self._model = None
        # (mtime_ns, parsed_dict) cache for load_metadata. None until first
        # load. See load_metadata docstring for invalidation semantics.
        self._metadata_cache: Optional[tuple] = None
        # (index_file mtime_ns) of the last loaded index.faiss, None when no
        # file backs the in-memory index. #544: _get_index refreshes the
        # in-memory index on this signal (same protocol as load_metadata),
        # so a cross-process compact/rewrite is picked up before search or
        # add_vectors use stale positions.
        self._index_sig: Optional[int] = None

    def _get_embedding_model(self):
        """Lazy load sentence transformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                logger.info("[RAG] Loaded embedding model: paraphrase-multilingual-MiniLM-L12-v2")
            except Exception as e:
                logger.error(f"[RAG] Failed to load embedding model: {e}")
                raise
        return self._model

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Encode text strings into normalized vector embeddings."""
        model = self._get_embedding_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return np.array(vectors, dtype=np.float32)

    def _stamp_index_sig(self) -> None:
        """Record the index.faiss mtime backing ``self._index``.

        Called after every successful in-process write of index.faiss so the
        mtime signature tracks the file the in-memory index was built from.
        """
        index_file = os.path.join(self.index_dir, "index.faiss")
        try:
            st = os.stat(index_file)
        except OSError:
            self._index_sig = None
            return
        self._index_sig = st.st_mtime_ns

    def _get_index(self, dim: int = 384):
        """Get or initialize FAISS index.

        #544 cross-process staleness: the in-memory index is refreshed on
        the same mtime signal that ``load_metadata`` uses. Any writer (this
        process, another uvicorn worker, a Celery compact task) rewrites
        index.faiss atomically (tempfile + os.replace), so a changed mtime
        means the cached index is stale — positions no longer match
        metadata, and search would return wrong-document content or surface
        deleted content. The mtime check is a single stat() syscall on the
        hot path; the re-read happens only when the file actually changed.
        Writers are serialized by ``_write_lock``, so a lock-free stat+swap
        is safe (no read flock needed).
        """
        index_file = os.path.join(self.index_dir, "index.faiss")
        try:
            file_sig = os.stat(index_file).st_mtime_ns
        except FileNotFoundError:
            file_sig = None

        if self._index is None or self._index_sig != file_sig:
            try:
                import faiss
                meta_file = os.path.join(self.index_dir, "metadata.json")
                if file_sig is not None and os.path.exists(meta_file):
                    self._index = faiss.read_index(index_file)
                    logger.info("[RAG] Loaded existing FAISS index")
                    # REVIEW-P0-PROMOTED, faiss_store startup recovery:
                    # if a crash left index.faiss newer than metadata.json
                    # (or vice versa) the on-disk state is inconsistent.
                    # Trust the index, drop metadata entries that have no
                    # matching vector. Survives the partial-write case
                    # that used to silently corrupt results. Runs on every
                    # mtime-triggered reload too (#544).
                    self._recover_index_metadata_consistency()
                else:
                    self._index = faiss.IndexFlatIP(dim)
                self._index_sig = file_sig
            except Exception as e:
                logger.error(f"[RAG] Failed to init FAISS: {e}")
                raise
        return self._index

    def _recover_index_metadata_consistency(self) -> None:
        """If on-disk index and metadata have diverged, pick a direction and
        log it. The chosen direction is "trust the index" because the
        in-memory index is rebuilt from index.faiss on every load; metadata
        without a backing vector is dead weight.
        """
        try:
            ntotal = int(self._index.ntotal) if self._index is not None else 0
        except Exception:
            return
        try:
            meta = self.load_metadata()
        except Exception:
            return
        chunks = meta.get("chunks", []) if isinstance(meta, dict) else []
        if len(chunks) == ntotal:
            return
        if ntotal < len(chunks):
            # Index behind metadata. A crash between metadata save and
            # index save is the most likely cause. Drop the metadata
            # entries with no vector. (The next add_vectors base_idx will
            # be ntotal, so re-adds are coherent.)
            dropped = len(chunks) - ntotal
            meta["chunks"] = list(chunks)[:ntotal]
            for i, ch in enumerate(meta["chunks"]):
                ch["index"] = i
            try:
                with _write_lock(self.index_dir):
                    _atomic_write_json(
                        os.path.join(self.index_dir, "metadata.json"), meta
                    )
            except Exception as e:
                logger.warning(
                    f"[RAG] recovery: failed to truncate metadata to match index: {e}"
                )
            logger.warning(
                f"[RAG] recovery: index ntotal={ntotal} < metadata chunks={len(chunks)}; "
                f"truncated {dropped} orphaned metadata entries"
            )
        else:
            # Index ahead of metadata. Either the metadata save failed
            # (now handled by add_vectors propagation, but historically
            # was silent) or there is a third file. Surface it.
            logger.warning(
                f"[RAG] recovery: index ntotal={ntotal} > metadata chunks={len(chunks)}; "
                f"vectors exist whose metadata is missing"
            )

    def save_index(self) -> None:
        """Persist FAISS index to disk. Public; preserved for tests; the
        critical-section wrapper is now add_vectors / compact, which is
        where production callers should go. Raises RagPersistenceError on
        write failure so callers don't silently lose the index."""
        if self._index is not None:
            try:
                _atomic_write_faiss(
                    os.path.join(self.index_dir, "index.faiss"), self._index
                )
                self._stamp_index_sig()
                logger.info("[RAG] Saved FAISS index to disk")
            except Exception as e:
                logger.warning(f"[RAG] Failed to save index: {e}")
                raise RagPersistenceError(f"save_index failed: {e}") from e

    def load_metadata(self) -> Dict[str, Any]:
        """Load metadata. Uses a shared read lock so writers don't tear
        the file mid-parse.

        REVIEW-P2 perf: caches the parsed dict keyed by the file's mtime.
        search() calls this on every query; without the cache each query
        re-opened, flocked, and json.load'd the whole file (megabytes for
        any non-trivial KB). The mtime check is a single stat() syscall,
        and when the file hasn't changed we return the cached parse
        directly. Cross-instance correctness is preserved: any writer
        (this process, another uvicorn worker, a Celery compact task)
        changes the mtime on save, so the next reader re-parses.
        """
        meta_file = os.path.join(self.index_dir, "metadata.json")
        try:
            stat = os.stat(meta_file)
        except FileNotFoundError:
            # Cache the empty-dict result too, so a missing-file case
            # also avoids the os.path.exists check on every call.
            self._metadata_cache = (None, {"chunks": []})
            return {"chunks": []}
        except OSError:
            # stat failed for some other reason — fall through to the
            # uncached read path, which has its own error handling.
            return self._load_metadata_uncached(meta_file)

        cached = getattr(self, "_metadata_cache", None)
        if cached is not None:
            cached_mtime, cached_meta = cached
            if cached_mtime == stat.st_mtime_ns:
                return cached_meta

        meta = self._load_metadata_uncached(meta_file)
        self._metadata_cache = (stat.st_mtime_ns, meta)
        return meta

    def _load_metadata_uncached(self, meta_file: str) -> Dict[str, Any]:
        """Read+parse metadata.json from disk, no cache."""
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"[RAG] Failed to load metadata: {e}")
            return {"chunks": []}

    def save_metadata(self, meta: Dict[str, Any]) -> None:
        """Save metadata. Public; preserved for tests. add_vectors and
        compact wrap this in _write_lock for cross-file atomicity."""
        try:
            with _write_lock(self.index_dir):
                _atomic_write_json(
                    os.path.join(self.index_dir, "metadata.json"), meta
                )
        except Exception as e:
            logger.warning(f"[RAG] Failed to save metadata: {e}")
            raise RagPersistenceError(f"save_metadata failed: {e}") from e

    def add_vectors(self, vectors: Any, chunks_metadata: List[Dict[str, Any]]) -> None:
        """Add embedding vectors and associated metadata.

        REVIEW-P0-PROMOTED, faiss_store durability. The previous
        implementation wrote metadata.json then index.faiss with no
        atomicity, and swallowed every exception in the writers. A
        crash between the two writes left the files permanently
        divergent; a disk-full / permission-denied was reported as
        success.

        The fix:
          1. hold a single sidecar write lock for the whole op
          2. build the new metadata in memory, validate size, then
             write the FAISS index first, then the metadata
          3. propagate every OS / IO failure as RagPersistenceError

        Write order chosen so a partial failure loses *at most* the
        new chunks (the index is older, the metadata references
        chunks the index doesn't have, and the next load truncates
        them — see _recover_index_metadata_consistency).

        #544: the index is resolved INSIDE the lock, via the mtime-refreshing
        _get_index. A cross-process compact that happened after a stale
        in-memory load is therefore re-read first, so this call never
        overwrites index.faiss with an index missing other writers' state.
        """
        index_path = os.path.join(self.index_dir, "index.faiss")
        meta_path = os.path.join(self.index_dir, "metadata.json")

        with _write_lock(self.index_dir):
            idx = self._get_index(vectors.shape[1])
            idx.add(vectors)
            try:
                meta = self.load_metadata()
                base_idx = len(meta.get("chunks", []))
                for i, ch_meta in enumerate(chunks_metadata):
                    ch_meta["index"] = base_idx + i
                    meta.setdefault("chunks", []).append(ch_meta)
                # Index first: a crash here leaves the new metadata behind,
                # which the next load truncates (safe direction).
                _atomic_write_faiss(index_path, idx)
                _atomic_write_json(meta_path, meta)
            except RagPersistenceError:
                raise
            except OSError as e:
                raise RagPersistenceError(
                    f"add_vectors persistence failed: {e}"
                ) from e
            # In-memory index now matches disk — refresh the signature so
            # the next _get_index is a cache hit, not a wasteful re-read.
            self._stamp_index_sig()

    def search(
        self,
        query_vector: Any,
        top_k: int = 5,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search top-k most similar vectors with optional tenant filtering."""
        idx = self._get_index()
        fetch_count = min(top_k * 4, idx.ntotal) if idx.ntotal > 0 else top_k
        scores, indices = idx.search(query_vector, fetch_count)
        meta = self.load_metadata()
        results = []
        for score, i in zip(scores[0], indices[0]):
            if i < 0 or i >= len(meta.get("chunks", [])):
                continue
            chunk_meta = dict(meta["chunks"][int(i)])

            if chunk_meta.get("deleted", False):
                continue

            # Tenant filtering check
            if not is_admin and (user_id or org_id):
                c_user = chunk_meta.get("user_id")
                c_org = chunk_meta.get("org_id")
                if c_user and user_id and c_user != user_id:
                    continue
                if c_org and org_id and c_org != org_id:
                    continue

            chunk_meta["score"] = float(score)
            results.append(chunk_meta)
            if len(results) >= top_k:
                break
        return results

    def mark_deleted(self, document_id: str) -> None:
        """Mark chunks of document as deleted in metadata.

        Issue #601: the load+modify+save RMW must happen entirely under
        _write_lock, matching add_vectors/compact. The previous code read
        metadata OUTSIDE the lock and only wrapped the save — a concurrent
        add_vectors (or a compact on a worker thread) could interleave
        between the read and the save, and the stale snapshot then
        overwrote the other writer's state: a concurrent add's new chunks
        vanished from metadata (index kept the vectors, search silently
        skipped them), or a compact's renumbered metadata was clobbered and
        a deleted document resurrected. save_metadata re-enters the
        (thread-local) lock, so nesting is safe.
        """
        with _write_lock(self.index_dir):
            meta = self.load_metadata()
            for ch in meta.get("chunks", []):
                if ch.get("document_id") == document_id:
                    ch["deleted"] = True
            self.save_metadata(meta)

    def compact(self) -> Dict[str, Any]:
        """Compact index by purging deleted chunks and rebuilding FAISS index.

        REVIEW-P0-PROMOTED, faiss_store durability. The previous
        implementation wrote the renumbered metadata FIRST, then rebuilt
        the index. A crash between the two left positions misaligned
        (metadata points to N chunks, index has the old layout with
        indices from a different sequence), and search silently
        returned wrong-chunk content. It also filtered texts on
        `ch.get("content") or ch.get("text")`, dropping content-less
        chunks from the rebuilt index but keeping them in renumbered
        metadata, permanently breaking alignment.

        The fix: build the new index FIRST in memory, atomic-write it,
        then atomic-write the metadata. If the embed/rebuild raises,
        the metadata on disk is unchanged (the lock prevents any
        concurrent reader from seeing partial state).
        """
        import faiss

        with _write_lock(self.index_dir):
            meta = self.load_metadata()
            chunks = meta.get("chunks", [])
            active_chunks = [ch for ch in chunks if not ch.get("deleted", False)]
            purged_count = len(chunks) - len(active_chunks)

            if purged_count == 0:
                return {"purged": 0, "total": len(active_chunks)}

            # Build the new index FIRST. A failure here means nothing
            # has been written yet — the next load sees the old state.
            new_index = None
            rebuildable = [
                ch for ch in active_chunks if ch.get("content") or ch.get("text")
            ]
            if rebuildable:
                texts = [ch.get("content", ch.get("text", "")) for ch in rebuildable]
                vectors = self.embed_texts(texts)
                new_index = faiss.IndexFlatIP(vectors.shape[1])
                new_index.add(vectors)

            # Renumber only the chunks that will actually be in the new
            # index, in the same order as the rebuildable list. Chunks
            # that lack content/text are dropped — they were never
            # addressable by the index anyway, but they must also be
            # removed from the metadata so positions match.
            if new_index is not None:
                kept_chunks = rebuildable
                for new_idx, ch in enumerate(kept_chunks):
                    ch["index"] = new_idx
                meta["chunks"] = kept_chunks
            else:
                # No active chunks had any text — drop them all.
                meta["chunks"] = []

            index_path = os.path.join(self.index_dir, "index.faiss")
            meta_path = os.path.join(self.index_dir, "metadata.json")

            # Index first (atomic), then metadata. If metadata write
            # fails after the index succeeds, _recover_index_metadata_consistency
            # will repair on the next load.
            try:
                if new_index is not None:
                    _atomic_write_faiss(index_path, new_index)
                else:
                    # No rebuildable chunks: drop the index file so a
                    # subsequent search creates a fresh empty one.
                    if os.path.exists(index_path):
                        os.unlink(index_path)

                _atomic_write_json(meta_path, meta)
            except (OSError, RagPersistenceError) as e:
                raise RagPersistenceError(f"compact persistence failed: {e}") from e

            # Update in-memory state to match.
            self._index = new_index
            self._stamp_index_sig()

        logger.info(
            f"[FaissVectorStore] Compacted index: purged {purged_count} "
            f"deleted vectors, kept {len(meta['chunks'])}"
        )
        return {"purged": purged_count, "total": len(meta["chunks"])}

    def get_stats(self) -> Dict[str, Any]:
        """Return index statistics (total_vectors, index_dim, deleted_count)."""
        idx = self._get_index()
        meta = self.load_metadata()
        chunks = meta.get("chunks", [])
        deleted_count = sum(1 for ch in chunks if ch.get("deleted", False))

        return {
            "total_vectors": idx.ntotal,
            "dimension": idx.d,
            "total_chunks": len(chunks),
            "deleted_count": deleted_count,
        }
