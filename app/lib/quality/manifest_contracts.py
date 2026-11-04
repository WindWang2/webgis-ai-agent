"""Closed records emitted by the registry-derived quality manifest compiler."""
from typing_extensions import NotRequired, TypedDict


class TestReferenceRecord(TypedDict):
    tests: list[str]


class ToolQualityRecord(TestReferenceRecord):
    name: str
    status: str
    tier: int
    side_effect: str
    execution_policy: str
    capabilities: list[str]
    tags: list[str]
    latency_class: str
    memory_class: str
    scale_class: str
    deterministic: bool | None
    requires_confirmation: bool
    behavior: str
    flags: NotRequired[list[str]]


class AlgorithmQualityRecord(TestReferenceRecord):
    id: str
    category: str
    runtime_status: str
    scientific_status: str
    deterministic: bool
    random_seed_policy: str
    memory_cost: str
    capabilities: list[str]
    conformance_tests: list[str]
    backend_variants: int
    uncertainty_outputs: list[str]


class CapabilityQualityRecord(TestReferenceRecord):
    id: str
    status: str
    category: str
    domain: str
    deterministic: bool
    supports_large_data: bool
    producer_algorithms: list[str]


class ArtifactQualityRecord(TestReferenceRecord):
    id: str


class RecipeSummaryRecord(TypedDict, total=False):
    count: int
    domains: list[str]


GateFieldRecord = TypedDict("GateFieldRecord", {
    "coverage": int,
    "threshold": int,
    "pass": bool,
    "missing_count": int,
    "missing": list[str],
})

# Empty defaults are supported by QualityManifest and gate({}) fails closed.
GateReportRecord = TypedDict("GateReportRecord", {
    "total": int,
    "fields": dict[str, GateFieldRecord],
    "pass": bool,
}, total=False)


class FindingViolationRecord(TypedDict):
    code: str
    baseline: int
    current: int


class ExpiredWaiverRecord(TypedDict):
    code: str
    subject: str
    expires: str


RatchetReportRecord = TypedDict("RatchetReportRecord", {
    "pass": bool,
    "violations": list[FindingViolationRecord],
    "expired_waivers": list[ExpiredWaiverRecord],
    "waivers_active": int,
    "waived_current": int,
    "behavioral_dispatch": int,
    "min_behavioral_dispatch": int,
}, total=False)
