"""Bounded parent capability evidence survives the Harness's durable ledger."""
from collections.abc import Mapping

from app.services.agent_swarm.delegation import MAX_CAPABILITIES_PER_REQUEST


def bounded_lineage(raw: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        key: str(value)[:48] for key, value in list(raw.items())[:6]
        if key not in {"capabilities_used", "capability_evidence"}
    }
    capabilities = raw.get("capabilities_used")
    if isinstance(capabilities, list):
        result["capabilities_used"] = [str(value)[:128] for value in capabilities[:MAX_CAPABILITIES_PER_REQUEST]]
    evidence = raw.get("capability_evidence")
    if isinstance(evidence, Mapping):
        result["capability_evidence"] = {
            key: str(evidence[key])[:120]
            for key in ("source", "status", "denials") if key in evidence
        }
    return result
