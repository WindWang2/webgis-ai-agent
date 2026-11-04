"""Persisted GIS trace envelopes and their segment index.

Stage payloads deliberately remain open: record_stage accepts stage-specific
keyword evidence from independent producers. The envelope and storage manifest
are closed records and are not arbitrary metadata bags.
"""
from typing import Any, TypedDict


class TraceChainRecord(TypedDict, total=False):
    schema_version: int
    turn_id: str
    session_id: str
    total_records: int | None
    completeness: float | None
    stages: list[dict[str, Any]]
    covered_stages: list[str] | None
    seq: int
    final: bool


class TraceSegment(TypedDict):
    file: str
    count: int
    min_seq: int
    max_seq: int
    protected: int


class TraceManifest(TypedDict):
    version: int
    last_seq: int
    segments: list[TraceSegment]
