"""多候选结构化对比（WP3）：确定性 diff + 指标 delta + 对比摘要 digest。

防重复施工：
- 指标口径只有一套：复用 whatif ``spatial_diff_engine`` 的
  ``extract_layer_payloads`` + ``compute_metric_deltas``（ADR-0193 同源，
  共享 lat0 联合均值与 ``compare_branches`` 同口径）；候选对比不造
  第二套指标实现，payload 无点/面几何时指标就是空 dict —— 不造假指标；
- 处方性建议（optimization_goals 的消费方）属 whatif
  ``PrescriptiveAdvisor``，本面不重复施工；
- 结构化 diff（层集合/样式键/组件集合）是确定性的 spec 集合代数，
  摘要行有界（超界截断披露，与 projection 行界同款纪律）；
- ``compare_digest`` 是行摘要的 canonical sha256 前 16 —— 同输入两次
  调用同值（不含 wall-clock），写回每个候选作最近对比存证。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.baseline import observe_baseline_move
from app.services.scenario_harness.contracts import (
    MAX_COMPARABLE_CANDIDATES,
    MAX_FIELD_PATHS,
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    CandidateDiffRow,
    ScenarioCandidate,
    ScenarioCompareResult,
)
from app.services.scenario_harness.candidates import get_candidate
from app.services.scenario_harness.staleness import classify_candidate_impacts
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    locked_registry_update,
    utc_now_iso,
)
from app.services.simulation.whatif.spatial_diff_engine import (
    _collect_latitudes,
    compute_metric_deltas,
    extract_layer_payloads,
)


def _layers_by_id(spec: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """spec["layers"]（list of dict）→ {id: layer}（确定性、id 唯一化）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for layer in (spec or {}).get("layers") or []:
        if isinstance(layer, dict) and layer.get("id"):
            out[str(layer["id"])] = layer
    return out


def _components_of(spec: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """spec["layout"]["components"] → {id: component}（无组件 → 空表）。"""
    layout = (spec or {}).get("layout")
    components = layout.get("components") if isinstance(layout, dict) else None
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict) and component.get("id"):
                out[str(component["id"])] = component
    return out


