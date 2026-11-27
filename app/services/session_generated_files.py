"""Read-only catalogue of registered files for an already authorized chat session."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.schemas.chat_schema import GeneratedFileItem
from app.schemas.data_delivery_schema import DeliveryJobView
from app.services import artifact_registry, export_storage, report_service
from app.services.data_delivery import store as delivery_store
from app.services.data_delivery.service import list_completed_session_deliveries
from app.services.data_delivery.source_resolver import DeliveryActor


def _epoch(created: datetime | None) -> float | None:
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.timestamp()


def _safe_delivery_name(name: str) -> bool:
    return 0 < len(name) <= 255 and name != "." and all(
        part not in name for part in ("/", "\\", "..", ":", "\0")
    )


def _delivery_files(jobs: Sequence[DeliveryJobView]) -> list[GeneratedFileItem]:
    items = []
    for job in jobs:
        receipt = job.receipt
        if not receipt or receipt.job_id != job.job_id or not job.download_url:
            continue
        if not _safe_delivery_name(receipt.delivery_id):
            continue
        directory = delivery_store.final_dir(receipt.delivery_id)
        if not receipt.members:
            continue
        primary = next((m for m in receipt.members if m.role == "data"), receipt.members[0])
        created = _epoch(datetime.fromisoformat(job.created_at)) if job.created_at else None
        for member in receipt.members:
            name = member.path
            if not _safe_delivery_name(name):
                continue
            path = directory / name
            url = None
            if path.is_file() and not path.is_symlink() and not directory.is_symlink():
                url = job.download_url
                if member is not primary:
                    url += "?" + urlencode({"member": name})
            items.append(GeneratedFileItem(
                id=f"delivery:{job.job_id}:{name}", name=name,
                format=path.suffix[1:].lower(), source="delivery",
                created_at=created, download_url=url,
            ))
    return items


def _project_files(
    records: Sequence[artifact_registry.ArtifactRecord],
    reports: Sequence[Report],
    deliveries: Sequence[DeliveryJobView],
    session_id: str,
    user_id: str | None,
) -> list[GeneratedFileItem]:
    session_owner = f"session:{session_id}"
    items = _delivery_files(deliveries)
    for record in records:
        ref = record.artifact_id
        if record.session_id != session_id or not artifact_registry.is_export_ref(ref):
            continue
        path = artifact_registry.export_file_path(ref)
        if path is None:
            continue
        # Ledger membership is provenance, not download authority. Only mint a
        # bearer URL after checking the receipt against this exact session.
        owner = export_storage.get_export_owner(path.name)
        if not owner or owner not in (session_owner, user_id):
            continue
        url = None
        if path.is_file() and not path.is_symlink():
            url = export_storage.export_download_url(path.name, owner)
        items.append(GeneratedFileItem(
            id=ref, name=path.name, format=path.suffix[1:], source="export",
            created_at=record.created_at or None, download_url=url,
        ))

    for report in reports:
        ext = report_service.file_ext(report.format)
        name = report.title or f"report_{report.id[:8]}"
        if not name.lower().endswith(f".{ext}"):
            name = f"{name}.{ext}"
        url = None
        if (report.status == "completed" and report.file_path
                and report_service.validate_report_path(report.file_path, report_service.REPORT_DIR)
                and os.path.isfile(report.file_path)):
            url = report_service.report_download_url(report.id)
        items.append(GeneratedFileItem(
            id=f"report:{report.id}", name=name, format=ext, source="report",
            created_at=_epoch(report.created_at), download_url=url,
        ))

    return sorted(items, key=lambda item: (item.created_at is None, -(item.created_at or 0), item.id))


async def list_generated_files(
    db: AsyncSession, session_id: str, user: dict, owner_token: str | None,
) -> list[GeneratedFileItem]:
    actor = DeliveryActor.from_user_dict(user)
    actor.owner_token = owner_token
    deliveries = await list_completed_session_deliveries(actor, session_id)
    records = await artifact_registry.list_artifacts(session_id)
    reports: Sequence[Report] = []
    # Report downloads also require a signed-in identity, even on a token-owned session.
    if actor.user_id:
        reports = (await db.scalars(select(Report).where(Report.session_id == session_id))).all()
    return await asyncio.to_thread(_project_files, records, reports, deliveries, session_id, actor.user_id)
