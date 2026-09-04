"""RasterSource — the raster identity descriptor (no dataset I/O at build).

A source answers "where is this raster and what is it" without opening the
dataset: ``uri`` plus a :class:`SourceKind`. Identity fields (crs, bbox,
shape, dtype, bands, nodata, transform, fingerprint) resolve lazily through
a :class:`RasterReader` on first use and are cached on the descriptor.

Supported kinds today: local file paths (incl. COG), session-store refs
(``ref:raster/...`` / ``ref:geojson``-style opaque refs with raster
payloads), and project artifacts (artifact_id → storage_ref resolution).
Remote imagery (``/vsi...``) is addressable via the same descriptor — the
reader never assumes locality.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.lib.geo_raster.reader import RasterMetadata, RasterReader

#: URI prefixes the session store uses for raster payloads (PNG products +
#: GeoTIFF refs). Mirrors app/services/tool_dispatch_service raster minting.
_RASTER_REF_PREFIXES = ("ref:raster/",)
#: Opaque geojson-prefixed refs may still carry raster payloads in some
#: legacy flows; the reader rejects non-raster payloads with a structured
#: error rather than guessing.
_GEOJSON_REF_PREFIX = "ref:geojson"


class RasterSourceError(ValueError):
    """Structured source rejection (unknown scheme, missing file, non-raster
    payload). Never a silent fallback to some other raster."""


@dataclass
class RasterSource:
    """Where a raster lives + its lazily-resolved identity."""

    uri: str
    session_id: Optional[str] = None
    _metadata: Optional[RasterMetadata] = field(default=None, repr=False)
    _reader: Optional[RasterReader] = field(default=None, repr=False)

    # ── construction helpers ─────────────────────────────────────────
    @classmethod
    def from_path(cls, path: str | Path) -> "LocalFileRasterSource":
        p = Path(path)
        if not p.is_file():
            raise RasterSourceError(f"raster file not found: {p}")
        return LocalFileRasterSource(uri=str(p.resolve()))

    @classmethod
    def from_ref(cls, ref: str, session_id: str) -> "SessionRefRasterSource":
        if not ref.startswith("ref:"):
            raise RasterSourceError(f"not a session ref: {ref!r}")
        return SessionRefRasterSource(uri=ref, session_id=session_id)

    @classmethod
    def from_project_artifact(cls, artifact_id: str, project_id: str) -> "ProjectArtifactRasterSource":
        return ProjectArtifactRasterSource(uri=f"artifact://{project_id}/{artifact_id}")

    @classmethod
    def auto(cls, uri: str, *, session_id: Optional[str] = None) -> "RasterSource":
        """Best-effort classification: file path vs ref vs artifact URI."""
        if uri.startswith("ref:"):
            if session_id is None:
                raise RasterSourceError(
                    f"session ref {uri!r} needs a session_id to resolve"
                )
            return cls.from_ref(uri, session_id)
        if uri.startswith("artifact://"):
            _, _, rest = uri.partition("artifact://")
            project_id, _, artifact_id = rest.partition("/")
            if not project_id or not artifact_id:
                raise RasterSourceError(f"malformed artifact uri: {uri!r}")
            return cls.from_project_artifact(artifact_id, project_id)
        if uri.startswith(("/vsicurl/", "/vsis3/", "/vsigs/", "/vsi", "http://", "https://")):
            return RemoteRasterSource(uri=uri)
        return cls.from_path(uri)

    # ── identity (lazy, cached) ──────────────────────────────────────
    @property
    def kind(self) -> str:
        if self.uri.startswith("ref:"):
            return "session_ref"
        if self.uri.startswith("artifact://"):
            return "project_artifact"
        if self.uri.startswith(("/vsicurl/", "/vsis3/", "/vsigs/", "http://", "https://")):
            return "remote"
        return "local_file"

    def reader(self) -> RasterReader:
        """Open (or reuse) the dataset reader for this source."""
        if self._reader is None or self._reader.closed:
            self._reader = self._open_reader()
        return self._reader

    def _open_reader(self) -> RasterReader:  # pragma: no cover - overridden
        raise RasterSourceError(
            f"cannot open source kind {self.kind!r} from the base class"
        )

    def metadata(self) -> RasterMetadata:
        if self._metadata is None:
            self._metadata = self.reader().metadata()
        return self._metadata

    # convenience identity accessors (resolve through metadata)
    @property
    def crs(self) -> Optional[str]:
        return self.metadata().crs

    @property
    def bbox(self) -> Optional[list[float]]:
        return self.metadata().bbox

    @property
    def shape(self) -> Optional[tuple[int, int]]:
        m = self.metadata()
        return (m.height, m.width) if m is not None else None

    @property
    def dtype(self) -> Optional[str]:
        return self.metadata().dtype

    @property
    def bands(self) -> Optional[int]:
        return self.metadata().count

    @property
    def nodata(self) -> Optional[float]:
        return self.metadata().nodata

    @property
    def fingerprint(self) -> str:
        return self.metadata().fingerprint

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def __enter__(self) -> "RasterSource":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@dataclass
class LocalFileRasterSource(RasterSource):
    """A raster on the local filesystem (GeoTIFF/COG/any GDAL-readable)."""

    def _open_reader(self) -> RasterReader:
        return RasterReader.open(self.uri)


@dataclass
class RemoteRasterSource(RasterSource):
    """A remote raster addressed by GDAL virtual filesystem or http(s) URI.
    Locality is never assumed — reads go through the hardened env exactly
    like STAC COG assets."""

    def _open_reader(self) -> RasterReader:
        return RasterReader.open(self.uri)


@dataclass
class COGRasterSource(LocalFileRasterSource):
    """A Cloud-Optimized GeoTIFF (validated as COG on first open; the
    structural check is advisory — a plain GTiff still reads, it just loses
    range-read efficiency)."""

    def metadata(self) -> RasterMetadata:
        # super() already computes is_cog from the OPEN dataset — recomputing
        # from the uri would double-open (review finding #9).
        return super().metadata()


@dataclass
class SessionRefRasterSource(RasterSource):
    """A raster payload stored in the session store under a ref."""

    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.session_id:
            raise RasterSourceError("SessionRefRasterSource requires session_id")

    def _open_reader(self) -> RasterReader:
        from app.services.session_data import session_data_manager

        payload = None
        try:
            payload = asyncio_run(
                session_data_manager.get(self.session_id, self.uri)
            )
        except Exception as e:  # noqa: BLE001 — structured, not silent
            raise RasterSourceError(
                f"session ref {self.uri!r} could not be read: {e}"
            ) from e
        if payload is None:
            raise RasterSourceError(f"session ref {self.uri!r} has no payload")
        # Raster refs minted by the dispatch seam carry {path, bbox, ...};
        # legacy flows may store raw bytes — the reader handles both.
        path = None
        if isinstance(payload, dict):
            path = payload.get("path") or payload.get("raster_path")
        if path and Path(str(path)).is_file():
            return RasterReader.open(str(path))
        raise RasterSourceError(
            f"session ref {self.uri!r} does not carry a readable raster "
            f"payload (got {type(payload).__name__}"
            + (" without a file path)" if isinstance(payload, dict) else ")")
        )


@dataclass
class ProjectArtifactRasterSource(RasterSource):
    """A raster promoted into the project artifact store."""

    def _open_reader(self) -> RasterReader:
        # Artifact ids resolve through the project artifact table; keep the
        # resolution lazy so this module stays I/O-free at import.
        try:
            from app.models.project import Artifact
            from app.core.database import SessionLocal
        except Exception as e:  # pragma: no cover - import guard
            raise RasterSourceError(f"artifact resolution unavailable: {e}") from e
        artifact_id = self.uri.rsplit("/", 1)[-1]
        with SessionLocal() as db:
            row = db.get(Artifact, artifact_id)
            if row is None or not row.storage_ref:
                raise RasterSourceError(
                    f"project artifact {artifact_id!r} not found or has no storage"
                )
            ref = str(row.storage_ref)
        if ref.startswith("ref:"):
            raise RasterSourceError(
                f"project artifact {artifact_id!r} stores a session ref "
                f"({ref!r}); project-scoped raster refs are not resolvable "
                "through the session store by project id — resolve the owning "
                "session first"
            )
        return RasterReader.open(ref)


def asyncio_run(coro: Any) -> Any:
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from a live loop (tool thread or async context): run on a
    # throwaway loop in a worker thread — session_data's sync callers use
    # this module from sync tool code.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()
