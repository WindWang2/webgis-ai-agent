"""Recoverable replacement of FAISS index/metadata pairs under the store lock.

A pending marker means rollback, even after process death. Backups remain
immutable until marker removal commits the pair, so interrupted rollback is
idempotent. Callers must hold the store's sidecar lock across reads and writes.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Iterator

logger = logging.getLogger(__name__)
_MARKER = ".rag-pair.pending"
_BACKUP = ".rag-pair.rollback"
_FILES = ("index.faiss", "metadata.json")


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return  # Windows has the same documented process-local lock boundary.
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_backup(directory: Path) -> None:
    try:
        shutil.rmtree(directory / _BACKUP)
    except FileNotFoundError:
        return
    except OSError:
        # Marker has already been removed and fsynced: cleanup is optional.
        logger.warning("RAG committed-pair backup cleanup failed", exc_info=True)


def recover_pending_pair(index_dir: str) -> bool:
    """Restore the old committed pair before serving any new reader/writer."""
    directory = Path(index_dir)
    marker = directory / _MARKER
    try:
        with marker.open("r", encoding="utf-8") as stream:
            state = json.loads(stream.read(256))
    except FileNotFoundError:
        return False
    if not isinstance(state, dict) or set(state) != set(_FILES) or any(type(state[name]) is not bool for name in _FILES):
        raise OSError("Invalid RAG pair recovery marker; refusing mixed-generation reads")
    for name in _FILES:
        target = directory / name
        if state[name]:
            # Keep the backup intact: a crash after restoring the first file
            # must allow the next process to replay the WHOLE rollback.
            backup = directory / _BACKUP / name
            staging = directory / (".rag-restore-" + name)
            staging.unlink(missing_ok=True)
            try:
                os.link(backup, staging)
            except OSError:
                shutil.copyfile(backup, staging)
                with staging.open("rb") as stream:
                    os.fsync(stream.fileno())
            os.replace(staging, target)
        else:
            target.unlink(missing_ok=True)
    _sync_directory(directory)
    marker.unlink()
    _sync_directory(directory)
    _cleanup_backup(directory)
    return True


@contextlib.contextmanager
def pair_transaction(index_dir: str) -> Iterator[None]:
    """Commit both replacements or restore the preceding durable generation."""
    directory = Path(index_dir)
    recover_pending_pair(index_dir)
    _cleanup_backup(directory)
    backup_dir = directory / _BACKUP
    backup_dir.mkdir()
    state: dict[str, bool] = {}
    for name in _FILES:
        original = directory / name
        state[name] = original.exists()
        if state[name]:
            try:
                os.link(original, backup_dir / name)
            except OSError:
                shutil.copyfile(original, backup_dir / name)
            with (backup_dir / name).open("rb") as stream:
                os.fsync(stream.fileno())
    _sync_directory(backup_dir)
    _sync_directory(directory)
    marker_staging = directory / (_MARKER + ".tmp")
    with marker_staging.open("w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(marker_staging, directory / _MARKER)
    _sync_directory(directory)
    try:
        yield
        _sync_directory(directory)
        (directory / _MARKER).unlink()
        _sync_directory(directory)
    except BaseException:
        # If rollback itself fails, retain marker/backups; every future read
        # retries recovery or fails closed. Never serve the uncertain pair.
        recover_pending_pair(index_dir)
        raise
    _cleanup_backup(directory)
