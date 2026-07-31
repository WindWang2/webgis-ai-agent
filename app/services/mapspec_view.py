"""MapSpecView — the typed view/viewport of a MapSpec (architecture review #3).

Replaces the Dict[str, Any] that carried view data and the magic-value
heuristic that treated center == [0.0, 0.0] as "unset". A user who legitimately
wants the origin (Null Island, common in demos) was misdetected and had their
view clobbered by auto-view injection.

Mirrors the authoritative TS MapSpecView (frontend/lib/mapspec-compiler/types.ts):
Optional fields where absent (None) means "unset", not a sentinel value. The
compiler already defaults missing center/zoom (`spec.view?.center ?? [0, 0]`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MapSpecView(BaseModel):
  """A MapSpec view. Fields are Optional — None means unset, not a magic value."""

  center: Optional[List[float]] = None
  zoom: Optional[float] = None
  pitch: Optional[float] = None
  bearing: Optional[float] = None

  model_config = {"extra": "allow"}

  def center_is_set(self) -> bool:
    """True when a center has been explicitly set (not the absence-of-value)."""
    return self.center is not None

  def is_set(self) -> bool:
    """True when any view field has been explicitly set."""
    return any(
        v is not None
        for v in (self.center, self.zoom, self.pitch, self.bearing)
    )

  def to_dict(self, omit_unset: bool = True) -> Dict[str, Any]:
    """Serialize. By default omits unset fields (matches the TS Optional shape)."""
    data = self.model_dump(exclude_none=omit_unset)
    return data


def view_from_dict(raw: Optional[Dict[str, Any]]) -> MapSpecView:
  """Build a MapSpecView from a raw dict (tolerates missing/None)."""
  if not raw:
    return MapSpecView()
  return MapSpecView.model_validate(raw)


def view_has_center(mapspec: Dict[str, Any]) -> bool:
  """Predicate over a raw MapSpec dict: has a center been explicitly set?

  This is the replacement for the old `center == [0.0, 0.0]` heuristic. It
  treats only an *absent* center as unset; an explicitly-set [0.0, 0.0] counts
  as a real value and must not be clobbered by auto-view injection.
  """
  view = mapspec.get("view") or {}
  # Only an explicitly-present, non-None center counts as set. A key carrying
  # None is treated as unset (defensive against partial writes).
  center = view.get("center", None)
  return "center" in view and center is not None
