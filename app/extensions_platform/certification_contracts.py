"""Stable evidence row emitted by every extension certification stage."""
from typing import Literal
from typing_extensions import TypedDict


class CapabilityCheckRecord(TypedDict):
    stage: str
    capability: str
    status: Literal["pass", "fail", "warn"]
    detail: str
