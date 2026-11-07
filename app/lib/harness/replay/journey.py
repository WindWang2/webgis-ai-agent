"""Read-only end-to-end evidence projection for recorded Harness journeys."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, cast

from app.lib.harness.result_contracts import (
    EndToEndJourneyEvidence,
    JourneyCompletionClaims,
    JourneyFinding,
    JourneyMode,
    JourneyStage,
    JourneyStageEvidence,
    JourneyStatus,
)
from app.lib.harness.replay.determinism import sha256_of

SCHEMA_VERSION = "gis_journey_evidence.v1"
_MODES = frozenset({
    "offline_replay", "live_dispatch_stub_llm", "browser_renderer",
    "live_services_opt_in",
})
_STAGE_ORDER: tuple[JourneyStage, ...] = (
    "intent", "plan", "tool_dispatch", "data_result", "mutation",
    "render_observation", "export", "settlement",
)
_SOURCE_STAGES = {
    "intent": ("USER_INTENT", "PARSED_INTENT"),
    "plan": ("CANDIDATE_WORKFLOWS", "SELECTED_WORKFLOW"),
    "tool_dispatch": ("TOOL_CALLS", "ARGUMENTS"),
    "data_result": ("TOOL_RESULTS",),
    "mutation": ("MAP_MUTATIONS",),
    "render_observation": ("MAP_OBSERVATION",),
    "export": (),
    "settlement": ("FINAL_VERDICT", "USER_OUTPUT"),
}
_SUCCESS = frozenset({"ok", "success", "succeeded", "complete", "completed",
                      "pass", "passed", "verified", "ready"})
_SOURCE_ORDER = (
    "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY", "DATA_PROFILE",
    "CANDIDATE_WORKFLOWS", "SELECTED_WORKFLOW", "TOOL_SURFACE",
    "MODEL_ROUTING", "TOOL_CALLS", "ARGUMENTS", "TOOL_RESULTS",
    "ARTIFACT_CREATION", "MAP_MUTATIONS", "MAP_OBSERVATION", "VERIFICATION",
    "REPAIR", "FINAL_VERDICT", "USER_OUTPUT",
)
_SOURCE_RANK = {name: index for index, name in enumerate(_SOURCE_ORDER)}


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    chain = _mapping(trace.get("chain"))
    return [r for r in (chain.get("stages") or []) if isinstance(r, dict)]


def _for(records: Iterable[Dict[str, Any]], stage: JourneyStage) -> List[Dict[str, Any]]:
    names = set(_SOURCE_STAGES[stage])
    return [r for r in records if str(r.get("stage") or "") in names]


def _fingerprint(records: List[Dict[str, Any]]) -> str:
    return sha256_of(records) if records else ""


def _status_records(
    stage: JourneyStage,
    records: List[Dict[str, Any]],
    status: JourneyStatus,
    reason: str,
) -> JourneyStageEvidence:
    return {
        "stage": stage,
        "ordinal": _STAGE_ORDER.index(stage) + 1,
        "status": status,
        "observed_ordinal": None,
        "evidence_refs": [f"stage:{r.get('stage')}" for r in records[:8]],
        "source_fingerprint": _fingerprint(records),
        "reason_code": reason,
    }


def _identity(trace: Dict[str, Any]) -> Dict[str, str]:
    chain = _mapping(trace.get("chain"))
    correlation = _mapping(trace.get("correlation"))
    return {
        "session_id": str(trace.get("session_id") or chain.get("session_id") or "")[:255],
        "turn_id": str(trace.get("turn_id") or chain.get("turn_id") or "")[:128],
        "goal_id": str(trace.get("goal_id") or correlation.get("goal_id") or "")[:128],
        "operation_id": str(trace.get("operation_id") or correlation.get("operation_id") or "")[:128],
        "correlation_id": str(trace.get("correlation_id") or correlation.get("correlation_id") or "")[:128],
        "request_id": str(trace.get("request_id") or "")[:128],
        "run_id": str(trace.get("run_id") or "")[:128],
    }


def _identity_conflicts(
    trace: Dict[str, Any],
    records: List[Dict[str, Any]],
    identity: Dict[str, str],
) -> List[JourneyFinding]:
    chain = _mapping(trace.get("chain"))
    conflicts: List[JourneyFinding] = []
    for key in ("session_id", "turn_id"):
        expected = identity.get(key) or ""
        recorded = str(chain.get(key) or "")
        if expected and recorded and recorded != expected:
            conflicts.append({
                "code": f"{key.removesuffix('_id')}_mismatch",
                "stage": "settlement",
                "detail": f"chain {key} does not match the turn envelope",
            })
    for record in records:
        for key in ("session_id", "turn_id", "goal_id", "operation_id", "correlation_id"):
            expected = identity.get(key) or ""
            recorded = str(record.get(key) or "")
            if expected and recorded and recorded != expected:
                conflicts.append({
                    "code": f"{key.removesuffix('_id')}_mismatch",
                    "stage": "settlement",
                    "detail": f"stage {key} does not match the turn envelope",
                })
    return conflicts[:64]


def project_journey_evidence(
    trace: Dict[str, Any],
    *,
    mode: JourneyMode = "offline_replay",
) -> EndToEndJourneyEvidence:
    """Project recorded facts without upgrading absent observations to success."""
    if mode not in _MODES:
        raise ValueError(f"unknown journey evidence mode {mode!r}")
    if not isinstance(trace, dict):
        raise TypeError("trace must be a dictionary")
    records = _records(trace)
    identity = _identity(trace)
    by_stage = {stage: _for(records, stage) for stage in _STAGE_ORDER}
    stages: Dict[JourneyStage, JourneyStageEvidence] = {}
    identity_conflicts = _identity_conflicts(trace, records, identity)
    findings: List[JourneyFinding] = list(identity_conflicts)
    source_ranks = [
        _SOURCE_RANK[str(r.get("stage"))]
        for r in records if str(r.get("stage") or "") in _SOURCE_RANK
    ]
    sequence_conflicts = any(
        current < previous for previous, current in zip(source_ranks, source_ranks[1:]))
    if sequence_conflicts:
        findings.append({
            "code": "noncanonical_stage_order",
            "stage": "settlement",
            "detail": "recorded stages are not in canonical evidence order",
        })

    stages["intent"] = _status_records(
        "intent", by_stage["intent"],
        "pass" if by_stage["intent"] or trace.get("normalized_goal") else "not_evaluated",
        "recorded" if by_stage["intent"] or trace.get("normalized_goal") else "intent_absent")
    stages["plan"] = _status_records(
        "plan", by_stage["plan"],
        "pass" if by_stage["plan"] or trace.get("selected_workflow") else "not_evaluated",
        "recorded" if by_stage["plan"] or trace.get("selected_workflow") else "plan_absent")

    command_accepted = bool(by_stage["tool_dispatch"] or trace.get("tool_calls"))
    dispatch_denials = [
        d for d in (trace.get("dispatch_evidence") or [])
        if isinstance(d, dict)
        and (d.get("allowed") is False or str(d.get("action") or "") == "refused")
    ]
    dispatch_ok = command_accepted and not dispatch_denials
    for denial in dispatch_denials:
        raw_reason = str(
            denial.get("reason") or denial.get("code") or "tool_dispatch_denied"
        ).lower()
        reason_code = "".join(
            char if char.isalnum() or char == "_" else "_" for char in raw_reason
        ).strip("_")[:48] or "tool_dispatch_denied"
        findings.append({
            "code": reason_code, "stage": "tool_dispatch",
            "detail": "tool dispatch was refused by the capability/surface gate",
        })
    stages["tool_dispatch"] = _status_records(
        "tool_dispatch", by_stage["tool_dispatch"],
        "fail" if dispatch_denials else "pass" if dispatch_ok else "not_evaluated",
        "dispatch_refused" if dispatch_denials else
        "dispatched" if dispatch_ok else "tool_dispatch_absent")

    result_records = by_stage["data_result"]
    call_ids = {
        str(r.get("call_id") or r.get("tool_call_id"))
        for r in by_stage["tool_dispatch"]
        if r.get("call_id") or r.get("tool_call_id")
    }
    call_ids.update(
        str(c.get("tool_call_id") or c.get("call_id"))
        for c in (trace.get("tool_calls") or []) if isinstance(c, dict)
        and (c.get("tool_call_id") or c.get("call_id"))
    )
    dangling_results = [
        r for r in result_records
        if (r.get("call_id") or r.get("tool_call_id"))
        and str(r.get("call_id") or r.get("tool_call_id")) not in call_ids
    ]
    if dangling_results:
        findings.append({
            "code": "dangling_tool_result",
            "stage": "data_result",
            "detail": "a tool result references an unknown tool call",
        })
    successful_results = [
        r for r in result_records if r not in dangling_results
        and str(r.get("status") or "ok").lower() in _SUCCESS
        and not r.get("is_error")
        and (r.get("geojson_ref") or r.get("ref") or r.get("result"))
    ]
    seen_results: Dict[str, str] = {}
    duplicate_delivery = False
    conflicting_results: List[Dict[str, Any]] = []
    for result in result_records:
        call_id = str(result.get("call_id") or result.get("tool_call_id") or "")
        if not call_id:
            continue
        fingerprint = sha256_of({
            key: value for key, value in result.items()
            if key not in ("ts", "latency_ms", "duration_ms")
        })
        if call_id in seen_results:
            if seen_results[call_id] == fingerprint:
                duplicate_delivery = True
            else:
                conflicting_results.append(result)
        else:
            seen_results[call_id] = fingerprint
    if duplicate_delivery:
        findings.append({
            "code": "duplicate_tool_callback", "stage": "data_result",
            "detail": "identical callback evidence indicates at-least-once delivery",
        })
    if conflicting_results:
        findings.append({
            "code": "conflicting_tool_result", "stage": "data_result",
            "detail": "one tool call has conflicting terminal result payloads",
        })
    result_status: JourneyStatus = (
        "fail" if dangling_results or conflicting_results else
        "partial" if duplicate_delivery else
        "pass" if successful_results else
        "fail" if any(r.get("is_error") or str(r.get("status") or "").lower()
                      in ("error", "failed") for r in result_records) else
        "not_evaluated"
    )
    for result in result_records:
        if not result.get("is_error") and str(result.get("status") or "").lower() \
                not in ("error", "failed"):
            continue
        raw_code = str(
            result.get("error_code") or result.get("code") or "tool_result_error"
        ).lower()
        typed_code = "".join(
            char if char.isalnum() or char == "_" else "_" for char in raw_code
        ).strip("_")[:48] or "tool_result_error"
        findings.append({
            "code": typed_code,
            "stage": "data_result",
            "detail": "tool result carries a terminal error contract",
        })
    stages["data_result"] = _status_records(
        "data_result", result_records, result_status,
        "dangling_tool_result" if dangling_results else
        "validated" if successful_results else
        "tool_result_error" if result_status == "fail" else "validated_result_absent")

    artifact_records = [
        r for r in records if str(r.get("stage") or "") == "ARTIFACT_CREATION"
    ]
    artifacts = _mapping(trace.get("artifacts"))
    result_refs = {
        str(r.get("geojson_ref") or r.get("ref"))
        for r in successful_results if r.get("geojson_ref") or r.get("ref")
    }
    for call in (trace.get("tool_calls") or []):
        if not isinstance(call, dict) or call.get("is_error"):
            continue
        result_ref = call.get("result_ref")
        if isinstance(result_ref, dict) and result_ref.get("geojson_ref"):
            result_refs.add(str(result_ref["geojson_ref"]))
        elif isinstance(result_ref, str) and result_ref.startswith("ref:"):
            result_refs.add(result_ref)
    artifact_refs = {
        str(r.get("ref") or r.get("artifact_id"))
        for r in artifact_records if r.get("ref") or r.get("artifact_id")
    }
    for key in ("ref", "artifact_id"):
        if artifacts.get(key):
            artifact_refs.add(str(artifacts[key]))
    missing_artifacts = result_refs - artifact_refs
    artifact_ok = not missing_artifacts if result_refs else False
    artifact_unverified = bool(successful_results) and not artifact_ok
    if artifact_unverified:
        findings.append({
            "code": "artifact_persistence_unverified", "stage": "data_result",
            "detail": "a successful result has no persisted artifact evidence",
        })
    if artifact_unverified:
        result_status = "fail"
        stages["data_result"] = _status_records(
            "data_result", result_records, result_status,
            "artifact_persistence_unverified")
    stages["data_result"]["evidence_refs"].extend(
        f"artifact:{str(r.get('ref') or r.get('artifact_id'))[:96]}"
        for r in artifact_records[:4] if r.get("ref") or r.get("artifact_id"))

    mutation_ok = any(
        r.get("revision") is not None or r.get("mapspec_revision") is not None
        or r.get("mapspec_fingerprint") or r.get("success") is True
        for r in by_stage["mutation"]
    )
    product = (trace.get("verdict") or {}).get("map_product") or {}
    mutation_ok = mutation_ok or product.get("mutation_revision") is not None
    stages["mutation"] = _status_records(
        "mutation", by_stage["mutation"],
        "pass" if mutation_ok else "not_evaluated",
        "mapspec_applied" if mutation_ok else "mapspec_application_unverified")

    context_lost = any(
        r.get("context_lost") is True or r.get("style_loaded") is False
        or bool(r.get("reconcile_error")) or bool(r.get("runtime_errors"))
        or str(r.get("status") or r.get("aggregate") or "").lower()
        in ("error", "failed", "context_lost", "webgl_context_lost")
        for r in by_stage["render_observation"]
    )
    missing_tiles = any(
        bool(r.get("missing_tiles") or r.get("tiles_missing") or r.get("tile_errors"))
        for r in by_stage["render_observation"]
    )
    render_ok = not context_lost and not missing_tiles and any(
        r.get("map_idle") is True
        or str(r.get("status") or r.get("aggregate") or "").lower() in _SUCCESS
        for r in by_stage["render_observation"]
    )
    render_status: JourneyStatus = (
        "fail" if context_lost else
        "partial" if missing_tiles else
        "pass" if render_ok else "not_evaluated"
    )
    if context_lost:
        findings.append({
            "code": "renderer_context_lost", "stage": "render_observation",
            "detail": "renderer observation carries a fatal runtime/context error",
        })
    if missing_tiles:
        findings.append({
            "code": "tiles_missing", "stage": "render_observation",
            "detail": "map observation is incomplete because tiles are missing",
        })
    stages["render_observation"] = _status_records(
        "render_observation", by_stage["render_observation"], render_status,
        "observed" if render_ok else
        "renderer_context_lost" if context_lost else
        "tiles_missing" if missing_tiles else "render_observation_absent")

    export_records = [
        r for r in artifact_records
        if str(r.get("kind") or r.get("artifact_type") or r.get("type") or "").lower()
        in ("export", "publication", "pdf", "png", "svg")
        and (r.get("ref") or r.get("artifact_id") or r.get("digest"))
    ]
    export_product = product.get("export") or product.get("publication") or {}
    export_failed = any(
        r.get("success") is False or str(r.get("status") or "").lower()
        in ("error", "failed", "cancelled") for r in export_records
    ) or (
        isinstance(export_product, dict)
        and str(export_product.get("status") or "").lower()
        in ("error", "failed", "cancelled")
    )
    export_ok = not export_failed and (bool(export_records) or (
        isinstance(export_product, dict)
        and str(export_product.get("status") or "").lower() in _SUCCESS
        and bool(export_product.get("artifact_id") or export_product.get("digest"))))
    if export_failed:
        findings.append({
            "code": "export_failed", "stage": "export",
            "detail": "export/publication did not reach a verified terminal artifact",
        })
    stages["export"] = _status_records(
        "export", [], "fail" if export_failed else "pass" if export_ok else "not_evaluated",
        "export_failed" if export_failed else
        "export_verified" if export_ok else "export_not_verified")

    task_complete = product.get("task_complete") is True
    outcome_block = _mapping(trace.get("outcome"))
    outcome = str(outcome_block.get("outcome") or "")
    failure_class = str(outcome_block.get("failure_class") or "").lower()
    terminal_reason = ""
    if outcome == "cancelled":
        terminal_reason = "cancelled"
    elif outcome == "superseded":
        terminal_reason = "superseded"
    elif outcome == "partial":
        terminal_reason = "partial"
    elif outcome == "failed":
        terminal_reason = "timeout" if "timeout" in failure_class else "outcome_failed"
    if terminal_reason:
        findings.append({
            "code": terminal_reason,
            "stage": "settlement",
            "detail": f"terminal outcome prevents completion: {terminal_reason}",
        })
    lock_lost = "lock" in failure_class and "lost" in failure_class
    for warning in (trace.get("warnings") or []):
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "").lower()
        if "lock" in code and "lost" in code:
            lock_lost = True
    if lock_lost:
        findings.append({
            "code": "lock_lost",
            "stage": "settlement",
            "detail": "session lock ownership was lost before settlement",
        })
    settlement_status: JourneyStatus = "not_evaluated"
    if identity_conflicts or sequence_conflicts or lock_lost \
            or conflicting_results or dispatch_denials or artifact_unverified:
        settlement_status = "fail"
    elif task_complete and not render_ok:
        settlement_status = "fail"
        findings.append({
            "code": "task_complete_without_render_observation",
            "stage": "settlement",
            "detail": "task_complete was recorded without a verified map observation",
        })
    elif task_complete and render_ok and outcome in ("", "succeeded") \
            and not duplicate_delivery:
        settlement_status = "pass"
    elif duplicate_delivery and task_complete and render_ok \
            and outcome in ("", "succeeded"):
        settlement_status = "partial"
    elif outcome in ("failed", "cancelled", "superseded", "partial"):
        settlement_status = "blocked" if outcome == "cancelled" else "fail"
    stages["settlement"] = _status_records(
        "settlement", by_stage["settlement"], settlement_status,
        "settled" if settlement_status == "pass" else
        "completion_contradiction" if settlement_status == "fail" else "settlement_unverified")

    positions = {id(record): index for index, record in enumerate(records, 1)}
    for name, stage_records in by_stage.items():
        if stage_records:
            stages[name]["observed_ordinal"] = positions[id(stage_records[0])]

    claims: JourneyCompletionClaims = {
        "command_accepted": command_accepted,
        "tool_dispatched": dispatch_ok,
        "tool_result_validated": bool(successful_results),
        "artifact_persisted": artifact_ok,
        "mapspec_applied": mutation_ok,
        "map_rendered_and_observed": render_ok,
        "goal_satisfied": settlement_status == "pass",
        "publication_export_verified": export_ok,
    }
    goal_report = product.get("goal_satisfaction") or {}
    requirements = goal_report.get("requirements") or product.get("requirements") or []
    export_required = any(
        str(r.get("kind") or r.get("type") or "").lower() in ("export", "publication")
        for r in requirements if isinstance(r, dict)
    ) or any(
        marker in str(trace.get("user_input") or trace.get("normalized_goal") or "").lower()
        for marker in ("导出", "export", "publication", "pdf")
    )
    core_complete = all(
        stages[name]["status"] == "pass"
        for name in _STAGE_ORDER if name != "export"
    )
    overall: JourneyStatus
    if any(s["status"] == "fail" for s in stages.values()):
        overall = "fail"
    elif settlement_status == "blocked":
        overall = "blocked"
    elif core_complete and (export_ok or not export_required):
        overall = "pass"
    elif any(s["status"] == "pass" for s in stages.values()):
        overall = "partial"
    else:
        overall = "not_evaluated"
    last_valid: Optional[JourneyStage] = next(
        (name for name in reversed(_STAGE_ORDER) if stages[name]["status"] == "pass"),
        None,
    )
    env = _mapping(trace.get("env"))
    runtime = {
        key: env[key] for key in (
            "registry_digest", "python_version", "platform", "manifest",
            "budgets_digest",
        ) if env.get(key) is not None
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "identity": cast(Any, _identity(trace)),
        "stages": [stages[name] for name in _STAGE_ORDER],
        "completion_claims": claims,
        "findings": findings[:64],
        "last_valid_stage": last_valid,
        "overall": overall,
        "sequence_basis": "canonical_stage_order_only",
        "runtime_fingerprint": runtime,
    }


def _diagnostic_json(report: Dict[str, Any]) -> str:
    return json.dumps(
        report, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )


def render_journey_diagnostic(
    trace: Dict[str, Any],
    *,
    session_id: str,
    turn_id: str,
    mode: JourneyMode = "offline_replay",
    max_bytes: int = 32 * 1024,
) -> str:
    """Render a bounded diagnostic without copying tool payloads or user text."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or not 512 <= max_bytes <= 64 * 1024:
        raise ValueError("max_bytes must be an integer in [512, 65536]")
    evidence = project_journey_evidence(trace, mode=mode)
    identity = evidence["identity"]
    if identity["session_id"] != session_id or identity["turn_id"] != turn_id:
        denied = {
            "schema_version": "gis_journey_diagnostic.v1",
            "mode": evidence["mode"],
            "overall": "blocked",
            "reason_code": "permission_denied",
            "truncated": False,
        }
        payload = _diagnostic_json(denied)
        if len(payload.encode("utf-8")) > max_bytes:
            raise ValueError("max_bytes is too small for a diagnostic report")
        return payload

    findings = evidence["findings"]
    finding_codes = {f["code"] for f in findings}
    stages = {s["stage"]: s for s in evidence["stages"]}
    integrity_failure = bool(finding_codes & {
        "session_mismatch", "turn_mismatch", "goal_mismatch",
        "operation_mismatch", "correlation_mismatch",
        "noncanonical_stage_order", "dangling_tool_result",
    })
    if integrity_failure:
        recovery = {
            "eligible": False, "action": "none", "automatic": False,
            "reason_code": "evidence_integrity_failure",
        }
    elif stages["render_observation"]["status"] != "pass":
        recovery = {
            "eligible": True, "action": "reobserve", "automatic": False,
            "reason_code": "render_observation_absent",
        }
    elif evidence["overall"] == "pass":
        recovery = {
            "eligible": False, "action": "none", "automatic": False,
            "reason_code": "no_recovery_needed",
        }
    else:
        recovery = {
            "eligible": False, "action": "none", "automatic": False,
            "reason_code": "no_verified_safe_action",
        }

    safe_stages = [
        {
            "stage": s["stage"], "ordinal": s["ordinal"],
            "observed_ordinal": s["observed_ordinal"], "status": s["status"],
            "reason_code": s["reason_code"],
            "source_fingerprint": s["source_fingerprint"],
        }
        for s in evidence["stages"]
    ]
    missing = [
        {"stage": s["stage"], "reason_code": s["reason_code"]}
        for s in evidence["stages"] if s["status"] != "pass"
    ]
    base: Dict[str, Any] = {
        "schema_version": "gis_journey_diagnostic.v1",
        "mode": evidence["mode"],
        "overall": evidence["overall"],
        "identity": identity,
        "last_valid_stage": evidence["last_valid_stage"],
        "missing_evidence": missing,
        "recovery": recovery,
        "runtime_fingerprint": evidence["runtime_fingerprint"],
        "sequence_basis": evidence["sequence_basis"],
        "completion_claims": evidence["completion_claims"],
        "stages": safe_stages,
        "findings": findings[:16],
    }
    variants: List[Dict[str, Any]] = [
        base,
        {**base, "findings": [
            {"code": f["code"], "stage": f["stage"]} for f in findings[:8]
        ]},
        {key: value for key, value in base.items()
         if key not in ("stages", "findings", "completion_claims")},
        {key: value for key, value in base.items()
         if key not in ("stages", "findings", "completion_claims", "identity")},
    ]
    for index, variant in enumerate(variants):
        candidate = {**variant, "truncated": index > 0}
        payload = _diagnostic_json(candidate)
        if len(payload.encode("utf-8")) <= max_bytes:
            return payload
    raise ValueError("max_bytes is too small for a diagnostic report")


__all__ = [
    "SCHEMA_VERSION", "project_journey_evidence", "render_journey_diagnostic",
]
