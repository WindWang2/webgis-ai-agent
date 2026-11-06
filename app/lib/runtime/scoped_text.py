"""String-compatible projection carrying scope evidence from its source record."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class ScopedText(str):
    source_scope: Mapping[str, str]

    def __new__(cls, text: str, *, source_scope: Mapping[str, str]) -> ScopedText:
        value = super().__new__(cls, text)
        value.source_scope = MappingProxyType(dict(source_scope))
        return value
