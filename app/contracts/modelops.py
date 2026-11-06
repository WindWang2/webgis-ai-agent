"""Stable ModelOps progress, published-artifact, and observation records."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from app.contracts.geojson import GeoJSONFeatureCollection


class InferenceProgress(TypedDict):
    stage: Literal["resolve", "profile", "reproject", "load", "infer"]
    run_id: str
    tiles_done: NotRequired[int]
    tiles_total: NotRequired[int]


class RasterGridIdentity(TypedDict):
    width: int
    height: int
    crs: str
    transform: list[float]
    dtype: str
    band_count: int
    block_shape: list[int]
    nodata: float | None


class PublishedArtifact(TypedDict, total=False):
    published: bool
    data_object_id: str | None
    content_sha256: str
    manifest: str
    deduped: bool
    path: str
    reason: str
    grid: RasterGridIdentity
    chunk_checksums: bool
    role: str
    reused: bool
    postgis: PostgisPublication
    selection: str
    selected_candidate: int | None
    windows: list[CandidateSetRecord]
    feature_collection: GeoJSONFeatureCollection


class DetectionRecord(TypedDict):
    box: list[float]
    label: int
    score: float


class GpuDeviceRecord(TypedDict):
    index: int
    total_mem_bytes: int


class VramSnapshot(TypedDict):
    devices: list[GpuDeviceRecord]
    used: dict[str, int]
    oom_factor: dict[str, float]


class WarmPoolRecord(TypedDict):
    model_id: str
    model_version: str
    device: str
    pinned: bool
    error: str
    cache_key: NotRequired[str]
    load_latency_s: NotRequired[float]


class WarmPoolStatus(TypedDict):
    pinned: dict[str, WarmPoolRecord]
    vram_ledger: VramSnapshot


class LineageEvent(TypedDict):
    schema_version: str
    seq: int
    ts: float
    model_id: str
    model_version: str
    event_type: str
    actor: str
    # Event payload is intentionally extensible, e.g. free-form provenance notes.
    payload: dict[str, Any]


class VersionHistory(TypedDict):
    events: list[LineageEvent]
    deployment_state: str


class ModelHistory(TypedDict):
    model_id: str
    versions: dict[str, VersionHistory]
    event_count: int


class PostgisPublication(TypedDict):
    published: bool
    reason: NotRequired[str]
    table: NotRequired[str]
    feature_count: NotRequired[int]
    crs: NotRequired[str | None]


class CandidateRecord(TypedDict):
    index: int
    score: float
    source: str
    label: int | None


class CandidateSetRecord(TypedDict):
    candidates: list[CandidateRecord]
    selection: str
    selected_index: int
    window: NotRequired[list[int]]
