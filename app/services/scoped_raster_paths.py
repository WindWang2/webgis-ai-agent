"""Local raster paths authorized by an already authenticated owner scope."""
from __future__ import annotations

import re
from pathlib import Path

from app.services.data_fabric.security import DataFabricSecurityError, resolve_safe_local_path


def scoped_raster_roots(*, session_id: str | None = None, project_id: str | None = None) -> list[Path]:
    from app.core.config import settings
    from app.services.mapspec.store import BASE_STORAGE_DIR

    if bool(session_id) == bool(project_id):
        raise DataFabricSecurityError("raster access requires exactly one owner scope")
    key = str(session_id or project_id)
    reserved = {"uploads", "projects", "modelops", "content", "blobs", "cache", "exports", "tmp"}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) or key.lower() in reserved:
        raise DataFabricSecurityError("invalid raster owner scope")
    data_root = Path(settings.DATA_DIR).resolve()
    roots = ([data_root / key, BASE_STORAGE_DIR.resolve() / key] if session_id
             else [data_root / "projects" / key])
    for root in roots:
        if root.resolve() != root.parent.resolve() / root.name:
            raise DataFabricSecurityError("raster owner directory escapes its storage root")

    # Upload directories use random upload IDs, not the session ID. Resolve
    # them through the ownership ledger rather than trusting a client path.
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.upload import UploadRecord

    with SessionLocal() as db:
        if session_id:
            query = select(UploadRecord.id, UploadRecord.filename).where(UploadRecord.session_id == session_id)
        else:
            from app.models.project import ProjectDataset

            refs = db.execute(select(ProjectDataset.source_ref).where(
                ProjectDataset.project_id == project_id,
                ProjectDataset.source_type == "upload",
                ProjectDataset.detached_at.is_(None),
            )).scalars().all()
            ids = [int(ref) for ref in refs if str(ref).isdigit()]
            query = select(UploadRecord.id, UploadRecord.filename).where(UploadRecord.id.in_(ids))
        for upload_id, filename in db.execute(query).all():
            candidate = Path(str(filename or ""))
            uploads_root = data_root / "uploads"
            if not candidate.is_absolute():
                candidate = data_root / candidate if candidate.parts and candidate.parts[0] == "uploads" else uploads_root / candidate
            try:
                relative = candidate.relative_to(uploads_root)
            except ValueError:
                raise DataFabricSecurityError("owned upload points outside upload storage") from None
            if len(relative.parts) < 2:
                continue
            upload_key = relative.parts[0]
            if re.fullmatch(r"[A-Za-z0-9_-]+", upload_key):
                upload_root = data_root / "uploads" / upload_key
                if upload_root.resolve() != upload_root.parent.resolve() / upload_root.name:
                    raise DataFabricSecurityError("upload directory redirects its owner identity")
                if not candidate.resolve().is_relative_to(upload_root.resolve()):
                    raise DataFabricSecurityError("owned upload file escapes its upload directory")
                roots.append(upload_root)
    return roots


def resolve_scoped_raster_path(uri: str, *, session_id: str | None = None,
                               project_id: str | None = None, roots: list[Path] | None = None) -> str:
    if not isinstance(uri, str) or uri.startswith("/vsi") or "://" in uri:
        raise DataFabricSecurityError("raster source must be a local file in the owner scope")
    allowed = roots if roots is not None else scoped_raster_roots(session_id=session_id, project_id=project_id)
    if not allowed:
        raise DataFabricSecurityError("no authorized raster roots")
    return str(resolve_safe_local_path(uri, allowed, 2**63 - 1))
