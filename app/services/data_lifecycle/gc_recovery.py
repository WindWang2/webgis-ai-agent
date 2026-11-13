"""GC process liveness locks and bounded original-location recovery evidence."""
from __future__ import annotations

from contextlib import contextmanager, suppress
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

from sqlalchemy import text


def _error(message: str) -> Exception:
    from app.services.data_lifecycle.gc_plan import GcPlanError
    return GcPlanError(message)


@contextmanager
def plan_guard(db: Any, plan_id: str, staging_root: Path) -> Iterator[None]:
    """Live owners exclude resume/rollback/purge; process death releases locks.

    PostgreSQL also pins a dedicated session advisory lock across all commits.
    The stable filesystem lock protects shared staging even if that connection
    is lost. Neither lease time nor heartbeat age proves an executor is dead.
    """
    digest = hashlib.sha256(str(plan_id).encode()).digest()
    lock_root = staging_root / '.locks'
    lock_root.mkdir(parents=True, exist_ok=True)
    # 完整 plan 哈希做文件名。前两字节 % 256 只有 256 个桶，不同计划会互相
    # 误报 live executor（fail-closed，但会把无关计划锁死）。
    lock_path = lock_root / f"{digest.hex()}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    connection = None
    acquired = False
    pg_key = int.from_bytes(digest[:8], 'big', signed=True)
    try:
        try:
            if sys.platform == 'win32':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'\0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise _error('GC plan has a live executor or recovery owner') from exc
        bind = db.get_bind()
        if bind.dialect.name == 'postgresql':
            engine = getattr(bind, 'engine', bind)
            connection = engine.connect().execution_options(isolation_level='AUTOCOMMIT')
            if not connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': pg_key}).scalar_one():
                raise _error('GC plan has a live executor or recovery owner')
        yield
    finally:
        try:
            if connection is not None:
                try:
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': pg_key})
                finally:
                    connection.close()
        finally:
            try:
                if acquired:
                    if sys.platform == 'win32':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def canonical_origin(kind: str, object_id: str, lane: str | None, *, legacy: bool = False) -> Path:
    from app.services.data_lifecycle import adapters, gc_plan
    root = {'artifact_cache': adapters._artifact_root, 'cog_output': adapters._cog_root,
            'spill': adapters._spill_root, 'data': adapters._data_root}.get((lane or '') if kind == 'fabric_materialization' else kind)
    if root is None:
        raise _error('GC original location is ambiguous; preserve staging and reconcile trusted origin metadata')
    base = root().resolve()
    candidate: Path | None
    if legacy and kind == 'artifact_cache':
        candidate = base / object_id  # pre-upgrade writer did not append .tif
    else:
        candidate = gc_plan._resolve_file_path(kind, object_id, lane=lane)
    if candidate is None or not candidate.resolve().is_relative_to(base):
        raise _error('GC original location escapes its trusted storage root')
    return candidate.resolve()


def legacy_lane(db: Any, plan: Any, kind: str, object_id: str) -> str | None:
    """Use frozen/DB-origin evidence, never guess between missing source roots."""
    if kind != 'fabric_materialization':
        return None
    from app.models.data_lifecycle import LifecycleObject
    hints = set()
    aliases = {'spill': 'spill', 'data': 'data'}
    entries = (plan.plan_tree or {}).get('objects', [])
    for entry in entries:
        if entry.get('kind') == kind and entry.get('object_id') == object_id:
            value = entry.get('lane')
            if value in aliases:
                hints.add(aliases[value])
    row = db.query(LifecycleObject).filter_by(kind=kind, object_id=object_id).one_or_none()
    if row is not None and isinstance(row.info, dict):
        value = row.info.get('origin_root', row.info.get('lane'))
        if value in aliases:
            hints.add(aliases[value])
        elif value == 'ref_spill':
            # The old semantic label was also emitted from data-root paths
            # containing ref_spill. Infer spill only when that old producer
            # could not have selected this relative ID from the data root.
            from app.services.data_lifecycle import adapters
            if (Path(object_id).suffix == '.json'
                    and 'ref_spill' not in Path(object_id).parts
                    and 'ref_spill' not in adapters._data_root().parts):
                hints.add('spill')
        # Old fabric_geoparquet describes format, not root; both roots could
        # contain it. Require trusted origin_root reconciliation in that case.
    if len(hints) != 1:
        raise _error('Legacy fabric origin is ambiguous; staging retained for trusted origin reconciliation')
    return hints.pop()


