"""Template / code-generation evaluator for cartography feedback (hermetic).

Scores MapSpec / composition-template / component fitness independently of
pixels. Fail-closed: missing MapSpec, schema errors, unknown templates, and
explicit compile failure never become pass.

Axes covered here (one feedback dimension):
- schema validity (authoritative ``parse_mapspec``)
- compile readiness (``is_compiled`` when known; never invent True)
- composition template fitness (``validate_component_composition``)
- component reuse (``templateId`` / variant against ComponentTemplateRegistry)

Pure functions + in-process registries only — no network, no VLM, no browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_MAX_FINDINGS = 8
_MAX_DETAIL = 200

# Sub-check ids (stable for tests + inject projection).
CHECK_SCHEMA = "schema_validity"
CHECK_COMPILE = "compile_readiness"
CHECK_COMPOSITION = "composition_fitness"
CHECK_REUSE = "component_reuse"

_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_NE = "not_evaluated"


def _clip(value: Any, limit: int = _MAX_DETAIL) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class TemplateCodegenFinding:
    check: str
    status: str
    message: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "check": self.check,
            "status": self.status,
            "message": _clip(self.message),
        }
        if self.detail:
            out["detail"] = _clip(self.detail)
        return out


@dataclass
class TemplateCodegenReport:
    """Honest template/codegen axis report."""

    status: str = _STATUS_NE
    score: Optional[float] = None
    evaluated: bool = False
    reason: str = "missing_mapspec"
    findings: List[TemplateCodegenFinding] = field(default_factory=list)
    composition_template_id: str = ""
    checks: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        findings = [f.to_dict() for f in self.findings[:_MAX_FINDINGS]]
        return {
            "status": self.status,
            "score": self.score,
            "evaluated": self.evaluated,
            "reason": self.reason,
            "composition_template_id": self.composition_template_id or None,
            "checks": dict(self.checks),
            "findings": findings,
            "findings_omitted": max(0, len(self.findings) - len(findings)),
            "source": "template_codegen_evaluator",
        }


def _extract_composition_template_id(
    mapspec: Dict[str, Any],
    explicit: str = "",
) -> str:
    if explicit:
        return str(explicit)
    for key in ("composition_template_id", "compositionTemplateId"):
        value = mapspec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta = mapspec.get("meta") if isinstance(mapspec.get("meta"), dict) else {}
    for key in ("composition_template_id", "compositionTemplateId"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    for key in ("composition_template_id", "compositionTemplateId"):
        value = layout.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _check_schema(mapspec: Dict[str, Any]) -> Tuple[str, List[TemplateCodegenFinding]]:
    from app.lib.cartography.mapspec_schema import (
        MapSpecSchemaError,
        parse_mapspec,
    )

    findings: List[TemplateCodegenFinding] = []
    try:
        result = parse_mapspec(mapspec)
    except MapSpecSchemaError as exc:
        findings.append(TemplateCodegenFinding(
            check=CHECK_SCHEMA,
            status=_STATUS_FAIL,
            message="MapSpec payload rejected by authoritative schema.",
            detail=getattr(exc, "code", "") or str(exc),
        ))
        return _STATUS_FAIL, findings
    except Exception as exc:  # noqa: BLE001 — evaluator must not crash the harness
        findings.append(TemplateCodegenFinding(
            check=CHECK_SCHEMA,
            status=_STATUS_NE,
            message="MapSpec schema parse could not run.",
            detail=type(exc).__name__,
        ))
        return _STATUS_NE, findings

    if not result.valid:
        invalid = result.invalid_fields[:5]
        detail = "; ".join(
            f"{d.path}:{d.kind}" for d in invalid
        ) or "invalid_fields"
        findings.append(TemplateCodegenFinding(
            check=CHECK_SCHEMA,
            status=_STATUS_FAIL,
            message="MapSpec schema validation failed.",
            detail=detail,
        ))
        return _STATUS_FAIL, findings

    findings.append(TemplateCodegenFinding(
        check=CHECK_SCHEMA,
        status=_STATUS_PASS,
        message="MapSpec spine validates against authoritative schema.",
    ))
    return _STATUS_PASS, findings


def _check_compile(
    is_compiled: Optional[bool],
) -> Tuple[str, List[TemplateCodegenFinding]]:
    findings: List[TemplateCodegenFinding] = []
    if is_compiled is True:
        findings.append(TemplateCodegenFinding(
            check=CHECK_COMPILE,
            status=_STATUS_PASS,
            message="Lifecycle reported is_compiled=True.",
        ))
        return _STATUS_PASS, findings
    if is_compiled is False:
        findings.append(TemplateCodegenFinding(
            check=CHECK_COMPILE,
            status=_STATUS_FAIL,
            message="Lifecycle reported is_compiled=False (compile not ready).",
        ))
        return _STATUS_FAIL, findings
    findings.append(TemplateCodegenFinding(
        check=CHECK_COMPILE,
        status=_STATUS_NE,
        message="No is_compiled evidence; compile readiness not evaluated.",
    ))
    return _STATUS_NE, findings


def _components_from_mapspec(mapspec: Dict[str, Any]) -> Tuple[List[Any], List[TemplateCodegenFinding]]:
    """Best-effort CartographyComponent list; malformed entries are findings."""
    from app.services.gis_harness.components import CartographyComponent

    findings: List[TemplateCodegenFinding] = []
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    raw_list = layout.get("components")
    if raw_list is None:
        return [], findings
    if not isinstance(raw_list, list):
        findings.append(TemplateCodegenFinding(
            check=CHECK_COMPOSITION,
            status=_STATUS_FAIL,
            message="layout.components must be a list.",
        ))
        return [], findings

    components: List[Any] = []
    for idx, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            findings.append(TemplateCodegenFinding(
                check=CHECK_COMPOSITION,
                status=_STATUS_FAIL,
                message=f"layout.components[{idx}] is not an object.",
            ))
            continue
        try:
            components.append(CartographyComponent.model_validate(raw))
        except Exception as exc:  # noqa: BLE001
            findings.append(TemplateCodegenFinding(
                check=CHECK_COMPOSITION,
                status=_STATUS_FAIL,
                message=f"layout.components[{idx}] failed component schema.",
                detail=type(exc).__name__,
            ))
    return components, findings


def _check_composition(
    mapspec: Dict[str, Any],
    composition_template_id: str,
) -> Tuple[str, List[TemplateCodegenFinding]]:
    from app.lib.cartography.composition_validation import validate_component_composition

    findings: List[TemplateCodegenFinding] = []
    components, parse_findings = _components_from_mapspec(mapspec)
    findings.extend(parse_findings)
    if any(f.status == _STATUS_FAIL for f in parse_findings):
        return _STATUS_FAIL, findings

    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    has_components_key = "components" in layout
    if not composition_template_id and not has_components_key:
        findings.append(TemplateCodegenFinding(
            check=CHECK_COMPOSITION,
            status=_STATUS_NE,
            message="No composition template id and no layout.components to validate.",
        ))
        return _STATUS_NE, findings

    layers = mapspec.get("layers") if isinstance(mapspec.get("layers"), list) else []
    layer_ids = [
        str(layer.get("id"))
        for layer in layers
        if isinstance(layer, dict) and layer.get("id")
    ]
    map_model_id = ""
    meta = mapspec.get("meta") if isinstance(mapspec.get("meta"), dict) else {}
    if isinstance(meta.get("map_model_id"), str):
        map_model_id = meta["map_model_id"]

    try:
        result = validate_component_composition(
            components,
            composition_template_id=composition_template_id,
            map_model_id=map_model_id,
            layer_ids=layer_ids,
        )
    except Exception as exc:  # noqa: BLE001
        findings.append(TemplateCodegenFinding(
            check=CHECK_COMPOSITION,
            status=_STATUS_NE,
            message="Composition validation could not run.",
            detail=type(exc).__name__,
        ))
        return _STATUS_NE, findings

    errors = list(result.errors)
    if errors:
        for violation in errors[:_MAX_FINDINGS]:
            findings.append(TemplateCodegenFinding(
                check=CHECK_COMPOSITION,
                status=_STATUS_FAIL,
                message=_clip(violation.detail or violation.code),
                detail=violation.code,
            ))
        return _STATUS_FAIL, findings

    findings.append(TemplateCodegenFinding(
        check=CHECK_COMPOSITION,
        status=_STATUS_PASS,
        message=(
            "Composition fitness ok"
            + (f" for {composition_template_id}" if composition_template_id else "")
            + "."
        ),
    ))
    return _STATUS_PASS, findings


def _check_component_reuse(
    mapspec: Dict[str, Any],
) -> Tuple[str, List[TemplateCodegenFinding]]:
    from app.lib.cartography.component_templates import get_component_template_registry

    findings: List[TemplateCodegenFinding] = []
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    raw_list = layout.get("components")
    if not isinstance(raw_list, list) or not raw_list:
        findings.append(TemplateCodegenFinding(
            check=CHECK_REUSE,
            status=_STATUS_NE,
            message="No components present; component reuse not evaluated.",
        ))
        return _STATUS_NE, findings

    try:
        registry = get_component_template_registry()
    except Exception as exc:  # noqa: BLE001
        findings.append(TemplateCodegenFinding(
            check=CHECK_REUSE,
            status=_STATUS_NE,
            message="Component template registry unavailable.",
            detail=type(exc).__name__,
        ))
        return _STATUS_NE, findings

    known_ids = set(registry.all_ids)
    reused = 0
    unknown = 0
    bare = 0
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        template_id = str(raw.get("templateId") or raw.get("template_id") or "").strip()
        if not template_id:
            bare += 1
            continue
        if template_id in known_ids:
            reused += 1
        else:
            unknown += 1
            findings.append(TemplateCodegenFinding(
                check=CHECK_REUSE,
                status=_STATUS_FAIL,
                message=f"Unknown component templateId '{template_id}'.",
                detail=template_id,
            ))

    if unknown:
        return _STATUS_FAIL, findings

    total = reused + bare
    if total == 0:
        findings.append(TemplateCodegenFinding(
            check=CHECK_REUSE,
            status=_STATUS_NE,
            message="No evaluable component entries for reuse scoring.",
        ))
        return _STATUS_NE, findings

    # Honest: bare components (no templateId) are not failures, but they are
    # not full reuse either — if *all* are bare, report not_evaluated rather
    # than fake pass. Partial reuse with no unknowns → pass with detail.
    if reused == 0:
        findings.append(TemplateCodegenFinding(
            check=CHECK_REUSE,
            status=_STATUS_NE,
            message=(
                f"{bare} component(s) lack templateId; reuse not evidenced."
            ),
        ))
        return _STATUS_NE, findings

    findings.append(TemplateCodegenFinding(
        check=CHECK_REUSE,
        status=_STATUS_PASS,
        message=f"{reused}/{total} component(s) reference registered templates.",
    ))
    return _STATUS_PASS, findings


def _aggregate(
    check_statuses: Dict[str, str],
    findings: List[TemplateCodegenFinding],
) -> Tuple[str, Optional[float], bool, str]:
    """Aggregate sub-checks: any fail → fail; else any NE → NE; else pass.

    Score is only set when evaluated (pass/fail). not_evaluated ⇒ score=None.
    """
    statuses = list(check_statuses.values())
    if _STATUS_FAIL in statuses:
        evaluated_n = sum(1 for s in statuses if s != _STATUS_NE)
        fail_n = sum(1 for s in statuses if s == _STATUS_FAIL)
        score = 0.0 if evaluated_n == 0 else max(0.0, 1.0 - (fail_n / evaluated_n))
        return _STATUS_FAIL, round(score, 3), True, "template_codegen_failed"
    if all(s == _STATUS_NE for s in statuses):
        return _STATUS_NE, None, False, "template_codegen_not_evaluated"
    if _STATUS_NE in statuses:
        # Mix of pass + not_evaluated, no fail — incomplete evidence.
        return _STATUS_NE, None, False, "template_codegen_evidence_incomplete"
    return _STATUS_PASS, 1.0, True, "template_codegen_passed"


def evaluate_template_codegen(
    mapspec: Optional[Dict[str, Any]],
    *,
    composition_template_id: str = "",
    is_compiled: Optional[bool] = None,
) -> TemplateCodegenReport:
    """Evaluate template/codegen fitness for one MapSpec generation.

    ``is_compiled`` must come from lifecycle evidence when available. Passing
    True without evidence is forbidden — callers that lack the flag must leave
    it ``None`` (honest not_evaluated on the compile sub-check).
    """
    if not isinstance(mapspec, dict):
        return TemplateCodegenReport(
            status=_STATUS_NE,
            score=None,
            evaluated=False,
            reason="missing_mapspec",
            findings=[TemplateCodegenFinding(
                check=CHECK_SCHEMA,
                status=_STATUS_NE,
                message="No MapSpec dict provided.",
            )],
            checks={
                CHECK_SCHEMA: _STATUS_NE,
                CHECK_COMPILE: _STATUS_NE,
                CHECK_COMPOSITION: _STATUS_NE,
                CHECK_REUSE: _STATUS_NE,
            },
        )

    cid = _extract_composition_template_id(mapspec, composition_template_id)
    all_findings: List[TemplateCodegenFinding] = []
    checks: Dict[str, str] = {}

    schema_status, schema_findings = _check_schema(mapspec)
    checks[CHECK_SCHEMA] = schema_status
    all_findings.extend(schema_findings)

    compile_status, compile_findings = _check_compile(is_compiled)
    checks[CHECK_COMPILE] = compile_status
    all_findings.extend(compile_findings)

    # Schema fail still runs composition/reuse so the agent sees all axes, but
    # overall remains fail.
    composition_status, composition_findings = _check_composition(mapspec, cid)
    checks[CHECK_COMPOSITION] = composition_status
    all_findings.extend(composition_findings)

    reuse_status, reuse_findings = _check_component_reuse(mapspec)
    checks[CHECK_REUSE] = reuse_status
    all_findings.extend(reuse_findings)

    status, score, evaluated, reason = _aggregate(checks, all_findings)
    return TemplateCodegenReport(
        status=status,
        score=score,
        evaluated=evaluated,
        reason=reason,
        findings=all_findings,
        composition_template_id=cid,
        checks=checks,
    )


__all__ = [
    "CHECK_COMPILE",
    "CHECK_COMPOSITION",
    "CHECK_REUSE",
    "CHECK_SCHEMA",
    "TemplateCodegenFinding",
    "TemplateCodegenReport",
    "evaluate_template_codegen",
]
