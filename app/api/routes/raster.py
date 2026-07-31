"""Raster image serving route (ADR-0011).

Serves the colormap-baked PNGs that back `type:"raster"` MapSpec sources. PNGs
live at `.webgis-agent/<sid>/raster/<raster_id>.png` and are referenced by an
opaque `ref:raster/<id>` cursor on the source; this route is the only path that
resolves that cursor to a URL MapLibre can fetch.

Session-scoped by design: `session_id` is the capability token (per CONTEXT.md
"the session_id acts as a capability token for data access"). No separate
ownership check — same authorization model as every other session-scoped read.
A `raster_id` is validated to be a plain identifier (no path traversal).
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.mapspec_store import BASE_STORAGE_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

# raster_id is a filename-safe identifier (alphanumeric + underscore + hyphen).
# Rejects anything path-traversal-shaped (`.`, `..`, `/`, `\`) before touching disk.
_RASTER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@router.get(
    "/sessions/{session_id}/raster/{raster_id}.png",
    tags=["raster"],
    summary="Serve a raster layer's rendered PNG (MapSpec `type:\"raster\"` source).",
)
async def get_raster_png(session_id: str, raster_id: str):
  # Both path segments are filesystem-interpolated; validate both to a plain
  # identifier charset BEFORE any disk access. Crucially, session_id must NOT
  # allow `.`/`..` — those would defeat the resolve()-based escape check below
  # (the prior looser regex admitted them; the strict check here is the real
  # defense). Same charset as raster_id.
  if not _RASTER_ID_RE.match(raster_id):
    raise HTTPException(status_code=400, detail="Invalid raster_id")
  if not _RASTER_ID_RE.match(session_id):
    raise HTTPException(status_code=400, detail="Invalid session_id")

  png_path = BASE_STORAGE_DIR / session_id / "raster" / f"{raster_id}.png"
  if not png_path.exists():
    raise HTTPException(status_code=404, detail="Raster image not found")

  # Defense in depth: resolve and confirm the file stayed under the session's
  # raster dir. The strict regex above is the primary defense; this catches
  # symlink escape or any future regression in path construction.
  try:
    resolved = png_path.resolve()
    expected_root = (BASE_STORAGE_DIR / session_id / "raster").resolve()
    # relative_to raises ValueError on escape — stronger than startswith.
    resolved.relative_to(expected_root)
  except ValueError:
    raise HTTPException(status_code=400, detail="Invalid raster path")
  except Exception as e:
    logger.warning("Raster path resolve failed for %s/%s: %s", session_id, raster_id, e)
    raise HTTPException(status_code=400, detail="Invalid raster path")

  return FileResponse(resolved, media_type="image/png")
