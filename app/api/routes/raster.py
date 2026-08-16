"""Raster image serving route (ADR-0011).

Serves the colormap-baked PNGs that back `type:"raster"` MapSpec sources. PNGs
live at `.webgis-agent/<sid>/raster/<raster_id>.png` and are referenced by an
opaque `ref:raster/<id>` cursor on the source; this route is the only path that
resolves that cursor to a URL MapLibre can fetch.

Ownership contract (SEC-08, issue #408): identical to every sibling session-data
route (`layer.py` ref/MVT, `chat.py` map-state, `report.py`, `upload.py`) —
`verify_session_owner` with user auth or the anonymous session's owner_token.
The token is accepted from the `X-Session-Token` header (sibling-compatible)
OR the `token` query parameter: MapLibre `image` source fetches cannot attach
request headers, so the backend appends the query form when minting image URLs
for token-bearing anonymous sessions. A `raster_id` is validated to be a plain
identifier (no path traversal).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.auth import get_current_user_optional, verify_session_owner
from app.core.database import get_async_db
from app.models.db_model import Conversation
from app.services.mapspec_store import BASE_STORAGE_DIR
from app.tools._utils import async_db_session

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
async def get_raster_png(
    session_id: str,
    raster_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    token: Optional[str] = Query(
        None, max_length=128, description="owner_token 兜底（MapLibre 图片请求无法携带请求头）"
    ),
    db=Depends(get_async_db),
    _user: dict = Depends(get_current_user_optional),
):
  # Both path segments are filesystem-interpolated; validate both to a plain
  # identifier charset BEFORE any disk access. Crucially, session_id must NOT
  # allow `.`/`..` — those would defeat the resolve()-based escape check below
  # (the prior looser regex admitted them; the strict check here is the real
  # defense). Same charset as raster_id. Format checks run before ownership so
  # malformed ids keep their distinct 400 (they never reach the session store).
  if not _RASTER_ID_RE.match(raster_id):
    raise HTTPException(status_code=400, detail="Invalid raster_id")
  if not _RASTER_ID_RE.match(session_id):
    raise HTTPException(status_code=400, detail="Invalid session_id")

  user_id = _user.get("user_id") if isinstance(_user, dict) else None
  if user_id == "anonymous":
    user_id = None
  await verify_session_owner(
      db, session_id, user_id=user_id, owner_token=owner_token or token
  )

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


async def lookup_session_owner_token(session_id: str) -> Optional[str]:
  """The session's anonymous owner_token, if any (for URL minting in tools).

  Authenticated sessions have no owner_token (cookie/bearer auth covers them);
  token-less legacy anonymous sessions need none. Only token-bearing anonymous
  sessions require the query-parameter form on the URLs they hand to MapLibre.
  """
  async with async_db_session() as db:
    row = (
        await db.execute(select(Conversation.owner_token).where(Conversation.id == session_id))
    ).scalar_one_or_none()
    return row if isinstance(row, str) else None