def prepare_origins(db: Any, plan: Any, plan_dir: Path) -> list[dict]:
    """Write original locations before moving any bytes; retry consumes same set."""
    path = plan_dir / '.recovery.json'
    if path.exists():
        if path.stat().st_size > 128 * 1024:
            raise _error('GC recovery evidence exceeds its bound')
        value = json.loads(path.read_text())
        if value.get('plan_id') != plan.id or value.get('plan_digest') != plan.plan_digest:
            raise _error('GC recovery evidence does not match the approved plan')
        origins = value.get('origins')
        if not isinstance(origins, list) or len(origins) > 64:
            raise _error('GC recovery evidence is malformed')
        approved = {(str(entry.get('kind') or ''), str(entry.get('object_id') or ''))
                    for entry in (plan.plan_tree or {}).get('objects', [])[:64]}
        seen = set()
        for item in origins:
            if not isinstance(item, dict):
                raise _error('GC recovery origin is malformed')
            if not isinstance(item.get('kind'), str) or not isinstance(item.get('object_id'), str):
                raise _error('GC recovery object identity is malformed')
            if item.get('lane') not in (None, 'spill', 'data'):
                raise _error('GC recovery origin lane is malformed')
            key = (item['kind'], item['object_id'])
            if key not in approved or key in seen:
                raise _error('GC recovery origin is outside the approved plan')
            seen.add(key)
            canonical = canonical_origin(key[0], key[1], item.get('lane'))
            staged = str(Path(key[0]) / (item.get('lane') or 'default') / key[1])
            if item.get('source') != str(canonical) or item.get('staged') != staged:
                raise _error('GC recovery origin differs from its trusted location')
        return origins
    origins = []
    for entry in (plan.plan_tree or {}).get('objects', [])[:64]:
        kind, object_id = str(entry.get('kind') or ''), str(entry.get('object_id') or '')
        if kind not in ('artifact_cache', 'cog_output', 'fabric_materialization'):
            continue
        lane = entry.get('lane')
        if kind == 'fabric_materialization' and lane not in ('spill', 'data'):
            lane = legacy_lane(db, plan, kind, object_id)
        source = canonical_origin(kind, object_id, lane)
        origins.append({'kind': kind, 'object_id': object_id, 'lane': lane,
                        'source': str(source), 'staged': str(Path(kind) / (lane or 'default') / object_id)})
    plan_dir.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=plan_dir, prefix='.recovery-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'protocol': 2, 'plan_id': plan.id, 'plan_digest': plan.plan_digest,
                       'origins': origins}, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp)
    return origins


def restore_target(db: Any, plan: Any, plan_dir: Path, path: Path) -> Path:
    """Resolve only an approved original target; sidecars follow their payload."""
    relative = path.relative_to(plan_dir).as_posix()
    manifest = plan_dir / '.recovery.json'
    if manifest.exists():
        origins = prepare_origins(db, plan, plan_dir)
        for origin in origins:
            staged = Path(str(origin['staged']))
            source = canonical_origin(origin['kind'], origin['object_id'], origin.get('lane'))
            if str(source) != origin.get('source'):
                raise _error('GC original root changed; staging retained')
            if relative == staged.as_posix():
                return source
            if origin['kind'] == 'artifact_cache' and relative == staged.with_suffix('.meta').as_posix():
                return source.with_suffix('.meta')
        raise _error('staged object has no approved original-location evidence')
    # Old staging has kind/object_id, with no lane component. Match the full
    # frozen object ID first (it may itself begin with data/ or spill/).
    for entry in (plan.plan_tree or {}).get('objects', [])[:64]:
        kind, object_id = str(entry.get('kind') or ''), str(entry.get('object_id') or '')
        old_relative = (Path(kind) / object_id).as_posix()
        if relative == old_relative:
            lane = legacy_lane(db, plan, kind, object_id)
            return canonical_origin(kind, object_id, lane, legacy=True)
        current_relative = (Path(kind) / (entry.get('lane') or 'default') / object_id).as_posix()
        if relative == current_relative:
            return canonical_origin(kind, object_id, entry.get('lane'))
    raise _error('legacy staged object is outside the approved recovery set')
