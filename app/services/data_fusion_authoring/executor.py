"""融合执行器 —— 源装载/资格闸、匹配运行、commit 组装（可重入纯逻辑）。

执行纪律（reviewer 重点）：
- 源解析/授权唯一走 G18 ``resolve_source``（与 G38 同一授权路径；不
  传源凭据给 LLM/前端，快照指纹冻结进幂等键）；
- 资格闸 fail-closed：CRS 不可信/不可比 → typed 阻断（提示走 G45 坐
  标操作）；实体种类与几何形态不符 → typed 阻断；非法几何按政策
  exclude_count/block，绝不静默 make_valid；
- commit 组装由计划规则 + 已授权人工决定驱动；未决项按提交政策
  （allow_unresolved / require_resolved）处理，绝不假装已决；
- 产出的 FeatureCollection 顶层带声明 CRS 与 bounded provenance。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fusion_authoring import (
    adjudication,
    contracts,
    matching,
)
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.plan import FusionPlan

logger = logging.getLogger("data_fusion.executor")


# ───────────────────────── 装载视图 ─────────────────────────

@dataclass
class FusionSourceView:
    """已装载源（行序 = 冻结快照序；重放确定性锚点）。"""

    side: str                     # "a" | "b"
    label: str
    crs: Optional[str]
    content_fingerprint: str
    content_revision: Optional[int] = None
    rows: List[Dict[str, Any]] = field(default_factory=list)
    geoms: List[Any] = field(default_factory=list)  # shapely（可含 None）
    declared_fields: List[str] = field(default_factory=list)
    excluded_invalid_geometry: int = 0
    source_ref: str = ""

    def row_count(self) -> int:
        return len(self.rows)


def _parse_geometry(geom: Optional[Dict[str, Any]]) -> Optional[Tuple[Any, str]]:
    """GeoJSON → shapely。返回 (geometry|None, reason)；合法但缺失 = (None, "missing")。"""
    if geom is None:
        return None, "missing"
    if not isinstance(geom, dict) or not geom.get("type"):
        return None, "malformed"
    try:
        import shapely.geometry as sgeom

        parsed = sgeom.shape(geom)
    except Exception:  # noqa: BLE001 — 结构坏几何如实记录
        return None, "malformed"
    if parsed.is_empty:
        return None, "empty"
    return parsed, "ok"


def _entity_kind_ok(kind: str, geom: Any) -> bool:
    gt = getattr(geom, "geom_type", "")
    if kind == "point_poi":
        return gt == "Point"
    if kind == "area_link":
        return gt in ("Polygon", "MultiPolygon")
    if kind == "line_link":
        return gt in ("LineString", "MultiLineString")
    return False


def build_source_view(
    side: str, plan: FusionPlan, snapshot: Any, source_ref: str,
) -> FusionSourceView:
    """授权快照 → FusionSourceView（几何解析 + 实体种类资格闸）。

    非法几何按政策：exclude_count（排除并计数披露）/ block（typed）。
    """
    import shapely

    crs = snapshot.crs
    if not crs and not snapshot.is_table:
        from app.services.data_delivery.source_resolver import _normalize_crs

        crs = _normalize_crs((snapshot.payload or {}).get("crs"))
    rows: List[Dict[str, Any]] = []
    geoms: List[Any] = []
    invalid = 0
    spec = plan.source_a if side == "a" else plan.source_b
    if snapshot.is_table:
        rows = list((snapshot.payload or {}).get("rows") or [])
        geoms = [None] * len(rows)
        if plan.entity_kind != "table_only" and plan.strategy.weights.get(
                "distance", 0.0) > 0.0:
            # 距离证据 >0 但表源无几何 —— 距离证据缺失由引擎诚实重归一，
            # 不在此阻断（纯属性融合是合法场景）
            pass
    else:
        for f in (snapshot.payload or {}).get("features") or []:
            rows.append((f.get("properties") or {}).copy())
            parsed, reason = _parse_geometry(f.get("geometry"))
            if parsed is not None:
                if not _entity_kind_ok(plan.entity_kind, parsed):
                    raise DataFusionError(
                        "entity_kind_unsupported",
                        f"source_{side} 第 {len(rows)} 行几何形态 "
                        f"{parsed.geom_type} 与实体种类 "
                        f"{plan.entity_kind} 不符",
                        side=side, row=len(rows),
                    )
                if not parsed.is_valid:
                    if plan.invalid_geometry_policy == "block":
                        raise DataFusionError(
                            "invalid_geometry",
                            f"source_{side} 第 {len(rows)} 行几何非法"
                            "（政策=block；请先修复源数据）",
                            side=side, row=len(rows),
                        )
                    invalid += 1
                    parsed = None
                    reason = "invalid_excluded"
            geoms.append(parsed)
    view = FusionSourceView(
        side=side,
        label=snapshot.label,
        crs=crs,
        content_fingerprint=snapshot.content_fingerprint[:64],
        content_revision=getattr(snapshot, "content_revision", None),
        rows=rows,
        geoms=geoms,
        declared_fields=[f.get("name") for f in (snapshot.fields or [])
                         if f.get("name")],
        excluded_invalid_geometry=invalid,
        source_ref=source_ref,
    )
    _validate_declared_fields(view, spec, side)
    return view


def _validate_declared_fields(
    view: FusionSourceView, spec: Any, side: str
) -> None:
    """计划声明的字段必须在源中存在（fail-closed；不静默缺列）。"""
    missing: List[str] = []
    declared = set(view.declared_fields)
    for fname in (spec.name_field, spec.external_id_field,
                  spec.address_field, spec.category_field,
                  spec.temporal_field):
        if fname and fname not in declared:
            missing.append(fname)
    for fm in spec.field_mappings:
        if fm.source_field not in declared:
            missing.append(fm.source_field)
    if missing:
        raise DataFusionError(
            "field_not_found",
            f"source_{side} 缺少计划声明的字段：{sorted(set(missing))}",
            side=side, missing=sorted(set(missing)),
            declared_sample=view.declared_fields[:40],
        )


def build_spatial_context(
    src_a: FusionSourceView, src_b: FusionSourceView, plan: FusionPlan
) -> matching.SpatialContext:
    """CRS 资格闸 → SpatialContext（两侧可比才放行）。"""
    cls_a = _classify(src_a.crs)
    cls_b = _classify(src_b.crs)
    comparable = {
        ("geographic", "geographic"),
        ("projected_local_metric", "projected_local_metric"),
    }
    if (cls_a, cls_b) not in comparable:
        raise DataFusionError(
            "crs_unresolvable",
            f"两侧源 CRS 不可比（a={src_a.crs or '未声明'}→{cls_a}，"
            f"b={src_b.crs or '未声明'}→{cls_b}）：先用 G45 坐标操作统一"
            "到可比帧（EPSG:4326 或投影米）",
            crs_a=src_a.crs, crs_b=src_b.crs, class_a=cls_a, class_b=cls_b,
        )
    kind = ("geographic" if cls_a == "geographic"
            else "projected")
    return matching.SpatialContext(kind, plan.strategy.max_match_distance_m)


def _classify(crs: Optional[str]) -> str:
    from app.lib.gis.crs_safety import classify_crs

    return classify_crs(crs)


def enforce_budgets(
    plan: FusionPlan, src_a: FusionSourceView, src_b: FusionSourceView
) -> None:
    budgets = plan.effective_budgets()
    cap = budgets["max_source_rows"]
    for view in (src_a, src_b):
        n = view.row_count()
        if n > cap:
            raise DataFusionError(
                "budget_exceeded",
                f"source_{view.side} {n} 行超过单源预算 {cap}（执行前拦截）",
                side=view.side, rows=n, cap=cap,
            )
    est = (src_a.row_count() + src_b.row_count()) * 1024  # 粗粒度行字节估计
    if est > budgets["temp_bytes_budget"]:
        raise DataFusionError(
            "budget_exceeded",
            f"临时内存估算 {est} B 超过预算 {budgets['temp_bytes_budget']} B",
            estimated_bytes=est,
        )


async def resolve_view(
    session_id: str, plan: FusionPlan, actor: Dict[str, Any], side: str,
) -> Tuple[FusionSourceView, Dict[str, Any]]:
    """计划源锚 + 调用方 → 授权快照 + FusionSourceView + 源卡证据。

    解析/授权失败 → typed source_unresolvable（包装 G18 原始码）；
    Pi 工具受信 session 回退与 G38 同款（注入 session 本身即授权）。
    """
    from app.schemas.data_delivery_schema import DeliverySourceRef
    from app.services.data_delivery.source_resolver import (
        DeliveryActor,
        SourceResolutionError,
        resolve_source,
    )

    spec = plan.source_a if side == "a" else plan.source_b
    source_dict = spec.model_dump(
        include={"kind", "session_id", "ref", "project_id", "artifact_id",
                 "revision_no", "table_ref"}, exclude_none=True)
    try:
        ref = DeliverySourceRef.model_validate(source_dict)
    except Exception as exc:
        raise DataFusionError(
            "invalid_plan", f"source_{side} 源锚无效：{exc}", side=side) from exc
    dactor = DeliveryActor(
        user_id=actor.get("user_id"),
        org_id=actor.get("org_id"),
        owner_token=actor.get("owner_token"),
    )
    try:
        snapshot = await resolve_source(ref, dactor)
    except SourceResolutionError as exc:
        code = str(getattr(exc, "error_code", "") or getattr(exc, "code", ""))
        reason = str(getattr(exc, "reason", "") or exc)
        trusted = actor.get("trusted_session_id")
        if (code.upper().endswith("UNAUTHORIZED") and trusted
                and source_dict.get("kind") in ("session_ref", "table_rows")
                and source_dict.get("session_id") == session_id == trusted):
            snapshot = await _trusted_session_snapshot(
                session_id, source_dict.get("table_ref")
                or source_dict.get("ref"))
        else:
            raise DataFusionError(
                "source_unresolvable",
                f"source_{side} 解析失败：{reason}",
                side=side, upstream_code=code, status=410,
            ) from exc
    except Exception as exc:  # noqa: BLE001 — 非预期失败如实传播
        raise DataFusionError(
            "source_unresolvable",
            f"source_{side} 异常：{type(exc).__name__}: {exc}", side=side,
        ) from exc
    view = build_source_view(side, plan, snapshot, source_dict.get("ref") or "")
    evidence = {
        "side": side,
        "kind": snapshot.kind,
        "label": snapshot.label,
        "is_table": snapshot.is_table,
        "row_count": snapshot.row_count,
        "content_fingerprint": snapshot.content_fingerprint,
        "content_revision": getattr(snapshot, "content_revision", None),
        "crs": view.crs,
        "crs_class": _classify(view.crs),
        "excluded_invalid_geometry": view.excluded_invalid_geometry,
        "fields": snapshot.fields,
    }
    return view, evidence


async def _trusted_session_snapshot(session_id: str, ref: Optional[str]) -> Any:
    """受信 session 内 session_ref/table_rows 直读（G18 快照构建器）。"""
    from app.services.data_delivery.source_resolver import (
        _as_rows_payload,
        _canonical_json_sha256,
        _profile_rows,
        _snapshot_from_payload,
    )
    from app.services.data_fusion_authoring.errors import DataFusionError
    from app.services.session_data import session_data_manager

    if not ref:
        raise DataFusionError(
            "source_unresolvable", "受信回退缺少 ref", status=410)
    payload = await session_data_manager.get(session_id, ref)
    if payload is None:
        raise DataFusionError(
            "source_unresolvable",
            f"会话数据引用已过期：{ref}（请先物化或晋升为项目产物）",
            upstream_code="source_expired", status=410,
        )
    rows_payload = _as_rows_payload(payload)
    if rows_payload is not None:
        from app.services.data_delivery.source_resolver import SourceSnapshot

        return SourceSnapshot(
            kind="table_rows", label=ref, is_table=True,
            row_count=len(rows_payload["rows"]),
            content_fingerprint=_canonical_json_sha256(rows_payload["rows"]),
            completeness={k: rows_payload[k] for k in (
                "is_complete", "truncated", "total_matching", "group_count")
                if k in rows_payload},
            fields=_profile_rows(rows_payload["rows"]), payload=rows_payload,
        )
    snap = _snapshot_from_payload(ref, payload, ref)
    snap.kind = "session_ref"
    return snap


# ───────────────────────── 匹配运行 ─────────────────────────

def run_matching(
    plan: FusionPlan, src_a: FusionSourceView, src_b: FusionSourceView,
    ctx: matching.SpatialContext,
) -> matching.MatchResult:
    """匹配（引擎纯函数调用；budget/资格已在上游闸过）。"""
    return matching.match_sources(
        src_a.rows, src_a.geoms, src_b.rows, src_b.geoms, plan, ctx)


def pair_records(
    plan: FusionPlan, src_a: FusionSourceView, src_b: FusionSourceView,
    result: matching.MatchResult,
) -> Tuple[List, List]:
    return (
        matching.build_records(src_a.rows, src_a.geoms, plan.source_a, plan),
        matching.build_records(src_b.rows, src_b.geoms, plan.source_b, plan),
    )


# ───────────────────────── commit 组装 ─────────────────────────

def assemble_commit(
    plan: FusionPlan,
    src_a: FusionSourceView,
    src_b: FusionSourceView,
    result: matching.MatchResult,
    decisions_by_key: Dict[str, Dict[str, Any]],
    run_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """匹配结果 + 决定 → (FeatureCollection, quality_report, receipt)。

    组装语义（诚实边界）：
    - accepted / matched → merged（unresolved 字段输出 null 并计数披露）；
    - rejected → 两侧各自单源要素（决定记录进 provenance）；
    - needs_review/ambiguous 未决 → withheld_review 两侧单源要素；
    - unmatched → 单源要素（真实实体保留）；
    - 顺序稳定：pairs 按 (idx_a, idx_b)，随后 A/B 单源按快照序。
    """
    recs_a, recs_b = pair_records(plan, src_a, src_b, result)
    features: List[Dict[str, Any]] = []
    counts = {
        "merged": 0, "withheld_review": 0, "rejected": 0,
        "single_a": 0, "single_b": 0,
    }
    unresolved_field_count = 0
    unresolved_fields_distinct: Dict[str, int] = {}
    decided_pairs = 0

    consumed_a: set = set()
    consumed_b: set = set()
    for pair in result.pairs:
        rec_a = recs_a[pair.idx_a]
        rec_b = recs_b[pair.idx_b]
        key = adjudication.pair_stable_key(
            rec_a, rec_b, plan, src_a.source_ref, src_b.source_ref)
        decision = decisions_by_key.get(key)
        if decision:
            decided_pairs += 1
        if decision is None and pair.status in ("needs_review", "ambiguous"):
            # 身份未决 —— 绝不自动合并（交给 withheld 循环两侧输出）
            continue
        if decision and decision.get("action") == "reject":
            counts["rejected"] += 2
            fa = adjudication.single_feature(
                "a", rec_a, plan, run_id, src_a.source_ref)
            fb = adjudication.single_feature(
                "b", rec_b, plan, run_id, src_b.source_ref)
            for f in (fa, fb):
                f["properties"][contracts.PROVENANCE_KEY]["decision"] = {
                    "action": "reject", "note": decision.get("note"),
                    "reviewer": decision.get("reviewer"),
                }
            features.extend([fa, fb])
            consumed_a.add(pair.idx_a)
            consumed_b.add(pair.idx_b)
            continue
        manual = {}
        geometry_side = None
        if decision and decision.get("action") == "accept":
            manual = decision.get("field_values") or {}
            geometry_side = decision.get("geometry_side")
        feat, adjs = adjudication.merged_feature(
            rec_a, rec_b, pair, plan, run_id, src_a.source_ref,
            src_b.source_ref,
            decisions={"field_values": manual, "geometry_side": geometry_side})
        unresolved = adjudication.unresolved_fields(adjs)
        unresolved_field_count += len(unresolved)
        for u in unresolved:
            unresolved_fields_distinct[u] = unresolved_fields_distinct.get(u, 0) + 1
        features.append(feat)
        counts["merged"] += 1
        consumed_a.add(pair.idx_a)
        consumed_b.add(pair.idx_b)

    # 未决审阅对 → 两侧 withheld 单源（pair_key 互挂，审阅面板可对照）
    for pair in result.pairs:
        if pair.idx_a in consumed_a:
            continue
        rec_a = recs_a[pair.idx_a]
        rec_b = recs_b[pair.idx_b]
        key = adjudication.pair_stable_key(
            rec_a, rec_b, plan, src_a.source_ref, src_b.source_ref)
        fa = adjudication.single_feature(
            "a", rec_a, plan, run_id, src_a.source_ref, withheld=True,
            partner_key=key)
        fb = adjudication.single_feature(
            "b", rec_b, plan, run_id, src_b.source_ref, withheld=True,
            partner_key=key)
        features.extend([fa, fb])
        counts["withheld_review"] += 2
        consumed_a.add(pair.idx_a)
        consumed_b.add(pair.idx_b)

    for rec in recs_a:
        if rec.index not in consumed_a:
            features.append(adjudication.single_feature(
                "a", rec, plan, run_id, src_a.source_ref))
            counts["single_a"] += 1
    for rec in recs_b:
        if rec.index not in consumed_b:
            features.append(adjudication.single_feature(
                "b", rec, plan, run_id, src_b.source_ref))
            counts["single_b"] += 1

    budgets = plan.effective_budgets()
    total = len(features)
    if total > budgets["max_output_features"]:
        raise DataFusionError(
            "budget_exceeded",
            f"输出要素 {total} 超过预算 {budgets['max_output_features']}",
            total=total, cap=budgets["max_output_features"],
        )

    fc = {
        "type": "FeatureCollection",
        "crs": src_a.crs,
        "features": features,
        "fusion_meta": {
            "run_id": run_id,
            "entity_kind": plan.entity_kind,
            "method_version": contracts.METHOD_VERSION,
            "counts": counts,
        },
    }
    quality = build_quality_report(
        plan, src_a, src_b, result, counts,
        unresolved_field_count, unresolved_fields_distinct, total,
        decided_pairs, decisions_by_key)
    receipt = {
        "run_id": run_id,
        "counts": counts,
        "total_features": total,
        "unresolved_field_count": unresolved_field_count,
        "decided_pairs": decided_pairs,
        "fc_sha256": None,  # 由 outputs.atomic_publish 后补（内容哈希）
    }
    return fc, quality, receipt


def build_quality_report(
    plan: FusionPlan,
    src_a: FusionSourceView,
    src_b: FusionSourceView,
    result: matching.MatchResult,
    counts: Dict[str, int],
    unresolved_field_count: int,
    unresolved_fields_distinct: Dict[str, int],
    total_features: int,
    decided_pairs: int,
    decisions_by_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """面向目标的质量摘要（全部真实计数；无单一神秘评分）。

    每一项都给出「适用/需审阅/不适用」的可解释依据；样本与总体区分。
    """
    st = result.stats
    a_rows, b_rows = st["rows_a"], st["rows_b"]
    matched = counts["merged"]
    review_items = (st["pairs_needs_review"] + st["pairs_ambiguous"])
    return {
        "entity_kind": plan.entity_kind,
        "purpose": plan.purpose,
        "method_version": contracts.METHOD_VERSION,
        "coverage": {
            "source_a": {
                "label": plan.source_a.label, "rows": a_rows,
                "invalid_geometry_excluded": src_a.excluded_invalid_geometry,
            },
            "source_b": {
                "label": plan.source_b.label, "rows": b_rows,
                "invalid_geometry_excluded": src_b.excluded_invalid_geometry,
            },
            "fused_features": total_features,
            "merged": matched,
            "uniqueness_note": (
                "merged 要素已消重；withheld_review 两侧暂按单源输出"
                if counts["withheld_review"] else "merged 要素已消重"),
        },
        "matching": {
            "candidates": st["candidates"],
            "matched_auto": st["pairs_matched"],
            "needs_review": st["pairs_needs_review"],
            "ambiguous": st["pairs_ambiguous"],
            "hard_blocked": st["hard_blocked"],
            "candidate_overflow_entities": (
                st["candidate_overflow_entities_a"]
                + st["candidate_overflow_entities_b"]),
            "decision_basis": (
                "阈值/权重/边际见计划 strategy；逐对证据在 provenance.match"),
        },
        "review": {
            "queue_items": review_items,
            "decided_pairs": decided_pairs,
            "withheld_features": counts["withheld_review"],
            "outstanding": max(0, review_items + 0 - decided_pairs),
        },
        "fields": {
            "unresolved_count": unresolved_field_count,
            "unresolved_distinct": dict(sorted(
                unresolved_fields_distinct.items())),
            "missing_semantics": (
                "缺失按 missing 记录（provenance.values），绝不折算零值"),
        },
        "trust": {
            "priority_a": plan.source_a.priority,
            "reason_a": plan.source_a.priority_reason,
            "priority_b": plan.source_b.priority,
            "reason_b": plan.source_b.priority_reason,
            "licenses": {
                "a": plan.source_a.license, "b": plan.source_b.license},
        },
        "limitations": _quality_limitations(plan, result, counts),
    }


def _quality_limitations(
    plan: FusionPlan, result: matching.MatchResult, counts: Dict[str, int]
) -> List[Dict[str, str]]:
    """可解释的「适用/需审阅/不适用」依据（不做单一评分）。"""
    out: List[Dict[str, str]] = []
    st = result.stats
    if st["candidate_overflow_entities_a"] + st["candidate_overflow_entities_b"]:
        out.append({
            "status": "需审阅",
            "item": "candidate_overflow",
            "reason": "高密集区单实体候选超帽：相关配对已强制进审阅，"
                      "自动合并被歧义保护拦截",
        })
    if st["hard_blocked"]:
        out.append({
            "status": "需审阅",
            "item": "id_conflict",
            "reason": "共享标识空间内 id 不一致的对：恒不自动合并",
        })
    if counts["withheld_review"]:
        out.append({
            "status": "需审阅",
            "item": "withheld_review",
            "reason": "未决审阅对两侧均按单源输出——融合版本中同一实体"
                      "可能出现两行，直至审阅完成",
        })
    if plan.strategy.weights.get("address", 0.0) == 0.0:
        out.append({
            "status": "不适用",
            "item": "address_evidence",
            "reason": "计划显式将地址证据权重置 0：地址不一致不影响匹配",
        })
    if st["unmatched_a"] + st["unmatched_b"]:
        out.append({
            "status": "适用",
            "item": "single_source_features",
            "reason": f"未配对单源要素 {st['unmatched_a'] + st['unmatched_b']} 个"
                      "：真实实体保留，不冒充融合",
        })
    return out