def _structured_diff(
    parent_spec: Dict[str, Any], branch_spec: Dict[str, Any]
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """(layer_adds, layer_removes, style_fields, component_changes) —— 有界。"""
    parent_layers = _layers_by_id(parent_spec)
    branch_layers = _layers_by_id(branch_spec)
    layer_adds = sorted(set(branch_layers) - set(parent_layers))[:12]
    layer_removes = sorted(set(parent_layers) - set(branch_layers))[:12]
    style_fields: List[str] = []
    for layer_id in sorted(set(parent_layers) & set(branch_layers)):
        parent_paint = parent_layers[layer_id].get("paint")
        branch_paint = branch_layers[layer_id].get("paint")
        parent_paint = parent_paint if isinstance(parent_paint, dict) else {}
        branch_paint = branch_paint if isinstance(branch_paint, dict) else {}
        for key in sorted(set(parent_paint) | set(branch_paint)):
            if parent_paint.get(key) != branch_paint.get(key):
                style_fields.append(f"{layer_id}.paint.{key}")
    parent_comps = _components_of(parent_spec)
    branch_comps = _components_of(branch_spec)
    component_changes: List[str] = [f"added:{cid}" for cid in sorted(set(branch_comps) - set(parent_comps))]
    component_changes += [f"removed:{cid}" for cid in sorted(set(parent_comps) - set(branch_comps))]
    component_changes += [
        f"changed:{cid}"
        for cid in sorted(set(parent_comps) & set(branch_comps))
        if parent_comps[cid] != branch_comps[cid]
    ]
    return (
        layer_adds,
        layer_removes,
        style_fields[:MAX_FIELD_PATHS],
        component_changes[:12],
    )


async def compare_candidates(
    session_id: str,
    candidate_ids: List[str],
    *,
    optimization_goals: Optional[Dict[str, str]] = None,
    store: Any = None,
) -> ScenarioCompareResult:
    """多候选对比（≤MAX_COMPARABLE_CANDIDATES；主基准 = 首候选的基准）。

    候选必须同 ``baseline_id``：不同基准的行 ``comparable=False`` +
    ``baseline_mismatch``（跨基准对比无共同出发点，诚实拒绝不硬比）。
    ``optimization_goals`` 为保留入参（处方建议由 whatif Advisor 承担）。
    """
    del optimization_goals  # 签名冻结；本面不做第二套处方建议（见 docstring）
    ids = [str(c or "").strip() for c in (candidate_ids or [])]
    ids = [c for c in ids if c]
    if not ids:
        raise RegistryError("compare_empty", "至少提供一个待比较候选")
    if len(ids) > MAX_COMPARABLE_CANDIDATES:
        raise RegistryError(
            "compare_limit",
            f"单次对比上限 {MAX_COMPARABLE_CANDIDATES} 个候选（收到 {len(ids)}）",
        )
    candidates = [await get_candidate(session_id, cid, store=store) for cid in ids]
    main = candidates[0]
    parent_spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(parent_spec, dict) or not parent_spec:
        raise RegistryError(
            "baseline_missing",
            f"父会话 {session_id} 无权威 MapSpec 可作对比基线",
        )
    baseline = await get_registry_entry(
        session_id, REGISTRY_BASELINES, main.baseline_id, store=store
    )
    if baseline is None:
        raise RegistryError(
            "baseline_not_found",
            f"主基准 {main.baseline_id!r} 不存在（会话 {session_id}）",
        )

    base_payloads = extract_layer_payloads(parent_spec)
    rows: List[CandidateDiffRow] = []
    branch_payload_of: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.baseline_id != main.baseline_id:
            rows.append(
                CandidateDiffRow(
                    candidate_id=candidate.candidate_id,
                    comparable=False,
                    incomparable_reason="baseline_mismatch",
                )
            )
            continue
        branch_spec = await mapspec_store_instance.get_mapspec(candidate.branch_session_id)
        if not isinstance(branch_spec, dict) or not branch_spec:
            rows.append(
                CandidateDiffRow(
                    candidate_id=candidate.candidate_id,
                    comparable=False,
                    incomparable_reason="branch_state_missing",
                )
            )
            continue
        adds, removes, styles, comps = _structured_diff(parent_spec, branch_spec)
        rows.append(
            CandidateDiffRow(
                candidate_id=candidate.candidate_id,
                layer_adds=adds,
                layer_removes=removes,
                style_fields=styles,
                component_changes=comps,
            )
        )
        branch_payload_of[candidate.candidate_id] = extract_layer_payloads(branch_spec)

    # 指标 delta：共享 lat0 = 父 + 全部可比分支联合均值（与 compare_branches 同款）
    latitudes = _collect_latitudes(base_payloads)
    for payloads in branch_payload_of.values():
        latitudes.extend(_collect_latitudes(payloads))
    shared_lat0 = sum(latitudes) / len(latitudes) if latitudes else 0.0
    for row in rows:
        payloads = branch_payload_of.get(row.candidate_id)
        if payloads is None:
            continue
        deltas = compute_metric_deltas(base_payloads, payloads, lat0=shared_lat0)
        row.metric_deltas = {
            md.metric_key: round(md.delta_abs, 6)
            for md in deltas
            if md.delta_abs is not None
        }

    move = await observe_baseline_move(session_id, main.baseline_id, store=store)
    impacts = await classify_candidate_impacts(
        session_id, baseline_id=main.baseline_id, store=store
    )

    # digest：行摘要 canonical JSON 的 sha256[:16]（确定性；不含 wall-clock）
    digest_source = [
        {
            "candidate_id": row.candidate_id,
            "layer_adds": len(row.layer_adds),
            "style_fields": len(row.style_fields),
            "metrics": sorted(
                (key, round(value, 6)) for key, value in row.metric_deltas.items()
            ),
        }
        for row in rows
    ]
    compare_digest = hashlib.sha256(
        json.dumps(
            digest_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]

    def _stamp(entry: ScenarioCandidate) -> ScenarioCandidate:
        entry.compare_digest = compare_digest
        entry.updated_at = utc_now_iso()
        return entry

    for candidate in candidates:
        await locked_registry_update(
            session_id, REGISTRY_CANDIDATES, candidate.candidate_id, _stamp, store=store
        )
    return ScenarioCompareResult(
        session_id=session_id,
        baseline_id=main.baseline_id,
        baseline_revision=baseline.mapspec_revision,
        move=move,
        rows=rows,
        impacts=impacts,
        compare_digest=compare_digest,
    )


__all__ = ["compare_candidates"]
