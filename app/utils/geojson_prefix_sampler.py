"""Bounded GeoJSON feature sampling (PERF-F9).

The upload-info path used to json.load() an entire uploaded file (50 MB →
1-3 s + a full memory spike) just to show the LLM 3 sample feature property
sets. This module extracts the first N ``properties`` objects by streaming a
bounded prefix of the file instead of materializing the whole document.

Strategy, cheapest-first:
1. Read up to ``max_bytes`` of the file.
2. Use ``json.JSONDecoder().raw_decode`` anchored at each ``{"type": "Feature"``
   occurrence to decode individual feature objects as they appear in the
   prefix.
3. Degrade gracefully: malformed/partial trailing JSON, NDJSON-ish files, or
   features that live beyond the prefix all simply yield fewer samples — the
   caller treats sampling as best-effort.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional


def sample_feature_properties(
    path: os.PathLike | str,
    count: int = 3,
    max_bytes: int = 262_144,
) -> Optional[List[dict]]:
    """Return the first ``count`` features' properties, or None if none found."""
    try:
        with open(path, "rb") as f:
            prefix = f.read(max_bytes)
    except OSError:
        return None

    text = prefix.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    props: List[dict] = []

    anchor = text.find('"type"')
    while anchor != -1 and len(props) < count:
        # Find the feature object enclosing this "type" occurrence: scan back
        # to the nearest '{' that starts an object containing it.
        start = text.rfind("{", 0, anchor)
        while start != -1:
            try:
                obj, end = decoder.raw_decode(text, start)
                if isinstance(obj, dict):
                    t = obj.get("type")
                    if t == "Feature" and "properties" in obj:
                        props.append(obj.get("properties") or {})
                        break
                    if t in ("FeatureCollection", "GeometryCollection"):
                        # The collection header decoded — its features follow;
                        # continue scanning after this object's opening.
                        start = text.find("{", start + 1)
                        if start == -1:
                            break
                        continue
                    # A geometry object — keep scanning.
                    break
            except json.JSONDecodeError:
                pass
            nxt = text.rfind("{", 0, start)
            if nxt == start:
                break
            start = nxt
        # Advance to the next "type" beyond this feature's extent.
        anchor = text.find('"type"', anchor + 6)

    return props or None
