"""Collision-safe source/field identity shared by profile and classification facts."""
from __future__ import annotations

import hashlib
import json


def field_subject(source_id: str, field: str) -> str:
    readable = f"{source_id}::{field}"
    if "::" in source_id or "::" in field or len(readable) > 255:
        digest = hashlib.sha256(json.dumps([source_id, field], ensure_ascii=False).encode()).hexdigest()
        return "source_field:" + digest
    return readable
