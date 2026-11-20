"""G45 服务编排 —— HTTP / Pi 工具 / UI 共用的唯一域入口。

面（与任务书 WP02–WP11 一一对应）：

- 源与 CRS：``describe_source``（资格 + CRS 证据）、``lookup_crs``、
  ``list_operations``（冻结源 CRS + AOI → 有界候选 + 环境证据）；
- 计划：CRUD（revision CAS）+ ``lock_plan``（服务器签发候选 → 冻结操作定义
  与环境证据；此后任何编辑 = 新 revision，预览失效）；
- 预览：``preview_plan``（有界样本 before/after、AOI 适用性发现、独立于
  authority accuracy 的诚实披露）；
- 执行：``execute_plan``（幂等键 = owner 语义 + 源指纹 + plan digest(含操作
  digest) + 输出政策；durable job + 后台/内联 runner）；
- 运行：list/inspect/result_page/cancel/display_ref/recover。

执行体不变式（详见 execute.py/outputs.py docstring）：源复核 → 冻结 → 流式
变换（all-or-nothing + 预算闸）→ staging 核验 → 严格发布门 → 原子发布 +
DataObject + 台账 + 显示副本（独立 WGS84 链）→ 收据可回读才 completed。
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import resource
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.services.coordinate_operations import contracts, execute, outputs
from app.services.coordinate_operations.contracts import CoordinateOperationError
from app.services.coordinate_operations.crs_contract import (
    describe_crs,
    parse_crs_for_execution,
)

logger = logging.getLogger(__name__)

_RUNNERS: Dict[str, asyncio.Task] = {}


# ── 工具 ───────────────────────────────────────────────────────────────────

def _canonical_digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _bounded_json(obj: Any, limit: int, what: str) -> Dict[str, Any]:
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > limit:
        raise CoordinateOperationError(
            "plan_invalid", f"{what} 超过落库上限 {limit} 字节")
    return obj


def _get_plan_row(db: Any, session_id: str, plan_id: str) -> Any:
    from app.models.coordinate_operations import CoordinateOperationPlan
    from sqlalchemy import select

    row = db.execute(
        select(CoordinateOperationPlan).where(
            CoordinateOperationPlan.id == plan_id,
            CoordinateOperationPlan.session_id == session_id)
    ).scalar_one_or_none()
    if row is None:
        raise CoordinateOperationError("not_found", f"计划 {plan_id} 不存在")
    return row


def _plan_view(row: Any) -> Dict[str, Any]:
    return {
        "plan_id": row.id,
        "session_id": row.session_id,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "plan": row.plan,
        "normalized_digest": row.normalized_digest,
        "source_fingerprint": row.source_fingerprint,
        "selected_candidate_id": row.selected_candidate_id,
        "operation": _operation_summary(row.operation),
        "preview": row.preview,
        "preview_digest": row.preview_digest,
        "preview_stale": (
            row.preview_digest is not None
            and row.preview_digest != row.normalized_digest),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _operation_summary(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """行内冻结操作 → 有界摘要（完整 PROJJSON 不进回执/UI）。"""
    if not raw:
        return None
    try:
        op = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"corrupt": True}
    return {
        "candidate_id": op.get("candidate", {}).get("candidate_id"),
        "name": op.get("candidate", {}).get("name"),
        "accuracy": op.get("candidate", {}).get("accuracy"),
        "accuracy_unknown": op.get("candidate", {}).get("accuracy_unknown"),
        "ballpark": op.get("candidate", {}).get("ballpark"),
        "definition_sha256": op.get("definition_sha256"),
        "environment": op.get("environment"),
        "projjson_bytes": len(op.get("operation_projjson") or ""),
    }


def _parse_plan_input(plan_input: Dict[str, Any]) -> Dict[str, Any]:
    """计划输入规范化 + typed 校验（不改既有词表，不猜 CRS）。"""
    from app.schemas.coordinate_operations_schema import CoordinateOperationPlanInput

    try:
        model = CoordinateOperationPlanInput(**plan_input)
    except Exception as e:
        raise CoordinateOperationError(
            "plan_invalid", f"计划内容非法: {e}") from e
    return model.model_dump(exclude_none=True)


# ── 源与 CRS 面 ────────────────────────────────────────────────────────────

async def describe_source(
    session_id: str, source: Dict[str, Any], actor: Dict[str, Any],
) -> Dict[str, Any]:
    """源资格 + 权威 CRS 描述（WP02/WP03）。"""
    from app.services.coordinate_operations import freeze

    card = await freeze.resolve_eligible_source(session_id, source, actor)
    card.pop("_snapshot", None)
    src_desc = describe_crs(card["crs"])
    if not src_desc["dimension_supported"]:
        raise CoordinateOperationError(
            "crs_unsupported",
            f"源 CRS {src_desc['name']} 不受支持: "
            f"{src_desc['dimension_unsupported_reason']}")
    return {
        **{k: v for k, v in card.items() if k != "geometry"},
        "geometry": card["geometry"],
        "crs_description": {
            k: src_desc[k] for k in (
                "identity", "name", "type_name", "axis_official",
                "carrier_convention", "dimension", "area_of_use",
                "canonical_sha256")},
        "units": _units(src_desc),
    }


def _units(desc: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordinate_operations.crs_contract import crs_unit_summary

    return crs_unit_summary(desc)


def lookup_crs(query: str) -> Dict[str, Any]:
    """目标 CRS 查询/描述（轴/单位/维度/范围；typed 解析失败）。"""
    desc = describe_crs(query)
    return {
        "input": desc["input"],
        "identity": desc["identity"],
        "name": desc["name"],
        "type_name": desc["type_name"],
        "axis_official": desc["axis_official"],
        "carrier_convention": desc["carrier_convention"],
        "dimension_supported": desc["dimension_supported"],
        "dimension_unsupported_reason": desc["dimension_unsupported_reason"],
        "area_of_use": desc["area_of_use"],
        "units": _units(desc),
        "canonical_sha256": desc["canonical_sha256"],
    }


async def list_operations(
    session_id: str, source: Dict[str, Any], target_crs: str,
    aoi: Optional[Dict[str, Any]], actor: Dict[str, Any],
) -> Dict[str, Any]:
    """冻结源 CRS → 目标的候选操作（WP04）。"""
    from app.services.coordinate_operations import candidates, freeze

    card = await freeze.resolve_eligible_source(session_id, source, actor)
    card.pop("_snapshot", None)
    tgt_desc = describe_crs(target_crs)
    if not tgt_desc["dimension_supported"]:
        raise CoordinateOperationError(
            "crs_unsupported",
            f"目标 CRS {tgt_desc['name']} 不受支持: "
            f"{tgt_desc['dimension_unsupported_reason']}")
    listing = candidates.list_operations(card["crs"], target_crs, aoi)
    return {
        "source": {
            "kind": card["kind"], "label": card["label"],
            "row_count": card["row_count"],
            "crs": card["crs"],
            "crs_identity": describe_crs(card["crs"])["identity"],
        },
        "target": {
            "crs": target_crs,
            "identity": tgt_desc["identity"],
            "name": tgt_desc["name"],
        },
        "listing": listing,
    }


# ── 计划 CRUD ──────────────────────────────────────────────────────────────

async def create_plan(
    session_id: str, plan_input: Dict[str, Any], *, actor: Dict[str, Any],
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationPlan

    plan = _parse_plan_input(plan_input)
    card = await describe_source(session_id, plan["source"], actor)
    digest = await _normalized_digest(session_id, plan, card, actor)
    row = CoordinateOperationPlan(
        session_id=session_id,
        project_id=plan.get("project_id"),
        created_by=actor.get("user_id"),
        title=str(plan.get("title") or "")[:200],
        status="draft",
        revision=1,
        plan=_bounded_json(plan, contracts.PLAN_JSON_MAX_BYTES, "计划内容"),
        normalized_digest=digest,
        source_fingerprint=card["content_fingerprint"][:128],
    )
    with SessionLocal() as db:
        db.add(row)
        db.commit()
        return _plan_view(row)


async def _normalized_digest(
    session_id: str, plan: Dict[str, Any], source_card: Dict[str, Any],
    actor: Dict[str, Any],
) -> str:
    """规范 digest：源锚+源指纹+源/目标 CRS canonical+AOI+政策（+锁定操作）。"""
    src_desc = describe_crs(source_card["crs"])
    tgt_desc = describe_crs(plan["target_crs"])
    basis = {
        "source_spec": plan.get("source"),
        "source_fingerprint": source_card["content_fingerprint"],
        "source_crs_canonical": src_desc["canonical_sha256"],
        "target_crs_canonical": tgt_desc["canonical_sha256"],
        "aoi": plan.get("aoi"),
        "aoi_derivation": plan.get("aoi_derivation", "none"),
        "z_policy": plan.get("z_policy", "preserve_unconverted"),
        "empty_geometry_policy": plan.get("empty_geometry_policy", "keep"),
        # out_of_area_policy 只支持 report：fail 政策未实现（评审 P2-3）——
        # 契约不承诺未兑现的语义；域外发现经 preview findings + 执行非有限
        # all-or-nothing 如实披露。
        "out_of_area_policy": "report",
    }
    if plan.get("_locked_operation_digest"):
        basis["operation_digest"] = plan["_locked_operation_digest"]
    return _canonical_digest(basis)


async def update_plan(
    session_id: str, plan_id: str, plan_input: Dict[str, Any],
    *, expected_revision: int, actor: Dict[str, Any],
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    plan = _parse_plan_input(plan_input)
    card = await describe_source(session_id, plan["source"], actor)
    digest = await _normalized_digest(session_id, plan, card, actor)
    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision)
        content_changed = digest != row.normalized_digest
        row.plan = _bounded_json(plan, contracts.PLAN_JSON_MAX_BYTES, "计划内容")
        row.title = str(plan.get("title") or "")[:200]
        row.source_fingerprint = card["content_fingerprint"][:128]
        row.normalized_digest = digest
        if content_changed:
            # 任何实质编辑 → 回 draft、清锁定与预览（预览失效语义）
            row.revision = row.revision + 1
            row.status = "draft"
            row.selected_candidate_id = None
            row.operation = None
            row.preview = None
            row.preview_digest = None
        db.commit()
        return _plan_view(row)


async def get_plan(session_id: str, plan_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return _plan_view(_get_plan_row(db, session_id, plan_id))


async def list_plans(
    session_id: str, *, limit: int = 50, include_archived: bool = False,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationPlan
    from sqlalchemy import select

    with SessionLocal() as db:
        stmt = select(CoordinateOperationPlan).where(
            CoordinateOperationPlan.session_id == session_id)
        if not include_archived:
            stmt = stmt.where(CoordinateOperationPlan.status != "archived")
        rows = db.execute(
            stmt.order_by(CoordinateOperationPlan.created_at.desc())
            .limit(min(max(limit, 1), 100))).scalars().all()
        return {"plans": [_plan_view(r) for r in rows]}


async def archive_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision)
        row.status = "archived"
        row.revision += 1
        db.commit()
        return {"plan_id": row.id, "status": row.status,
                "revision": row.revision}


# ── 锁定（服务器签发候选 → 冻结操作 + 环境）──────────────────────────────

async def lock_plan(
    session_id: str, plan_id: str, candidate_id: str,
    *, expected_revision: int, actor: Dict[str, Any],
) -> Dict[str, Any]:
    """锁定候选：重算候选集 → id 匹配 → 冻结 PROJJSON + 环境证据（WP05）。

    客户端只传 candidate_id —— 任意 pipeline/格网参数在此路径外无入口。
    """
    from app.core.database import SessionLocal
    from app.services.coordinate_operations import candidates, freeze

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision)
        if row.status == "archived":
            raise CoordinateOperationError(
                "plan_invalid", "已归档计划不能锁定")
        plan = dict(row.plan or {})

    # 源复核（C25：预览/锁定后源变化 → typed 拒绝）
    card = await freeze.resolve_eligible_source(
        session_id, plan.get("source") or {}, actor)
    card.pop("_snapshot", None)
    if card["content_fingerprint"][:128] != row.source_fingerprint:
        raise CoordinateOperationError(
            "source_revision_changed",
            "源在计划创建后发生变化；请更新计划（新 revision）后重新锁定")

    resolved = candidates.resolve_candidate_for_lock(
        card["crs"], plan["target_crs"], plan.get("aoi"), candidate_id)
    frozen_op = {
        "candidate": {
            k: resolved["candidate"][k] for k in (
                "candidate_id", "name", "accuracy", "accuracy_unknown",
                "ballpark", "area_of_use", "definition_sha256")},
        "operation_projjson": resolved["operation_projjson"],
        "definition_sha256": resolved["definition_sha256"],
        "environment": resolved["environment"],
    }
    raw = json.dumps(frozen_op, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 1_000_000:
        raise CoordinateOperationError(
            "plan_invalid", "冻结操作定义超出落库上限")

    plan["_locked_operation_digest"] = resolved["definition_sha256"]
    digest = await _normalized_digest(session_id, plan, card, actor)
    plan.pop("_locked_operation_digest", None)

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict", "计划在锁定期间被修改；请重试",
                current_revision=row.revision)
        row.plan = _bounded_json(plan, contracts.PLAN_JSON_MAX_BYTES, "计划内容")
        row.normalized_digest = digest
        row.status = "locked"
        row.selected_candidate_id = candidate_id
        row.operation = raw
        row.revision = row.revision + 1
        row.preview = None
        row.preview_digest = None
        db.commit()
        return _plan_view(row)


# ── 预览 ───────────────────────────────────────────────────────────────────

def _frozen_op_from_row(row: Any) -> execute.FrozenOperation:
    if not row.operation:
        raise CoordinateOperationError(
            "plan_not_locked", "计划未锁定操作；先 lock 再预览/执行")
    try:
        op = json.loads(row.operation)
        return execute.FrozenOperation(
            op["operation_projjson"], op["definition_sha256"], op["environment"])
    except KeyError as e:
        raise CoordinateOperationError(
            "plan_invalid", f"冻结操作定义损坏: {e}") from e


def _op_area_bounds(operation_summary: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    if not operation_summary:
        return None
    aou = (operation_summary.get("candidate") or {}).get("area_of_use")
    return (aou or {}).get("bounds")


async def preview_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    actor: Dict[str, Any], sample_rows: int = 25,
) -> Dict[str, Any]:
    """有界预览（WP06）：样本 before/after、适用范围发现、非有限披露。

    样本规则（确定性，回执披露）：源顺序前 ``sample_rows`` 行。
    预览成功 ≠ 全量成功；不发布任何成果 ref。
    """
    from app.core.database import SessionLocal
    from app.services.coordinate_operations import freeze

    sample_rows = max(1, min(int(sample_rows), contracts.PREVIEW_MAX_ROWS))
    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision)
        if row.status != "locked":
            raise CoordinateOperationError(
                "plan_not_locked", "计划未锁定操作；先 lock 再预览")
        plan = dict(row.plan or {})
        plan_revision = row.revision
        plan_digest = row.normalized_digest
        card_fp_expected = row.source_fingerprint
        op = _frozen_op_from_row(row)  # 构造内含环境复核（C10）
        op_summary = _operation_summary(row.operation)

    card = await freeze.resolve_eligible_source(
        session_id, plan.get("source") or {}, actor)
    snapshot = card.pop("_snapshot", None)
    if card["content_fingerprint"][:128] != card_fp_expected:
        raise CoordinateOperationError(
            "source_revision_changed",
            "源在锁定后发生变化；请更新计划并重新锁定")
    _enforce_z_policy(plan, card)

    from app.services.data_delivery.source_resolver import sample_parquet_geometry

    if snapshot is not None and snapshot.parquet_path is not None:
        sample_parquet_geometry(snapshot, limit=max(sample_rows, 1))
    if snapshot is None:
        raise CoordinateOperationError(
            "internal_error", "源快照缺失（预览样本不可读）")
    feats = snapshot.sample_features(limit=sample_rows)

    stats = execute.BatchStats()
    pairs: List[Dict[str, Any]] = []
    findings: Dict[str, Any] = {
        "non_finite_input": 0, "non_finite_output": 0, "out_of_area_of_use": 0,
        "null_geometry": 0, "empty_geometry": 0,
        "first_non_finite_row": None, "first_out_of_area_row": None,
    }
    src_extent: List[float] = [float("inf"), float("inf"), -float("inf"), -float("inf")]
    tgt_extent: List[float] = [float("inf"), float("inf"), -float("inf"), -float("inf")]
    op_bounds = _op_area_bounds(op_summary)
    preview_rows = 0
    for i, f in enumerate(feats):
        geom = f.get("geometry")
        if not geom:
            findings["null_geometry" if geom is None else "empty_geometry"] += 1
            continue
        try:
            out = execute.transform_feature(f, op, stats, row_index=i + 1)
        except CoordinateOperationError as e:
            phase = (e.details or {}).get("phase")
            if e.error_code == "execute_failed" and phase in (
                    "non_finite_input", "non_finite_output"):
                key = ("non_finite_input" if phase == "non_finite_input"
                       else "non_finite_output")
                findings.setdefault(key, 0)
                findings[key] += 1
                findings["first_non_finite_row"] = findings[
                    "first_non_finite_row"] or (i + 1)
                continue
            raise
        preview_rows += 1
        before = _first_xy(geom)
        after = _first_xy(out.get("geometry"))
        if before:
            _extend(src_extent, before)
        if after:
            _extend(tgt_extent, after)
        if op_bounds is not None and after is not None:
            # 目标 CRS 是地理坐标时用度；投影坐标时 area_of_use（EPSG 度）不可
            # 直接比较 —— 仅当目标为地理 CRS 才做域内检查（披露语义）。
            if _is_geographic_crs(plan["target_crs"]) and not _in_bounds(
                    after, op_bounds):
                findings["out_of_area_of_use"] += 1
                findings["first_out_of_area_row"] = findings[
                    "first_out_of_area_row"] or (i + 1)
        if len(pairs) < contracts.PREVIEW_MAX_COORD_PAIRS and before and after:
            pairs.append({"row": i + 1, "before": before, "after": after})

    result = {
        "plan_id": plan_id,
        "plan_revision": plan_revision,
        "plan_digest": plan_digest,
        "sample_rule": f"源顺序前 {sample_rows} 行（确定性；不代表全量保证）",
        "sample_rows_requested": sample_rows,
        "sample_rows_transformed": preview_rows,
        "operation": op_summary,
        "coordinate_pairs": pairs,
        "source_extent": src_extent if src_extent[0] != float("inf") else None,
        "target_extent": tgt_extent if tgt_extent[0] != float("inf") else None,
        "findings": findings,
        "area_of_use_check": (
            "target-geographic-bounds" if op_bounds is not None else
            "operation 无 area_of_use 声明（跳过域内检查）"),
        "accuracy_note": (
            "authority accuracy 是 EPSG 登记的期望精度，不是实测误差；"
            "unknown 不会显示为 0"),
        "preview_is_not_full_execution": True,
    }
    evidence = _bounded_json(
        result, contracts.PREVIEW_JSON_MAX_BYTES, "预览证据")
    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision == plan_revision:
            prow.preview = evidence
            prow.preview_digest = plan_digest
            db.commit()
    return result


def _first_xy(geom: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    node = geom
    for _ in range(12):
        if not isinstance(node, dict):
            return None
        coords = node.get("coordinates")
        if coords is None:
            subs = node.get("geometries")
            if subs:
                node = subs[0]
                continue
            return None
        node = coords
        if isinstance(node, list) and node and isinstance(node[0], (int, float)):
            return [float(node[0]), float(node[1])] if len(node) >= 2 else None
        if isinstance(node, list) and node:
            node = node[0]
            continue
        return None
    return None


def _extend(extent: List[float], xy: List[float]) -> None:
    extent[0] = min(extent[0], xy[0])
    extent[1] = min(extent[1], xy[1])
    extent[2] = max(extent[2], xy[0])
    extent[3] = max(extent[3], xy[1])


def _in_bounds(xy: List[float], bounds: List[float]) -> bool:
    # bounds: EPSG area_of_use (west, south, east, north)（度，地理语义）
    return (bounds[0] <= xy[0] <= bounds[2]) and (bounds[1] <= xy[1] <= bounds[3])


def _is_geographic_crs(crs_input: str) -> bool:
    try:
        return bool(parse_crs_for_execution(crs_input).is_geographic)
    except Exception:  # noqa: BLE001
        return False


# ── 执行 ───────────────────────────────────────────────────────────────────

def _enforce_z_policy(plan: Dict[str, Any], card: Dict[str, Any]) -> None:
    """z_policy=reject（评审 P2-3 实现 + Round-2 收敛）：

    - session_ref/project_artifact：``_profile_fc`` 全扫 has_z —— 权威；
    - fabric_parquet：has_z 来自 ``sample_parquet_geometry`` **采样** ——
      采样未覆盖全量行时维度事实无法确认，reject 政策按「不可确认即拒绝」
      保守阻断（不静默降级为 preserve；评审顾虑 a）。
    """
    if plan.get("z_policy") != "reject":
        return
    geom = card.get("geometry") or {}
    has_z = bool(geom.get("has_z"))
    if not has_z and card.get("kind") == "fabric_parquet":
        sampled = int(geom.get("sampled_rows") or 0)
        if sampled < int(card.get("row_count") or 0):
            raise CoordinateOperationError(
                "unsupported_dimension",
                "计划 z_policy=reject：GeoParquet 源的 Z 维证据来自采样"
                f"（{sampled}/{card.get('row_count')} 行），无法确认全量无 Z ——"
                "保守拒绝；请改用 preserve_unconverted 或先物化为全量可判源")
    if has_z:
        raise CoordinateOperationError(
            "unsupported_dimension",
            "计划 z_policy=reject：源几何含 Z 维（horizontal-only 契约下"
            "拒绝执行；如需保留 Z 原值请改用 preserve_unconverted）")


def _build_idempotency_key(
    session_id: str, plan_id: str, plan_digest: str, source_fp: str,
) -> str:
    """幂等键（评审 P3：不截断原文 —— 长输入经 sha 摘要保完整区分度）。"""
    raw = (f"{contracts.TASK_TYPE}:{session_id}:{plan_id}:{plan_digest}:"
           f"{source_fp}")
    key = f"{contracts.TASK_TYPE}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
    return key[:contracts.IDEMPOTENCY_KEY_MAX]


async def execute_plan(
    session_id: str, plan_id: str, *, expected_revision: int,
    actor: Dict[str, Any], background: bool = True,
) -> Dict[str, Any]:
    """执行：锁定/源/环境复核 → 幂等 job + run 行 → 后台/内联执行体。"""
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from app.services.coordinate_operations import freeze

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, plan_id)
        if row.revision != int(expected_revision):
            raise CoordinateOperationError(
                "plan_revision_conflict",
                f"计划修订冲突：expected {expected_revision}，当前 {row.revision}",
                current_revision=row.revision)
        if row.status != "locked" or not row.operation:
            raise CoordinateOperationError(
                "plan_not_locked", "计划未锁定操作；先 lock 再执行")
        plan = dict(row.plan or {})
        plan_revision = row.revision
        plan_digest = row.normalized_digest
        op_json = row.operation
        expected_source_fp = row.source_fingerprint

    # 源复核 + 授权（执行与预览同一解析路径）
    card = await freeze.resolve_eligible_source(
        session_id, plan.get("source") or {}, actor)
    card.pop("_snapshot", None)
    if card["content_fingerprint"][:128] != expected_source_fp:
        raise CoordinateOperationError(
            "source_revision_changed",
            "源在计划锁定后发生变化；请更新计划并重新锁定")
    _enforce_z_policy(plan, card)

    base_key = _build_idempotency_key(
        session_id, plan_id, plan_digest, card["content_fingerprint"])

    existing = _find_completed_run_by_key(session_id, base_key)
    if existing is not None:
        try:
            outputs.verify_published(existing["run_id"])
            return {**existing, "idempotent_reuse": True}
        except CoordinateOperationError:
            _invalidate_run(session_id, existing["run_id"], "result_unavailable")

    active = _find_active_run_by_key(session_id, base_key)
    if active is not None:
        return {**_run_view(session_id, active["run_id"]),
                "idempotent_reuse": True, "job_id": active["job_id"],
                "poll": f"/api/v1/tasks/jobs/{active['job_id']}"}

    generation = _count_runs_by_key_prefix(session_id, base_key)
    idem = base_key if generation == 0 else f"{base_key}:g{generation + 1}"

    run_id = outputs.new_run_id()
    with SessionLocal() as db:
        prow = _get_plan_row(db, session_id, plan_id)
        if prow.revision != plan_revision:
            raise CoordinateOperationError(
                "plan_revision_conflict", "计划在执行提交期间被修改；请重试",
                current_revision=prow.revision)
        db.add(CoordinateOperationRun(
            id=run_id, plan_id=plan_id, session_id=session_id,
            created_by=actor.get("user_id"), plan_revision=plan_revision,
            plan_digest=plan_digest, status="pending", idempotency_key=idem))
        db.commit()

    from app.services.data_operations.runner import submit_async_job

    try:
        job_id, job_status, reused = submit_async_job(
            task_type=contracts.TASK_TYPE,
            kind=contracts.JOB_KIND,
            display_name=f"坐标操作 · {(plan.get('title') or '')[:24]}",
            params={"session_id": session_id, "plan_id": plan_id,
                    "run_id": run_id, "plan_digest": plan_digest},
            idempotency_key=idem,
            owner_id=actor.get("user_id"),
            owner_token=actor.get("owner_token"),
            org_id=actor.get("org_id"),
            session_id=session_id,
            project_id=plan.get("project_id"),
        )
    except Exception:
        with SessionLocal() as db:
            from app.models.coordinate_operations import CoordinateOperationRun as R

            dead = db.get(R, run_id)
            if dead is not None:
                db.delete(dead)
                db.commit()
        raise
    with SessionLocal() as db:
        from app.models.coordinate_operations import CoordinateOperationRun as R

        run = db.get(R, run_id)
        run.job_id = job_id
        db.commit()
    if reused and job_status in ("pending", "queued", "running", "cancelling"):
        twin = _find_run_by_job(session_id, job_id)
        if twin is not None and twin != run_id:
            with SessionLocal() as db:
                from app.models.coordinate_operations import CoordinateOperationRun as R

                dead = db.get(R, run_id)
                if dead is not None:
                    db.delete(dead)
                    db.commit()
            return {**_run_view(session_id, twin), "idempotent_reuse": True,
                    "job_id": job_id, "poll": f"/api/v1/tasks/jobs/{job_id}"}

    ctx = {
        "run_id": run_id, "job_id": job_id, "session_id": session_id,
        "plan_id": plan_id, "plan": plan, "plan_revision": plan_revision,
        "plan_digest": plan_digest, "op_json": op_json, "actor": actor,
        "source_spec": plan.get("source") or {},
        "expected_source_fp": expected_source_fp,
    }
    if background:
        runner_task = asyncio.get_running_loop().create_task(
            _run_execution_job(run_id, job_id, ctx),
            name=f"coord-ops:{run_id}")
        _RUNNERS[run_id] = runner_task
        runner_task.add_done_callback(lambda t: _RUNNERS.pop(run_id, None))
        return _run_view(session_id, run_id) | {
            "job_id": job_id, "poll": f"/api/v1/tasks/jobs/{job_id}"}

    await _run_execution_job(run_id, job_id, ctx)
    view = _run_view(session_id, run_id)
    view["job_id"] = job_id
    return view


async def _run_execution_job(
    run_id: str, job_id: int, ctx: Dict[str, Any],
) -> None:
    from app.services.data_operations.runner import run_async_job

    async def body(handle: Any) -> Dict[str, Any]:
        return await _execute_body(handle, ctx)

    await run_async_job(job_id, body)


def _mark_run(
    session_id: str, run_id: str, status: str, *,
    error_code: Optional[str] = None, patch: Optional[Dict[str, Any]] = None,
) -> None:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun

    with SessionLocal() as db:
        run = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.id == run_id)
        ).scalar_one_or_none()
        if run is None:
            return
        run.status = status
        run.error_code = error_code
        if status in ("completed", "failed", "cancelled"):
            from datetime import datetime, timezone

            run.finished_at = datetime.now(timezone.utc)
        if patch:
            for k, v in patch.items():
                setattr(run, k, v)
        db.commit()


async def _execute_body(handle: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """执行体（WP07–WP09 全链）：详见模块 docstring 的不变式清单。"""

    from app.lib.cancellation import OperationCancelled
    from app.services.coordinate_operations import freeze

    run_id = ctx["run_id"]
    session_id = ctx["session_id"]
    plan = ctx["plan"]
    op_meta = json.loads(ctx["op_json"])
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.monotonic()

    _mark_run(session_id, run_id, "running")
    staging = outputs.staging_dir(run_id)
    try:
        handle.checkpoint()
        # 1. 源终复核（执行刻度）+ 冻结（C25/C26）
        snapshot = await freeze.resolve_snapshot(
            session_id, ctx["source_spec"], ctx["actor"])
        if snapshot.content_fingerprint[:128] != ctx["expected_source_fp"]:
            raise CoordinateOperationError(
                "source_revision_changed",
                "源在执行前复核中发生变化；请更新计划并重新锁定")
        frozen = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                freeze.freeze_source, snapshot, staging))
        handle.checkpoint()

        # 2. 冻结操作 + 环境复核（C10）→ 流式全量变换（all-or-nothing）
        op = execute.FrozenOperation(
            op_meta["operation_projjson"], op_meta["definition_sha256"],
            op_meta["environment"])
        target_crs = plan["target_crs"]
        owner_loop = asyncio.get_running_loop()  # P1-2：进度跨线程回投
        stats = await owner_loop.run_in_executor(
            _EXEC_POOL, functools.partial(
                execute.run_streaming_transform, frozen, staging, op,
                target_crs, output_name="output", handle=handle,
                batch_rows=contracts.EXECUTE_BATCH_ROWS,
                loop=owner_loop))
        handle.checkpoint()

        # 3. staging 核验（行数/CRS 声明一致性）
        verified = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                execute.verify_staged_output, staging, "output", target_crs,
                stats["rows_transformed"] + stats["null_geometry"]
                + stats["empty_geometry"]))
        handle.checkpoint()

        # 4. 严格发布门（C39/C40）→ manifest → 原子发布
        outputs.strict_publish_gate(handle, ctx["job_id"])
        members = {
            "output.geojson": verified["geojson_sha256"],
            "source-frozen." + frozen["carrier"]:
                frozen["frozen_sha256"],
        }
        if stats["parquet_projection"]:
            members["output.parquet"] = outputs._sha256_file(
                staging / "output.parquet")
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                outputs.write_manifest, run_id,
                members=members, output_crs=verified["crs"],
            operation={
                "candidate_id": op_meta["candidate"]["candidate_id"],
                "name": op_meta["candidate"]["name"],
                "accuracy": op_meta["candidate"]["accuracy"],
                "accuracy_unknown": op_meta["candidate"]["accuracy_unknown"],
                "ballpark": op_meta["candidate"]["ballpark"],
                "definition_sha256": op_meta["definition_sha256"],
                "environment": op_meta["environment"],
            },
                source={
                    "kind": frozen["source_kind"],
                    "fingerprint": frozen["source_fingerprint"],
                    "row_count": frozen["row_count"],
                    "crs": frozen["crs"],
                },
                stats=stats))
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(outputs.atomic_publish, run_id))
        handle.checkpoint()

        # 5. 原生交付 ref（C23/C24：G18 保持 CRS 交付的主成果载体）
        native_refs = await _register_native_delivery_refs(
            session_id, run_id, plan, stats)

        # 6. 台账 + DataObject + 显示副本（显示失败不伤主成果，C22）
        artifact_ref = await outputs.register_session_artifact(
            session_id, run_id,
            descriptor={"title": plan.get("title") or run_id,
                        "crs": verified["crs"],
                        "rows": verified["rows"]},
            metadata={"plan_id": ctx["plan_id"],
                      "operation": op_meta["candidate"]["name"]})
        data_object = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                outputs.publish_data_object_for_run, run_id,
                owner_scope={"session_id": session_id},
                source_refs=[frozen["source_fingerprint"]]))
        # 显示副本隔离层（C22）：任何意外异常都不波及已发布主成果 ——
        # 主成果保持 completed + display 状态单独披露。
        try:
            display = await _mint_display_copy(
                handle, session_id, run_id, target_crs, plan)
        except CoordinateOperationError as e:
            display = {"state": "failed", "reason": e.reason,
                       "error_code": e.error_code}
        except Exception as e:  # noqa: BLE001 — 显示失败不伤主成果（深度防御）
            logger.warning("[coord-ops] display copy raised for %s: %s",
                           run_id, e)
            display = {"state": "failed",
                       "reason": f"{type(e).__name__}: {e}"}
        handle.checkpoint()

        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        receipt = _bounded_json({
            "run_id": run_id,
            "rows_transformed": stats["rows_transformed"],
            "rows_total": verified["rows"],
            "null_geometry": stats["null_geometry"],
            "empty_geometry": stats["empty_geometry"],
            "total_vertices": stats["total_vertices"],
            "geometry_types": stats["geometry_types"],
            "output_crs": verified["crs"],
            "output_ref": artifact_ref,
            "ledger_registration": ("ok" if artifact_ref else "failed"),
            "output_sha256": verified["geojson_sha256"],
            "geojson_bytes": stats["geojson_bytes"],
            "parquet_projection": stats["parquet_projection"],
            "operation": {
                "candidate_id": op_meta["candidate"]["candidate_id"],
                "name": op_meta["candidate"]["name"],
                "accuracy": op_meta["candidate"]["accuracy"],
                "accuracy_unknown": op_meta["candidate"]["accuracy_unknown"],
                "ballpark": op_meta["candidate"]["ballpark"],
                "definition_sha256": op_meta["definition_sha256"],
            },
            "environment": op_meta["environment"],
            "source": {
                "kind": frozen["source_kind"],
                "fingerprint": frozen["source_fingerprint"],
                "row_count": frozen["row_count"]},
            "data_object": data_object,
            "native_refs": native_refs,
            "display": display,
            "budget_measured": {
                "duration_s": round(time.monotonic() - t0, 3),
                "rss_kb_before": int(rss_before),
                "rss_kb_after": int(rss_after),
                "frozen_bytes": frozen["frozen_bytes"],
                "output_bytes": stats["geojson_bytes"] + stats["parquet_bytes"],
                "batch_rows": stats["batch_rows"]},
            "z_policy": plan.get("z_policy", "preserve_unconverted"),
            "z_note": ("horizontal-only：Z 原值保留、未做高程基准变换"
                       if plan.get("z_policy", "preserve_unconverted")
                       == "preserve_unconverted" else None),
        }, contracts.RECEIPT_JSON_MAX_BYTES, "收据")
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                outputs.write_receipt, run_id, receipt))
        # 收据可回读才 completed（C38）
        reread = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(outputs.read_receipt, run_id))
        if reread is None:
            raise CoordinateOperationError(
                "internal_error", "收据写入后不可回读（run 不落 completed）")
        _mark_run(session_id, run_id, "completed", patch={
            "receipt": receipt,
            "output_ref": receipt["output_ref"],
            "output_sha256": receipt["output_sha256"],
            "output_rows": receipt["rows_total"],
        })
        return receipt
    except OperationCancelled:
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(outputs.discard_staging, run_id))
        _mark_run(session_id, run_id, "cancelled")
        raise
    except CoordinateOperationError as e:
        if e.error_code in ("publish_blocked",):
            # 可恢复阻断：保留 staging，run 落 failed+码 —— 除非阻断源于
            # 持久取消事实（评审 P3-10：用户主动取消不得显示为「失败」）。
            cancelled = bool((e.details or {}).get("cancel_fact")) \
                or "持久取消事实" in e.reason or handle.token.cancelled
            if cancelled or handle.token.cancelled:
                await asyncio.get_running_loop().run_in_executor(
                    _EXEC_POOL, functools.partial(
                        outputs.discard_staging, run_id))
                _mark_run(session_id, run_id, "cancelled")
                raise OperationCancelled(
                    "publish blocked by durable cancel", job_id=ctx["job_id"]
                ) from e
            _mark_run(session_id, run_id, "failed", error_code=e.error_code)
            raise
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, outputs.discard_staging, run_id)
        _mark_run(session_id, run_id, "failed", error_code=e.error_code)
        raise
    except BaseException:  # noqa: BLE001 — 未预期异常同样诚实失败
        await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, outputs.discard_staging, run_id)
        _mark_run(session_id, run_id, "failed", error_code="internal_error")
        raise


async def _register_native_delivery_refs(
    session_id: str, run_id: str, plan: Dict[str, Any], stats: Dict[str, Any],
) -> Dict[str, Any]:
    """主成果的 G18 交付载体登记（增值；失败单独披露，不阻断完成）。

    - fabric-parquet（pyarrow 环境必有投影）：保持 target CRS 的原生交付
      载体 —— G18 无 target_crs 交付时不再次选操作（C23）；
    - 全量 session FC ref（≤DISPLAY_REF_MAX_ROWS）：非 pyarrow 环境的
      G18 session_ref 交付路径（crs 成员 + schema_info.crs 权威证据）。
    """
    out: Dict[str, Any] = {
        "native_fabric_ref": None, "output_session_ref": None,
        "session_ref_reason": None}
    title = plan.get("title") or run_id
    try:
        final_parquet = outputs.final_dir(run_id) / "output.parquet"
        if final_parquet.is_file():
            fab = await asyncio.get_running_loop().run_in_executor(
                _EXEC_POOL, functools.partial(
                    outputs.register_fabric_parquet, session_id,
                    final_parquet, title=title,
                    crs=plan["target_crs"],
                    rows=stats["rows_transformed"]
                    + stats["null_geometry"] + stats["empty_geometry"],
                    run_id=run_id))
            from app.services.artifact_registry import register_artifact

            await register_artifact(
                session_id, artifact_id=fab["artifact_ref"],
                artifact_type="fabric_geoparquet",
                producer_capability="coordinate_operations",
                producer_tool=contracts.TASK_TYPE,
                descriptor={
                    "title": fab["title"], "crs": fab["crs"],
                    "rows": fab["rows"],
                    "content_sha256": fab["content_sha256"],
                    "coordinate_operation_run": run_id},
                metadata={"coordinate_operation_native": True})
            out["native_fabric_ref"] = fab["artifact_ref"]
    except Exception as e:  # noqa: BLE001 — 增值登记失败不阻断完成
        logger.warning("[coord-ops] native fabric ref failed %s: %s", run_id, e)
        out["native_fabric_ref"] = None
    try:
        fc = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(outputs.load_output_fc, run_id))
        n = len(fc.get("features") or [])
        if n <= contracts.DISPLAY_REF_MAX_ROWS:
            from app.services.session_data import session_data_manager

            ref = await session_data_manager.store(
                session_id, fc, prefix="coord-ops-output")
            out["output_session_ref"] = ref
        else:
            out["session_ref_reason"] = (
                f"{n} 行超过 session ref 上限 "
                f"{contracts.DISPLAY_REF_MAX_ROWS}；用 native_fabric_ref 交付")
    except Exception as e:  # noqa: BLE001
        logger.warning("[coord-ops] output session ref failed %s: %s", run_id, e)
        out["session_ref_reason"] = f"{type(e).__name__}: {e}"
    return out


_EXEC_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="coord-ops")


def _transform_display_features(feats: List[Any], op: Any) -> List[Any]:
    """显示副本逐要素变换（线程池执行；评审 P3-8：不阻塞事件循环）。"""
    stats = execute.BatchStats()
    out: List[Any] = []
    for i, f in enumerate(feats):
        out.append(execute.transform_feature(f, op, stats, row_index=i + 1))
    return out


async def _mint_display_copy(
    handle: Any, session_id: str, run_id: str, target_crs: str,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """WGS84 显示副本（独立转换链 + 证据；失败只影响 display，C21/C22）。"""
    from app.services.coordinate_operations import candidates as cand_mod

    try:
        fc = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(outputs.load_output_fc, run_id))
        listing = cand_mod.list_operations(target_crs, "EPSG:4326", None)
        chosen = next((c for c in listing["candidates"]
                       if c["availability"] == "available"), None)
        if chosen is None:
            return {"state": "unavailable",
                    "reason": "target → WGS84 在当前环境无可用操作",
                    "candidates_available": listing["available_count"]}
        resolved = cand_mod.resolve_candidate_for_lock(
            target_crs, "EPSG:4326", None, chosen["candidate_id"])
        op = execute.FrozenOperation(
            resolved["operation_projjson"], resolved["definition_sha256"],
            resolved["environment"])
        handle.checkpoint()
        feats = fc.get("features") or []
        out_feats = await asyncio.get_running_loop().run_in_executor(
            _EXEC_POOL, functools.partial(
                _transform_display_features, feats, op))
        display_fc = {
            "type": "FeatureCollection",
            "features": out_feats,
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "properties": {"schema_info": {"crs": "EPSG:4326"},
                           "display_copy_of": run_id},
        }
        display_ref_info = await outputs.mint_display_ref(
            session_id, display_fc, label=f"{plan.get('title') or run_id} · WGS84")
        result = {
            "state": "available",
            "display_ref": display_ref_info["display_ref"],
            "rows": display_ref_info["rows"],
            "operation": {
                "candidate_id": chosen["candidate_id"],
                "name": chosen["name"],
                "accuracy": chosen["accuracy"],
                "accuracy_unknown": chosen["accuracy_unknown"],
                "ballpark": chosen["ballpark"],
                "definition_sha256": chosen["definition_sha256"]},
            "note": "显示副本是独立 WGS84 派生物（用于地图显示）；"
                    "GeoJSON 交付应使用本副本或 4326 源",
        }
        # fabric-parquet 4326 投影（pyarrow 可用时；供全量 GeoJSON 交付）
        try:
            import pyarrow  # noqa: F401

            from app.services.data_fabric.vector_carrier import features_to_arrow

            table = features_to_arrow(out_feats, crs="EPSG:4326")
            import tempfile

            from pathlib import Path

            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tf:
                tmp = Path(tf.name)
            import pyarrow.parquet as pq

            pq.write_table(table, str(tmp), compression="zstd")
            fab = outputs.register_fabric_parquet(
                session_id, tmp, title=f"{plan.get('title') or run_id} · WGS84",
                crs="EPSG:4326", rows=len(out_feats), run_id=run_id)
            tmp.unlink(missing_ok=True)
            from app.services.artifact_registry import register_artifact

            await register_artifact(
                session_id, artifact_id=fab["artifact_ref"],
                artifact_type="fabric_geoparquet",
                producer_capability="coordinate_operations",
                producer_tool=contracts.TASK_TYPE,
                descriptor={"title": fab["title"], "crs": "EPSG:4326",
                            "rows": fab["rows"],
                            "content_sha256": fab["content_sha256"],
                            "display_copy_of": run_id},
                metadata={"coordinate_operation_display": True})
            result["display_fabric_ref"] = fab["artifact_ref"]
        except ImportError:
            result["display_fabric_ref"] = None
        return result
    except CoordinateOperationError as e:
        if e.error_code == "display_unavailable":
            return {"state": "unavailable", "reason": e.reason}
        return {"state": "failed", "reason": e.reason,
                "error_code": e.error_code}
    except Exception as e:  # noqa: BLE001 — 显示失败不伤主成果
        logger.warning("[coord-ops] display copy failed for %s: %s", run_id, e)
        return {"state": "failed",
                "reason": f"{type(e).__name__}: {e}"}


# ── 运行视图 / 取消 / 结果 / 恢复 ─────────────────────────────────────────

def _run_view(session_id: str, run_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        run = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.id == run_id,
            CoordinateOperationRun.session_id == session_id)
        ).scalar_one_or_none()
        if run is None:
            raise CoordinateOperationError("not_found", f"run {run_id} 不存在")
        return {
            "run_id": run.id, "plan_id": run.plan_id, "status": run.status,
            "job_id": run.job_id, "error_code": run.error_code,
            "output_ref": run.output_ref, "output_rows": run.output_rows,
            "receipt": run.receipt, "plan_revision": run.plan_revision,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat()
            if run.finished_at else None,
        }


def _find_completed_run_by_key(
    session_id: str, base_key: str,
) -> Optional[Dict[str, Any]]:
    """按 base_key 前缀匹配（评审 P1-1）：世代键（:g2…）的 completed run
    同样可复用 —— 同输入重复执行不再因历史失败世代而全量重跑。"""
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.session_id == session_id,
            CoordinateOperationRun.idempotency_key.like(base_key + "%"),
            CoordinateOperationRun.status == "completed")
            .order_by(CoordinateOperationRun.created_at.desc()).limit(1)
        ).scalars().all()
        if not rows:
            return None
        r = rows[0]
        return {"run_id": r.id, "status": r.status, "receipt": r.receipt,
                "output_ref": r.output_ref, "job_id": r.job_id}


def _find_active_run_by_key(
    session_id: str, base_key: str,
) -> Optional[Dict[str, Any]]:
    """同前缀语义：任一世代的活跃 run 即收敛锚（不并发起新执行）。"""
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.session_id == session_id,
            CoordinateOperationRun.idempotency_key.like(base_key + "%"),
            CoordinateOperationRun.status.in_(("pending", "running")))
            .order_by(CoordinateOperationRun.created_at.desc()).limit(1)
        ).scalars().all()
        if not rows:
            return None
        r = rows[0]
        return {"run_id": r.id, "job_id": r.job_id}


def _count_runs_by_key_prefix(session_id: str, base_key: str) -> int:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = db.execute(select(CoordinateOperationRun.id).where(
            CoordinateOperationRun.session_id == session_id,
            CoordinateOperationRun.idempotency_key.like(base_key + "%"))
        ).scalars().all()
        return len(rows)


def _find_run_by_job(session_id: str, job_id: int) -> Optional[str]:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        row = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.session_id == session_id,
            CoordinateOperationRun.job_id == job_id)
            .order_by(CoordinateOperationRun.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        return row.id if row else None


def _invalidate_run(session_id: str, run_id: str, code: str) -> None:
    _mark_run(session_id, run_id, "failed", error_code=code)


async def list_runs(
    session_id: str, *, plan_id: Optional[str] = None, limit: int = 50,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    with SessionLocal() as db:
        stmt = select(CoordinateOperationRun).where(
            CoordinateOperationRun.session_id == session_id)
        if plan_id:
            stmt = stmt.where(CoordinateOperationRun.plan_id == plan_id)
        rows = db.execute(stmt.order_by(
            CoordinateOperationRun.created_at.desc())
            .limit(min(max(limit, 1), 100))).scalars().all()
        return {"runs": [
            {k: v for k, v in _row_dict(r).items() if k != "receipt"}
            | {"receipt_summary": _receipt_summary(r.receipt)}
            for r in rows]}


def _row_dict(r: Any) -> Dict[str, Any]:
    return {
        "run_id": r.id, "plan_id": r.plan_id, "status": r.status,
        "job_id": r.job_id, "error_code": r.error_code,
        "output_ref": r.output_ref, "output_rows": r.output_rows,
        "receipt": r.receipt, "created_at":
            r.created_at.isoformat() if r.created_at else None,
    }


def _receipt_summary(receipt: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(receipt, dict):
        return None
    return {k: receipt.get(k) for k in (
        "rows_total", "output_crs", "output_ref", "display")}


async def inspect_run(session_id: str, run_id: str) -> Dict[str, Any]:
    return _run_view(session_id, run_id)


async def run_result_page(
    session_id: str, run_id: str, cursor: int, limit: int,
) -> Dict[str, Any]:
    view = _run_view(session_id, run_id)
    if view["status"] != "completed":
        raise CoordinateOperationError(
            "result_unavailable",
            f"run 状态 {view['status']}；完成後才有结果分页")
    return await asyncio.get_running_loop().run_in_executor(
        _EXEC_POOL, functools.partial(
            outputs.result_page, run_id, cursor, limit))


async def cancel_run(session_id: str, run_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    view = _run_view(session_id, run_id)
    if view["status"] in ("completed", "failed", "cancelled"):
        return {"run_id": run_id, "status": view["status"],
                "already_terminal": True}
    job_id = view.get("job_id")
    if job_id is None:
        _mark_run(session_id, run_id, "cancelled")
        return {"run_id": run_id, "status": "cancelled"}
    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, job_id)
        status = str(getattr(record, "status", "pending")) if record else None
        if record is not None and status in (
                "completed", "failed", "cancelled", "stale"):
            db.commit()
            return {"run_id": run_id, "status": status,
                    "already_terminal": True}
        DurableJobStore.request_cancel_sync(db, job_id)
        db.commit()
    task = _RUNNERS.get(run_id)
    if task is not None:
        # 进程内执行体经看门狗 ≤2.5s+抖动感知；不强制 cancel 任务
        pass
    return {"run_id": run_id, "status": "cancelling"}


async def display_ref_for_run(
    session_id: str, run_id: str, actor: Dict[str, Any],
) -> Dict[str, Any]:
    """重新铸显示副本 session ref（刷新后恢复地图层；服务器为事实源）。"""
    view = _run_view(session_id, run_id)
    if view["status"] != "completed":
        raise CoordinateOperationError(
            "display_unavailable", f"run 状态 {view['status']}")
    receipt = view.get("receipt") or {}
    target_crs = ((receipt.get("output_crs")) or "")
    if not target_crs:
        raise CoordinateOperationError(
            "display_unavailable", "收据缺少输出 CRS（无法重建显示链）")
    plan = None
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_plan_row(db, session_id, view["plan_id"])
        plan = dict(row.plan or {})
    display = await _mint_display_copy(
        _NullHandle(), session_id, run_id, target_crs, plan)
    if display.get("state") != "available":
        raise CoordinateOperationError(
            "display_unavailable",
            display.get("reason") or "显示副本不可用")
    return display


class _NullHandle:
    """display 重铸无 job 上下文时的占位（无取消语义，仅满足接口）。"""

    worker_id = "display-remint"
    attempt = 0

    def checkpoint(self) -> None:
        return None

    def progress(self, *a: Any, **k: Any) -> bool:  # noqa: ARG002
        return False


async def recover_all() -> Dict[str, Any]:
    """全局孤儿收敛（lifespan 启动接线，评审 P2-5；G38 dj_recovery 同款）。

    遍历全部非终态 run 行：job 已终态 → 按权威文件/manifest 收敛（C38）；
    超龄 staging（run 已终态）清理。进程崩溃窗口不再等手动 /recover。
    """
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from sqlalchemy import select

    converged: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        runs = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.status.in_(("pending", "running")))
        ).scalars().all()
        session_ids = sorted({r.session_id for r in runs})
        db.commit()
    for sid in session_ids:
        try:
            out = await recover(sid)
            converged.extend(out.get("converged") or [])
        except Exception as e:  # noqa: BLE001 — 单会话失败不阻断整体收敛
            logger.warning("[coord-ops] recover_all session %s failed: %s",
                           sid, e)
    # 超龄 staging 兜底（run 已终态或行已不存在的遗留目录，>1h mtime）
    import shutil as _shutil
    from datetime import datetime, timedelta, timezone

    staging_root = outputs.domain_root() / ".staging"
    if staging_root.is_dir():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        for child in staging_root.iterdir():
            try:
                if child.is_dir() and datetime.fromtimestamp(
                        child.stat().st_mtime, tz=timezone.utc) < cutoff:
                    _shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    if converged:
        logger.info("[coord-ops] recover_all converged %d runs", len(converged))
    return {"converged": converged}


async def recover(session_id: str) -> Dict[str, Any]:
    """孤儿收敛（C29/C38）：崩溃窗口三态按权威文件/manifest 恢复。"""
    from app.core.database import SessionLocal
    from app.models.coordinate_operations import CoordinateOperationRun
    from app.services.jobs.lifecycle import coerce_status, is_terminal
    from app.services.jobs.store import DurableJobStore
    from sqlalchemy import select

    converged: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        runs = db.execute(select(CoordinateOperationRun).where(
            CoordinateOperationRun.session_id == session_id,
            CoordinateOperationRun.status.in_(("pending", "running")))
        ).scalars().all()
        for run in runs:
            job = (DurableJobStore.get_sync(db, run.job_id)
                   if run.job_id else None)
            job_status = coerce_status(
                getattr(job, "status", None)).value if job else None
            final_dir = outputs.final_dir(run.id)
            if job is None or not is_terminal(coerce_status(job_status)):
                continue
            # job 已终态：run 行收敛
            if job_status == "completed" and final_dir.is_dir():
                # 主成果可能已发布但回执未确认（C38）：核验后补收据
                try:
                    outputs.verify_published(run.id)
                    receipt = outputs.read_receipt(run.id) or {
                        "run_id": run.id,
                        "recovered": True,
                        "output_crs": outputs.read_manifest(run.id).get(
                            "output_crs"),
                    }
                    if outputs.read_receipt(run.id) is None:
                        await asyncio.get_running_loop().run_in_executor(
                            _EXEC_POOL, functools.partial(
                                outputs.write_receipt, run.id, receipt))
                    run.status = "completed"
                    run.receipt = receipt
                    run.output_ref = receipt.get(
                        "output_ref", f"ref:coord-ops/{run.id}")
                    converged.append({"run_id": run.id, "action":
                                      "receipt_finalized"})
                except CoordinateOperationError:
                    run.status = "failed"
                    run.error_code = "result_unavailable"
                    converged.append({"run_id": run.id, "action": "invalidated"})
            elif job_status == "cancelled":
                run.status = "cancelled"
                converged.append({"run_id": run.id, "action": "cancelled"})
            else:
                run.status = "failed"
                run.error_code = run.error_code or "execute_failed"
                converged.append({"run_id": run.id, "action": "failed"})
            from datetime import datetime, timezone

            run.finished_at = datetime.now(timezone.utc)
        # 孤儿 staging（对应 run 已终态）清理
        db.commit()
    staging_root = outputs.domain_root() / ".staging"
    if staging_root.is_dir():
        import shutil

        for child in staging_root.iterdir():
            rid = child.name
            view = None
            try:
                view = _run_view(session_id, rid)
            except CoordinateOperationError:
                pass
            if view is not None and view["status"] in (
                    "completed", "failed", "cancelled"):
                shutil.rmtree(child, ignore_errors=True)
    return {"converged": converged}
