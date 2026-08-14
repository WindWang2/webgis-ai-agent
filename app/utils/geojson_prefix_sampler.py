"""Bounded GeoJSON feature sampling (PERF-F9).

The upload-info path used to json.load() an entire uploaded file (50 MB →
1-3 s + a full memory spike) just to show the LLM 3 sample feature property
sets. This module extracts the first N ``properties`` objects by scanning a
bounded prefix of the file instead of materializing the whole document.

Robustness contract (review P1-1): the input is attacker-controlled upload
content, so the scan must ALWAYS terminate. The scanner anchors on `"type"`
occurrences under a hard attempt budget, resolves each candidate's object
start with a string-literal-aware backward search (a `{` inside a string
value never opens an object), and skips whole decoded objects forward —
every step strictly advances. Malformed tails simply yield fewer samples;
sampling is best-effort by contract.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

# Hard bounds: the scan can never loop unboundedly, whatever the content.
_MAX_DECODE_ATTEMPTS = 512
_MAX_BACKWARD_CHARS = 256


def _in_string_mask(text: str) -> List[bool]:
    """Single pass: mask[i] is True while text[i] is inside a string literal."""
    mask = [False] * len(text)
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            mask[i] = True
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                mask[i] = False  # the closing quote itself is not content
        elif ch == '"':
            in_string = True
            mask[i] = True
    return mask


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
    if not text:
        return None
    mask = _in_string_mask(text)
    decoder = json.JSONDecoder()
    props: List[dict] = []

    search_from = 0
    attempts = 0
    while len(props) < count and attempts < _MAX_DECODE_ATTEMPTS:
        anchor = text.find('"type"', search_from)
        if anchor == -1:
            break
        search_from = anchor + 6  # every iteration strictly advances

        # Find the object start: nearest '{' before the anchor that is not
        # inside a string literal (a '{' inside "name": "foo {bar" never
        # opens an object).
        start = -1
        lo = max(0, anchor - _MAX_BACKWARD_CHARS)
        j = anchor
        while j >= lo:
            j = text.rfind("{", lo, j)
            if j == -1:
                break
            if not mask[j]:
                start = j
                break
        if start == -1:
            continue

        attempts += 1
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "Feature" and "properties" in obj:
            props.append(obj.get("properties") or {})
            # Advance past this feature's "type" token is already done via
            # search_from; nothing else needed.
        # FeatureCollection / geometry headers: just keep scanning — nested
        # features carry their own "type" tokens further on.

    return props or None
