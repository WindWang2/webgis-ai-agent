"""Bounded publication rendering in a disposable process group."""
from __future__ import annotations

import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.publication_export import PublicationPdfResult

_SLOT = threading.BoundedSemaphore(1)
_MAX_PDF_BYTES = 50 * 1024 * 1024
_MAX_RECEIPT_BYTES = 2 * 1024 * 1024


def _render(payload: dict, options: dict, directory: str) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    root = Path(directory)
    try:
        from app.services.publication_export import render_publication_pdf
        result = render_publication_pdf(payload, **options)
        if len(result.pdf) > _MAX_PDF_BYTES:
            raise ValueError("publication output exceeds 50MB")
        metadata = dataclasses.asdict(result)
        metadata.pop("pdf")
        encoded = json.dumps(metadata, ensure_ascii=False).encode()
        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise ValueError("publication receipt exceeds 2MB")
        (root / "result.pdf").write_bytes(result.pdf)
        (root / "receipt.json").write_bytes(encoded)
    except Exception as exc:
        (root / "error.json").write_text(json.dumps({
            "type": type(exc).__name__, "code": str(getattr(exc, "code", ""))[:96],
            "message": str(exc)[:2000],
        }))


def render_publication_bounded(payload: dict, *, timeout_s: float = 110.0, **options: Any) -> "PublicationPdfResult":
    from app.services.publication_export import PublicationBusyError, PublicationUnavailableError, PublicationPdfResult
    from app.lib.cartography.mapspec_schema import MapSpecSchemaError

    if not _SLOT.acquire(blocking=False):
        raise PublicationBusyError("vector pdf renderer is busy; retry later")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="webgis-publication-") as directory:
            process = multiprocessing.get_context("spawn").Process(
                target=_render, args=(payload, options, directory), daemon=False,
            )
            try:
                process.start()
                process.join(max(0.0, timeout_s - (time.monotonic() - started)))
                if process.is_alive():
                    raise TimeoutError("publication rendering deadline exceeded")
                root = Path(directory)
                error = root / "error.json"
                if error.is_file():
                    detail = json.loads(error.read_bytes()[:8192])
                    if detail["type"] == "MapSpecSchemaError":
                        raise MapSpecSchemaError(detail["code"], detail["message"])
                    if detail["type"] == "PublicationUnavailableError":
                        raise PublicationUnavailableError(detail["message"])
                    raise ValueError("publication rendering failed: " + detail["message"])
                pdf, receipt = root / "result.pdf", root / "receipt.json"
                if process.exitcode != 0 or not pdf.is_file() or not receipt.is_file():
                    raise PublicationUnavailableError("publication worker did not produce a result")
                if pdf.stat().st_size > _MAX_PDF_BYTES or receipt.stat().st_size > _MAX_RECEIPT_BYTES:
                    raise ValueError("publication output budget exceeded")
                return PublicationPdfResult(pdf=pdf.read_bytes(), **json.loads(receipt.read_bytes()))
            finally:
                if process.pid is not None and process.is_alive():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (AttributeError, ProcessLookupError, PermissionError):
                        process.kill()
                    process.join(5)
                if process.pid is not None:
                    process.close()
    finally:
        _SLOT.release()
        from app.core.sre_metrics import observe_export_duration
        observe_export_duration(time.monotonic() - started)
