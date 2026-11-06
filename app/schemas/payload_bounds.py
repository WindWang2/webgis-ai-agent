"""Finite, serialized request budgets shared by project authoring models."""
import json
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator


def cap_json(value: object, *, limit: int = 256 * 1024, name: str = "JSON payload") -> object:
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{name} must contain finite JSON values") from exc
    if size > limit:
        raise ValueError(f"{name} exceeds {limit} bytes")
    return value


def bounded_json(value: dict[str, object]) -> dict[str, object]:
    cap_json(value)
    return value


BoundedJson = Annotated[dict[str, object], AfterValidator(bounded_json)]


def thumbnail_url(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 2048 or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError("thumbnail_url must be an https URL or site-relative path")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//") and not parsed.scheme and not parsed.netloc:
        return value
    if parsed.scheme == "https" and parsed.netloc and parsed.username is None and parsed.password is None:
        return value
    raise ValueError("thumbnail_url must be an https URL or site-relative path")
