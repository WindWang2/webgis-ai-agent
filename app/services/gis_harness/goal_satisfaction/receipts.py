"""G28 P1 —— 新能力权威回执的只读收集与证据投影。

单一职责：把 G13–G20 已合入能力的**持久化权威回执**投影成 goal_satisfaction
证据面。红线：

- **只读**：只调源服务的公开读路径（journal/list_receipts/get_receipts/
  get_delivery_job/MapSpec 文档），不复制算法、不重算、不写任何状态；
- **授权不放宽**：交付回执读取走既有 ``get_delivery_job`` 授权（actor =
  会话 owner），且额外按 ``session_id == 本会话`` 过滤 —— 其他会话/其他
  owner 的回执绝不进入证据面（A17）；
- **失败开路**：任一收集面异常 → 该族置空（诚实缺席），绝不伪造回执；
- **旧回执兼容**：缺字段按 unknown 处理（无码不造码，A25）；
- **有界**：每族上限固定（edit≤4/compute≤8/import≤8/delivery≤4/raster≤8/
  refresh≤2），bundle 总体可序列化。

kill switch：``GIS_GOAL_RECEIPT_ADAPTERS=0`` 关闭全部收集（返回空 bundle，
评估零漂移）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    EvidenceClass,
    EvidenceKind,
    EvidenceStatus,
    GoalEvidence,
)

logger = logging.getLogger(__name__)

#: kill switch（默认开；"0"/"false" 关闭 —— 关闭后证据面回到 G27 前形状）。
ENV_RECEIPT_ADAPTERS = "GIS_GOAL_RECEIPT_ADAPTERS"

#: 每族回执上限（有界；超限截断 + notes 披露）。
FAMILY_BUDGETS = {
    "edit": 4,
    "compute": 8,
    "import": 8,
    "delivery": 4,
    "raster": 8,
    "refresh": 2,
}

_FAMILIES = ("edit", "compute", "import", "delivery", "raster", "refresh")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def adapters_enabled() -> bool:
    return str(os.environ.get(ENV_RECEIPT_ADAPTERS, "1")).strip().lower() \
        not in ("0", "false", "off")


def empty_bundle(session_id: str = "", *, disabled: bool = False,
                 notes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "session_id": str(session_id or "")[:64],
        "families": {f: [] for f in _FAMILIES},
        "disabled": bool(disabled),
        "notes": [str(n)[:120] for n in (notes or [])][:6],
    }


def _project(row: Any, whitelist: tuple) -> Dict[str, Any]:
    """模型/字典 → 白名单字段投影（值统一截断标量化；缺字段 = 不出场）。"""
    src = row if isinstance(row, dict) else _model_dump(row)
    out: Dict[str, Any] = {}
    for key, limit in whitelist:
        if key not in src:
            continue
        value = src.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, int):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:limit]
        else:
            out[key] = value
    return out


def _model_dump(row: Any) -> Dict[str, Any]:
    dump = getattr(row, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


# ── 收集器（async；每族独立开路）────────────────────────────────────────

async def _collect_edit(session_id: str) -> List[Dict[str, Any]]:
    """G14 编辑 journal 头（真源 = 持久 journal；回执由 executor 派生）。"""
    from app.services.feature_edit.contracts import JOURNALS_STATE_KEY
    from app.services.feature_edit.journal import head_entry
    from app.services.session_data import session_data_manager

    journals = await session_data_manager.get_state_field(
        session_id, JOURNALS_STATE_KEY)
    if not isinstance(journals, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for ref_id, journal in list(journals.items())[:FAMILY_BUDGETS["edit"]]:
        head = head_entry(journal if isinstance(journal, dict) else {})
        if not isinstance(head, dict) or not head:
            continue
        deltas = [d for d in (head.get("deltas") or []) if isinstance(d, dict)]
        rows.append(_project(head, (
            ("seq", 10**9), ("kind", 8), ("origin", 8),
            ("content_revision_after", 10**9),
            ("fingerprint_after", 64),
            ("created_at", 40),
        )))
        row = rows[-1]
        row["ref_id"] = str(ref_id)[:64]
        row["created_count"] = sum(
            1 for d in deltas if d.get("presence") == "created")
        row["removed_count"] = sum(
            1 for d in deltas if d.get("presence") == "removed")
    return rows


async def _collect_compute(session_id: str) -> List[Dict[str, Any]]:
    """G19 ComputeReceipt（published 即持久权威；草稿回执不在场）。"""
    from app.services.data_preparation.field_compute import receipt as fc_receipt

    receipts = await fc_receipt.list_receipts(
        session_id, published_only=True, limit=FAMILY_BUDGETS["compute"])
    rows: List[Dict[str, Any]] = []
    for rcpt in receipts:
        dump = _model_dump(rcpt) or _dict(rcpt)
        row = _project(dump, (
            ("plan_id", 64), ("compute_fingerprint", 64),
            ("input_ref", 96), ("output_ref", 96),
            ("input_content_hash", 64), ("output_content_hash", 64),
            ("row_count", 10**9), ("computed_rows", 10**9),
            ("selected_rows", 10**9), ("skipped_rows", 10**9),
            ("null_count", 10**9), ("error_count", 10**9),
            ("descriptor_minted", 10), ("created_at", 40),
        ))
        cols = [c for c in (dump.get("columns") or []) if isinstance(c, dict)]
        row["columns"] = [
            _project(c, (
                ("name", 64), ("unit", 24), ("unit_status", 16),
                ("measurement_kind", 24), ("computed", 10),
            ))
            for c in cols[:8]
        ]
        degraded = [str(d)[:48] for d in (dump.get("degraded") or [])[:4]]
        if degraded:
            row["degraded"] = degraded
        rows.append(row)
    return rows


async def _collect_import(session_id: str) -> List[Dict[str, Any]]:
    """G17 导入回执（batch.json 持久面；只读 get_receipts 公开入口）。"""
    from app.services.data_import.service import get_data_import_service

    service = get_data_import_service()
    summaries = await service.list_batches(session_id)
    rows: List[Dict[str, Any]] = []
    for summary in summaries[:2]:
        batch_id = str((summary or {}).get("batch_id") or "")
        if not batch_id:
            continue
        try:
            payload = await service.get_receipts(batch_id, session_id)
        except Exception:  # noqa: BLE001 — 单 batch 失败 → 该 batch 缺席
            continue
        receipts = _dict(payload.get("receipts"))
        for member_key, rcpt in list(receipts.items())[
                :FAMILY_BUDGETS["import"] - len(rows)]:
            row = _project(_dict(rcpt), (
                ("status", 16), ("output_kind", 16), ("output_ref", 96),
                ("input_rows", 10**9), ("output_rows", 10**9),
                ("rejected_rows", 10**9), ("geometry_type", 24),
                ("duplicate", 10), ("plan_hash", 64),
                ("quality_status", 16), ("error_code", 48),
            ))
            row["member_key"] = str(member_key)[:64]
            crs = _dict(_dict(rcpt).get("crs_evidence"))
            row["crs_source"] = str(crs.get("crs_source") or "")[:24]
            rows.append(row)
        if len(rows) >= FAMILY_BUDGETS["import"]:
            break
    return rows


async def _collect_delivery(session_id: str) -> List[Dict[str, Any]]:
    """G18 交付回执（AnalysisTask 按 session 过滤 + 既有 get_delivery_job
    授权 —— 授权不放宽：匿名会话/无权任务 → 诚实缺席）。"""
    rows: List[Dict[str, Any]] = []
    try:
        from app.core.database import SessionLocal
        from app.models.db_model import AnalysisTask, Conversation
        from app.services.data_delivery import service as dl_service
        from app.services.data_delivery.source_resolver import DeliveryActor

        def _query() -> Tuple[List[str], Optional[str]]:
            with SessionLocal() as db:
                conv = db.query(Conversation).filter(
                    Conversation.id == session_id).first()
                if conv is None or not getattr(conv, "user_id", None):
                    # 匿名会话无 actor 身份 —— 不读交付回执（授权不放宽）。
                    return [], None
                records = db.query(AnalysisTask).filter(
                    AnalysisTask.task_type == dl_service.TASK_TYPE,
                    AnalysisTask.session_id == session_id,
                ).order_by(AnalysisTask.created_at.desc())[
                    :FAMILY_BUDGETS["delivery"]].all()
                return ([str(r.id) for r in records],
                        str(conv.user_id))

        job_ids, conv_user = await asyncio.to_thread(_query)
        if not job_ids or not conv_user:
            return []
        actor = DeliveryActor(user_id=conv_user)
        for job_id in job_ids:
            try:
                view = await dl_service.get_delivery_job(job_id, actor)
            except Exception:  # noqa: BLE001 — 无权/损坏 → 该 job 缺席
                continue
            receipt = getattr(view, "receipt", None)
            if receipt is None:
                # job 未完成/回执缺席 —— job 状态本身仍是事实（A19）。
                rows.append({
                    "delivery_id": "", "job_id": str(getattr(view, "job_id",
                                                            job_id))[:64],
                    "format": str(getattr(view, "format", "") or "")[:16],
                    "status": str(getattr(view, "status", "") or "")[:16],
                })
                continue
            dump = _model_dump(receipt) or _dict(receipt)
            row = _project(dump, (
                ("delivery_id", 64), ("job_id", 64), ("format", 16),
                ("status", 16), ("filename", 96), ("row_count", 10**9),
                ("content_sha256", 64), ("durable", 16),
                ("artifact_ref", 96),
            ))
            source = _dict(dump.get("source"))
            row["source_revision"] = str(
                source.get("content_revision") or "")[:64]
            scope = _dict(dump.get("scope"))
            row["scope_subset_of_selection"] = bool(
                scope.get("subset_of_selection"))
            missing = scope.get("missing_ids")
            row["scope_missing_ids"] = (
                [str(m)[:64] for m in missing[:8]]
                if isinstance(missing, list) else [])
            rows.append(row)
    except Exception:  # noqa: BLE001 — DB/服务缺席 → 族置空（诚实缺席）
        logger.debug("[GoalReceipts] delivery collect failed session=%s",
                     session_id, exc_info=True)
    return rows


async def _collect_raster(session_id: str) -> List[Dict[str, Any]]:
    """G20 栅格表达对账（MapSpec numeric_raster 源 = durable truth）。"""
    try:
        from app.services.mapspec_store import mapspec_store

        spec = await mapspec_store.get_mapspec(session_id)
        if not isinstance(spec, dict):
            return []
        sources = _dict(spec.get("sources"))
        rows: List[Dict[str, Any]] = []
        for source_id, source in list(sources.items())[:32]:
            src = _dict(source)
            if str(src.get("type") or "") != "numeric_raster":
                continue
            layers = [
                str(l.get("id") or "")[:64]
                for l in (spec.get("layers") or [])[:64]
                if isinstance(l, dict) and str(l.get("source") or "") == str(source_id)
            ]
            rows.append({
                "source_id": str(source_id)[:64],
                "ref_id": str(src.get("refId") or "")[:96],
                "data_revision": str(src.get("dataRevision") or "")[:64],
                "data_fingerprint": str(src.get("dataFingerprint") or "")[:64],
                "layer_ids": layers[:4],
            })
            if len(rows) >= FAMILY_BUDGETS["raster"]:
                break
        return rows
    except Exception:  # noqa: BLE001 — spec 读失败 → 族置空
        logger.debug("[GoalReceipts] raster collect failed session=%s",
                     session_id, exc_info=True)
        return []


async def _collect_refresh(chapter: Dict[str, Any]) -> List[Dict[str, Any]]:
    """G10 刷新采纳回执（project 上下文在场才消费；session 桥不存在 =
    诚实空 —— A30：不伪造未合入能力的事实源）。"""
    project_id = str(chapter.get("project_id")
                     or _dict(chapter.get("intent")).get("project_id")
                     or "").strip()
    if not project_id:
        return []
    try:
        from app.services.project_runtime.refresh.store import get_refresh_store

        store = get_refresh_store()
        proposals = await store.list_proposals(project_id)
        rows: List[Dict[str, Any]] = []
        for proposal in proposals[:FAMILY_BUDGETS["refresh"]]:
            receipt = _dict(proposal.get("receipt"))
            if not receipt:
                continue
            rows.append(_project(receipt, (
                ("proposal_id", 64), ("action", 12), ("status", 16),
                ("tool_execution", 12), ("task_execution", 12),
                ("map_observation", 16), ("version_adoption", 16),
                ("revision_before", 10**9), ("revision_after", 10**9),
            )))
            rows[-1]["proposal_fingerprint"] = str(
                receipt.get("proposal_fingerprint") or "")[:64]
        return rows
    except Exception:  # noqa: BLE001 — store 缺席 → 族置空
        logger.debug("[GoalReceipts] refresh collect failed", exc_info=True)
        return []


async def collect_capability_receipts(
    session_id: str,
    chapter: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """会话权威回执 → 有界 bundle（finalizer 锁外预取；每族独立开路）。

    任何一族失败不影响其余族；全关（kill switch）→ empty(disabled=True)。
    """
    if not adapters_enabled():
        return empty_bundle(session_id, disabled=True,
                            notes=["receipt_adapters_disabled"])
    session_id = str(session_id or "")
    if not session_id:
        return empty_bundle()
    chapter = chapter if isinstance(chapter, dict) else {}
    families: Dict[str, List[Dict[str, Any]]] = {}
    notes: List[str] = []
    import asyncio

    collectors = (
        ("edit", lambda: _collect_edit(session_id)),
        ("compute", lambda: _collect_compute(session_id)),
        ("import", lambda: _collect_import(session_id)),
        ("delivery", lambda: _collect_delivery(session_id)),
        ("raster", lambda: _collect_raster(session_id)),
        ("refresh", lambda: _collect_refresh(chapter)),
    )
    for name, fn in collectors:
        try:
            families[name] = list(await fn())
        except Exception as exc:  # noqa: BLE001 — 单族失败 → 诚实缺席
            logger.debug("[GoalReceipts] %s collect failed session=%s",
                         name, session_id, exc_info=True)
            families[name] = []
            notes.append(f"{name}_collect_failed:{type(exc).__name__}"[:120])
    bundle = empty_bundle(session_id, notes=notes)
    bundle["families"] = {
        f: families.get(f, [])[:FAMILY_BUDGETS[f]] for f in _FAMILIES
    }
    return bundle


# ── 纯投影面（同输入同输出；测试直接构造 bundle）────────────────────────

def _latest_per_identity(rows: List[Dict[str, Any]],
                         identity_keys: tuple) -> List[Dict[str, Any]]:
    """同身份回执去重（取 created_at / seq 最大者）—— 旧回执不背书新事实
    （A29 语义：数据更新后旧证据失效，靠「保留最新」+ 引用对账）。"""
    best: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        ident = tuple(str(row.get(k) or "") for k in identity_keys)
        if not any(ident):
            continue
        prev = best.get(ident)
        if prev is None or _order_key(row) >= _order_key(prev):
            best[ident] = row
    out = [r for r in best.values()]
    keep = {id(r) for r in out}
    return [r for r in rows if id(r) in keep]


def _order_key(row: Dict[str, Any]) -> tuple:
    created = str(row.get("created_at") or "")
    seq = row.get("seq") or row.get("entry_seq") or 0
    try:
        seq_v = int(seq)
    except (TypeError, ValueError):
        seq_v = 0
    return (created, seq_v)


def receipt_evidence_rows(
    bundle: Optional[Dict[str, Any]],
    *,
    session_id: str = "",
) -> List[GoalEvidence]:
    """bundle → 有界证据行（全部 deterministic；owner 过滤 A17）。"""
    if not isinstance(bundle, dict) or bundle.get("disabled"):
        return []
    # owner/scope 绑定：bundle 必须归属当前会话（A17 —— 他人回执整包拒绝）。
    b_session = str(bundle.get("session_id") or "")
    if b_session and session_id and b_session != str(session_id):
        return []
    families = _dict(bundle.get("families"))
    out: List[GoalEvidence] = []

    for row in families.get("edit", [])[:FAMILY_BUDGETS["edit"]]:
        row = _dict(row)
        ref_id = str(row.get("ref_id") or "")[:56]
        if not ref_id:
            continue
        committed = row.get("seq") is not None
        out.append(GoalEvidence(
            id=f"edit:{ref_id}"[:64],
            kind=EvidenceKind.CAPABILITY_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=EvidenceStatus.PRESENT if committed else EvidenceStatus.ABSENT,
            source="feature_edit:journal_head",
            revision=str(row.get("content_revision_after") or "")[:64],
            ref=ref_id,
            detail=(f"kind={row.get('kind') or 'unknown'} "
                    f"origin={row.get('origin') or 'unknown'} "
                    f"created={row.get('created_count')} "
                    f"removed={row.get('removed_count')}"
                    )[:160],
        ))

    for row in _latest_per_identity(
            [r for r in families.get("compute", []) if isinstance(r, dict)],
            ("compute_fingerprint", "input_ref"))[:FAMILY_BUDGETS["compute"]]:
        output_ref = str(row.get("output_ref") or "")[:56]
        input_ref = str(row.get("input_ref") or "")[:56]
        if not output_ref and not input_ref:
            continue
        out.append(GoalEvidence(
            id=f"compute:{str(row.get('plan_id') or input_ref)[:52]}"[:64],
            kind=EvidenceKind.CAPABILITY_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=EvidenceStatus.PRESENT,
            source="field_compute:receipt",
            revision=str(row.get("output_content_hash") or "")[:64],
            ref=input_ref or output_ref,
            detail=(f"rows={row.get('row_count')} "
                    f"computed={row.get('computed_rows')} "
                    f"skipped={row.get('skipped_rows')} "
                    f"errors={row.get('error_count')}"
                    )[:160],
        ))

    for row in families.get("import", [])[:FAMILY_BUDGETS["import"]]:
        row = _dict(row)
        member_key = str(row.get("member_key") or "")[:56]
        if not member_key:
            continue
        status = str(row.get("status") or "").strip().lower()
        evidence_status = {
            "completed": EvidenceStatus.PRESENT,
            "failed": EvidenceStatus.FAILED,
        }.get(status, EvidenceStatus.ABSENT)  # cancelled/未知 → absent
        out.append(GoalEvidence(
            id=f"import:{member_key}"[:64],
            kind=EvidenceKind.CAPABILITY_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=evidence_status,
            source="data_import:commit_receipt",
            revision=str(row.get("output_ref") or "")[:64],
            ref=str(row.get("output_ref") or "")[:64],
            detail=(f"in={row.get('input_rows')} out={row.get('output_rows')} "
                    f"rejected={row.get('rejected_rows')} "
                    f"crs_source={row.get('crs_source') or 'unknown'}"
                    )[:160],
        ))

    for row in families.get("delivery", [])[:FAMILY_BUDGETS["delivery"]]:
        row = _dict(row)
        fmt = str(row.get("format") or "").strip().lower()
        job_id = str(row.get("job_id") or "")[:48]
        if not fmt and not job_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        evidence_status = {
            "completed": EvidenceStatus.PRESENT,
            "failed": EvidenceStatus.FAILED,
        }.get(status, EvidenceStatus.ABSENT)  # cancelled/queued/running → absent
        out.append(GoalEvidence(
            id=f"export_receipt:delivery:{fmt or job_id}"[:64],
            kind=EvidenceKind.EXPORT_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=evidence_status,
            source="data_delivery:receipt",
            revision="",  # dataset 修订空间 ≠ MapSpec 修订空间 —— 不交叉比陈旧
            ref=job_id,
            detail=(f"delivery: fmt={fmt} rows={row.get('row_count')} "
                    f"sha256={str(row.get('content_sha256') or '')[:16]} "
                    f"missing_ids={len(row.get('scope_missing_ids') or [])}"
                    )[:160],
        ))

    for row in families.get("raster", [])[:FAMILY_BUDGETS["raster"]]:
        row = _dict(row)
        source_id = str(row.get("source_id") or "")[:56]
        if not source_id:
            continue
        layer_ids = [str(x) for x in (row.get("layer_ids") or []) if x]
        out.append(GoalEvidence(
            id=f"raster:{source_id}"[:64],
            kind=EvidenceKind.CAPABILITY_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=EvidenceStatus.PRESENT if layer_ids else EvidenceStatus.ABSENT,
            source="mapspec:numeric_raster",
            revision=str(row.get("data_revision") or "")[:64],
            ref=str(row.get("ref_id") or "")[:64],
            detail=f"layers={','.join(layer_ids[:3])}"[:160],
        ))

    for row in families.get("refresh", [])[:FAMILY_BUDGETS["refresh"]]:
        row = _dict(row)
        proposal_id = str(row.get("proposal_id") or "")[:56]
        if not proposal_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        evidence_status = {
            "adopted": EvidenceStatus.PRESENT,
            "rolled_back": EvidenceStatus.PRESENT,
            "discarded": EvidenceStatus.ABSENT,
            "blocked": EvidenceStatus.FAILED,
        }.get(status, EvidenceStatus.ABSENT)
        out.append(GoalEvidence(
            id=f"refresh:{proposal_id}"[:64],
            kind=EvidenceKind.DATA_REFRESH,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=evidence_status,
            source="project_runtime:refresh_ledger",
            revision=str(row.get("revision_after") or "")[:64],
            detail=(f"action={row.get('action')} "
                    f"map_observation={row.get('map_observation') or 'unknown'}"
                    )[:160],
        ))
    return out


def bounded_capability_receipts(
    bundle: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """bundle → 块持久化/SSE 有界投影（无内容 → None，零漂移）。"""
    if not isinstance(bundle, dict) or bundle.get("disabled"):
        return None
    families = _dict(bundle.get("families"))
    total = sum(len(v) for v in families.values() if isinstance(v, list))
    if total == 0:
        return None
    return {
        "counts": {str(k): len(v) for k, v in list(families.items())[:8]},
        "notes": [str(n)[:120] for n in (bundle.get("notes") or [])[:6]],
    }


def bound_receipt_rows(
    bundle: Optional[Dict[str, Any]],
    family: str,
    ref: str,
) -> List[Dict[str, Any]]:
    """ref（行 bound_ref / 工件 ref）→ 绑定回执行（绑定键 = 源回执自带的
    input_ref/output_ref/ref_id；空 ref 不绑定 —— 不猜）。"""
    if not isinstance(bundle, dict) or not ref:
        return []
    rows = [r for r in _dict(bundle.get("families")).get(str(family), [])
            if isinstance(r, dict)]
    ref_norm = str(ref).strip()
    out = []
    for row in rows:
        for key in ("input_ref", "output_ref", "ref_id", "artifact_ref"):
            value = str(row.get(key) or "").strip()
            if value and (value == ref_norm or value.endswith(ref_norm)
                          or ref_norm.endswith(value)):
                out.append(row)
                break
    return out


__all__ = [
    "ENV_RECEIPT_ADAPTERS",
    "FAMILY_BUDGETS",
    "adapters_enabled",
    "empty_bundle",
    "collect_capability_receipts",
    "receipt_evidence_rows",
    "bounded_capability_receipts",
    "bound_receipt_rows",
    "_latest_per_identity",
]
