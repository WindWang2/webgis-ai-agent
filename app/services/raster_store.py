"""RasterStore — persist computed raster PNGs to the session dir (ADR-0011).

Sibling to `mapspec_checkpoint_store.py` (the extracted-helper pattern): the
store stays the data authority; this owns only the raster-image lifecycle.
PNGs live at `.webgis-agent/<sid>/raster/<raster_id>.png` and are served by the
session-scoped raster route; the `imageRef` cursor returned here is what a
`type:"raster"` MapSpec source carries (and what `mapspec_source.ref()` reads
back at checkpoint time).

Pure-ish by design: functions take a session_dir; no back-reference to the
store. Returns the imageRef (a path-style ref string) the caller stores on the
MapSpec source entry.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# #703-H4：raster_id 字符集白名单（字母/数字/下划线/连字符）。save_png 生成的
# id 恒满足；resolve 侧校验防路径拼接面被 ../ 或分隔符污染（纵深防御）。
_RASTER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# Raster images live under the session dir, parallel to revisions/ and checkpoints/.
_RASTER_SUBDIR = "raster"


def raster_dir(session_dir: Path) -> Path:
  """The per-session raster directory, created on demand."""
  d = session_dir / _RASTER_SUBDIR
  d.mkdir(parents=True, exist_ok=True)
  return d


def save_png(session_dir: Path, raster_id: str, png_bytes: bytes) -> str:
  """Write `png_bytes` to `<session_dir>/raster/<raster_id>.png`.

  Returns the imageRef cursor the MapSpec source should carry. We use a
  `ref:raster/<id>` form (not a raw path) so the ref is opaque and the
  serving route owns path resolution — mirroring how geojson `ref:` cursors
  hide their storage location.
  """
  path = raster_dir(session_dir) / f"{raster_id}.png"
  with open(path, "wb") as f:
    f.write(png_bytes)
  return f"ref:raster/{raster_id}"


def resolve_png_path(session_dir: Path, image_ref: str) -> Optional[Path]:
  """Resolve an imageRef cursor back to its on-disk PNG path, or None.

  Inverse of save_png. Used by the serving route and by checkpoint
  materialization (to copy the PNG into the snapshot).

  #703-H4: raster_id 白名单校验（与服务路由 raster.py 的正则+relative_to
  纪律对齐）——防御纵深。当前仅内部 ref 可达，但路径拼接面不该信任上游。
  """
  if not image_ref.startswith("ref:raster/"):
    return None
  raster_id = image_ref[len("ref:raster/"):]
  if not _RASTER_ID_RE.fullmatch(raster_id):
    return None
  path = raster_dir(session_dir) / f"{raster_id}.png"
  return path if path.exists() else None
