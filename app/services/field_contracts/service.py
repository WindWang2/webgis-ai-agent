"""G44 owner 编排面 —— REST 路由与 Pi 工具共用（不绕过本面直接触内核）。

生命周期：draft（可修订，digest CAS）→ assess（完整冻结快照评估，durable job）
→ activate（编辑锁内复核指纹 + DB CAS；零违反完整收据才可激活）→ retired。
active/superseded/retired 行内容不可变 —— 修订生成同 contract_key 新行。

并发纪律（WP05/F11/F13/F14）：激活/退役与受影响数据写入共享
``feature_edit.executor._edit_lock``（分布式会话锁 + 进程锁同款次序），锁内
冷读载荷复核「评估 verified 指纹 == 当前指纹」后 DB CAS —— 跨进程互斥由
session_lock_registry 兑现；DB 部分唯一 active 索引兜底。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.field_contracts.contracts import (
    ACTIVATABLE_ASSESSMENT_STATUS,
    FINDINGS_PAGE_DEFAULT,
    FINDINGS_PAGE_MAX,
    MAX_ASSESSABLE_FEATURES,
)
from app.services.field_contracts.store import field_contract_store
from app.services.field_contracts.validator import (
    ContractDocumentError,
    assess_feature_collection,
    contract_digest,
    normalize_document,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "field_contract_assess"
_MAX_FINDINGS_FILE_BYTES = 8 * 1024 * 1024
#: 执行中的评估（assessment_id -> asyncio.Task）；取消走 durable job 通道
_RUNNERS: Dict[str, asyncio.Task] = {}


class FieldContractError(RuntimeError):
    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _findings_dir(session_id: str) -> Path:
    from app.core.config import settings

    safe = "".join(ch for ch in str(session_id) if ch not in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', '\x00'))
    return Path(settings.DATA_DIR).resolve() / "field-contracts" / safe


def _write_findings_atomic(assessment_id: str, session_id: str, payload: Dict[str, Any]) -> Optional[str]:
    rdir = _findings_dir(session_id)
    rdir.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(dir=str(rdir), prefix=".findings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        final = rdir / f"{assessment_id}.json"
        os.replace(tmp, final)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return str(final)


def _read_findings(session_id: str, assessment_id: str) -> Optional[Dict[str, Any]]:
    path = _findings_dir(session_id) / f"{assessment_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _delete_assessment_row(assessment_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.field_contracts import FieldContractAssessment

    with SessionLocal() as db:
        row = db.get(FieldContractAssessment, assessment_id)
        if row is not None:
            db.delete(row)
            db.commit()


def _digest_payload(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   default=str).encode("utf-8")
    ).hexdigest()


async def _load_dataset(session_id: str, ref_id: str) -> Dict[str, Any]:
    """载荷 + descriptor + 资格（editable ref 才可绑定合同；F15）。"""
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.datasets import qualify_dataset
    from app.services.feature_edit.plan import extract_fc
    from app.services.session_data import session_data_manager

    qualification = await qualify_dataset(session_id, ref_id)
    if not qualification.get("editable"):
        raise FieldContractError(
            "dataset_not_eligible",
            "contracts bind to editable vector copies only",
            {"reason": qualification.get("reason"), "ref_id": ref_id},
        )
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        raise FieldContractError("dataset_not_found", f"ref not resolvable: {ref_id[:48]}")
    fc = extract_fc(payload)
    if fc is None:
        raise FieldContractError("dataset_not_editable", "payload is not a geojson FeatureCollection")
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return {
        "payload": payload,
        "fc": fc,
        "fingerprint": compute_payload_fingerprint(payload),
        "content_revision": int(descriptor.get("content_revision") or 0),
        "qualification": qualification,
    }


# ─── 草稿 / 修订 / 查询 ───────────────────────────────────────────────────


async def create_contract(
    session_id: str,
    *,
    ref_id: str,
    document: Dict[str, Any],
    actor: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    dataset = await _load_dataset(session_id, ref_id)
    try:
        normalized = normalize_document(document)
    except ContractDocumentError as exc:
        raise FieldContractError("invalid_document", "contract document invalid",
                                 {"errors": exc.errors[:12]}) from exc
    digest = contract_digest(normalized)
    latest = await asyncio.to_thread(
        field_contract_store.latest_for_binding, session_id, ref_id
    )
    if latest is not None and latest.get("status") == "draft":
        raise FieldContractError(
            "draft_exists",
            "a draft already exists for this dataset; revise it instead",
            {"contract_id": latest.get("id"), "revision": latest.get("revision")},
        )
    contract_key = (latest or {}).get("contract_key") or uuid.uuid4().hex
    revision = int((latest or {}).get("revision") or 0) + 1
    row = await asyncio.to_thread(
        field_contract_store.create_contract,
        contract_key=contract_key,
        session_id=session_id,
        ref_id=ref_id,
        project_id=project_id,
        created_by=actor,
        title=str(document.get("title") or ""),
        rules=normalized.document,
        normalized_digest=digest,
        revision=revision,
        status="draft",
        notes=document.get("notes"),
    )
    row["binding"] = {
        "ref_id": ref_id,
        "content_revision_at_draft": dataset["content_revision"],
        "data_digest_at_draft": dataset["fingerprint"],
    }
    return _contract_view(row, dataset["qualification"])


async def revise_contract(
    session_id: str,
    contract_id: str,
    *,
    expected_revision: int,
    document: Dict[str, Any],
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("contract_not_found", "contract not found in this session")
    if row.get("status") != "draft":
        raise FieldContractError(
            "contract_not_revisionable",
            "only drafts can be revised; create a new draft revision instead",
            {"status": row.get("status")},
        )
    if int(row.get("revision") or 0) != expected_revision:
        raise FieldContractError(
            "revision_conflict",
            "contract was updated concurrently; reload and retry",
            {"expected": expected_revision, "current": row.get("revision")},
        )
    try:
        normalized = normalize_document(document)
    except ContractDocumentError as exc:
        raise FieldContractError("invalid_document", "contract document invalid",
                                 {"errors": exc.errors[:12]}) from exc
    from app.core.database import SessionLocal
    from app.models.field_contracts import FieldContract

    digest = contract_digest(normalized)
    def _update() -> Optional[Dict[str, Any]]:
        with SessionLocal() as db:
            target = db.get(FieldContract, contract_id)
            if target is None or target.status != "draft":
                return None
            if target.normalized_digest == digest:
                return field_contract_store.get_contract(contract_id)
            target.rules = normalized.document
            target.normalized_digest = digest
            target.title = str(document.get("title") or "")
            target.notes = document.get("notes")
            target.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(target)
            return field_contract_store.get_contract(contract_id)
    # R1-P0：草稿修订与激活/数据写共用编辑锁 —— 激活锁内 digest 复核因此
    # 闭环（Pi webgis_field_contract_draft 的修订路径不得与激活交错）。
    from app.services.feature_edit import executor as fe_executor

    async with fe_executor._edit_lock(session_id):
        updated = await asyncio.to_thread(_update)
    if updated is None:
        raise FieldContractError("contract_not_revisionable", "draft no longer revisionable")
    qualification = (await _load_dataset(session_id, str(row.get("ref_id"))))["qualification"]
    return _contract_view(updated, qualification)


async def get_contract(session_id: str, contract_id: str) -> Dict[str, Any]:
    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("contract_not_found", "contract not found in this session")
    try:
        qual = await asyncio.to_thread(
            _qualification_lite, session_id, str(row.get("ref_id"))
        )
    except Exception:  # noqa: BLE001 — 视图面失败不阻断读取
        qual = {}
    return _contract_view(row, qual)


async def list_contracts(session_id: str, ref_id: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = await asyncio.to_thread(field_contract_store.list_contracts, session_id, ref_id)
    return [_contract_view(r, {}) for r in rows]


def _qualification_lite(session_id: str, ref_id: str) -> Dict[str, Any]:
    """同步薄资格（列表视图用；完整资格在评估/创建路径）。"""
    return {"ref_id": ref_id}


def _contract_view(row: Dict[str, Any], qualification: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contract_id": row["id"],
        "contract_key": row.get("contract_key"),
        "session_id": row.get("session_id"),
        "ref_id": row.get("ref_id"),
        "status": row.get("status"),
        "revision": row.get("revision"),
        "title": row.get("title"),
        "rules": row.get("rules"),
        "normalized_digest": row.get("normalized_digest"),
        "binding": row.get("binding") or {},
        "activation": row.get("activation"),
        "notes": row.get("notes"),
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "dataset": qualification,
    }


# ─── 完整评估（durable job）──────────────────────────────────────────────


async def start_assessment(
    session_id: str,
    contract_id: str,
    *,
    actor: Optional[str] = None,
    background: bool = True,
) -> Dict[str, Any]:
    """幂等启动完整评估：冻结快照 + 逐行 + 唯一性 + 收据落账。"""
    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("contract_not_found", "contract not found in this session")
    if row.get("status") not in ("draft", "active"):
        raise FieldContractError(
            "contract_not_assessable",
            "only draft or active contracts can be assessed",
            {"status": row.get("status")},
        )
    dataset = await _load_dataset(session_id, str(row.get("ref_id")))
    fc = dataset["fc"]
    n_features = len(fc.get("features") or [])
    if n_features > MAX_ASSESSABLE_FEATURES:
        raise FieldContractError(
            "dataset_too_large_to_assess",
            "dataset exceeds the assessable size gate; contract activation requires "
            "an editable copy within the bound",
            {"features": n_features, "limit": MAX_ASSESSABLE_FEATURES},
        )
    idem = "g44:assess:{sid}:{cid}:{rev}:{fp}".format(
        sid=session_id[:24], cid=contract_id[:24],
        rev=row.get("revision"), fp=dataset["fingerprint"][:16],
    )
    assessment = await asyncio.to_thread(
        field_contract_store.create_assessment,
        contract_id=contract_id,
        contract_revision=int(row.get("revision") or 0),
        session_id=session_id,
        ref_id=str(row.get("ref_id")),
        status="queued",
        verified={
            "data_digest": dataset["fingerprint"],
            "content_revision": dataset["content_revision"],
            "features_total": n_features,
            "rules_digest": row.get("normalized_digest"),
        },
        counts={},
        created_by=actor,
    )
    assessment_id = assessment["id"]

    from app.services.data_operations.runner import submit_async_job

    job_id, _status, reused = submit_async_job(
        task_type=TASK_TYPE,
        kind=TASK_TYPE,
        display_name=f"字段合同评估 · {str(row.get('title') or contract_id)[:24]}",
        params={
            "session_id": session_id,
            "contract_id": contract_id,
            "assessment_id": assessment_id,
        },
        idempotency_key=idem,
        owner_id=actor,
        session_id=session_id,
    )
    if reused:
        # 幂等命中既有 job：收敛到该 job 的既有收据，丢弃本次占位行
        prior = [
            a for a in await asyncio.to_thread(
                field_contract_store.list_assessments, contract_id, 50
            ) if a.get("job_id") == str(job_id) and a.get("id") != assessment_id
        ]
        if prior:
            await asyncio.to_thread(
                _delete_assessment_row, assessment_id
            )
            return {
                "assessment": prior[0],
                "job_id": job_id,
                "poll": f"/api/v1/tasks/jobs/{job_id}",
                "idempotent_reuse": True,
            }
    await asyncio.to_thread(
        field_contract_store.update_assessment, assessment_id, job_id=str(job_id)
    )
    if background:
        task = asyncio.get_running_loop().create_task(
            _run_assessment_job(job_id, assessment_id, session_id, contract_id),
            name=f"field-contract-assess:{assessment_id}",
        )
        _RUNNERS[assessment_id] = task
        task.add_done_callback(lambda t: _RUNNERS.pop(assessment_id, None))
    else:
        await _run_assessment_job(job_id, assessment_id, session_id, contract_id)
    latest = await asyncio.to_thread(field_contract_store.get_assessment, assessment_id)
    return {
        "assessment": latest or assessment,
        "job_id": job_id,
        "poll": f"/api/v1/tasks/jobs/{job_id}",
        "idempotent_reuse": reused,
    }


async def _run_assessment_job(job_id: int, assessment_id: str, session_id: str, contract_id: str) -> None:
    from app.services.data_operations.runner import run_async_job

    async def body(handle: Any) -> Dict[str, Any]:
        return await _execute_assessment(handle, assessment_id, session_id, contract_id)

    try:
        await run_async_job(job_id, body)
    except Exception as exc:  # noqa: BLE001 — 区分幂等复用与真崩溃
        name = type(exc).__name__
        if "AlreadyFinished" in name:
            # R1-P3：submit_async_job 幂等命中既有终态 job 而收据行缺失/
            # 被裁剪 —— job 本身已成功执行；占位行按 job 终态收敛（非
            # "crashed" 误报），保持「failed 不可激活」不变式。
            await asyncio.to_thread(
                field_contract_store.update_assessment, assessment_id,
                status="completed",
                error="converged to reused terminal job (receipt row absent)",
            )
            return
        logger.exception("[field-contracts] assessment job crashed: %s", assessment_id)
        await asyncio.to_thread(
            field_contract_store.update_assessment, assessment_id,
            status="failed", error=f"job runner crashed: {name}",
        )


async def _execute_assessment(
    handle: Any, assessment_id: str, session_id: str, contract_id: str
) -> Dict[str, Any]:
    """评估体：锁内冻结快照 → 逐行/唯一性 → 原子收据（partial 永不激活）。"""
    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None:
        await asyncio.to_thread(
            field_contract_store.update_assessment, assessment_id,
            status="failed", error="contract row disappeared",
        )
        return {"status": "failed"}
    try:
        normalized = normalize_document(row.get("rules") or {})
    except ContractDocumentError as exc:
        await asyncio.to_thread(
            field_contract_store.update_assessment, assessment_id,
            status="failed", error=f"contract unparsable: {exc.errors[:3]}",
        )
        return {"status": "failed"}
    dataset = await _load_dataset(session_id, str(row.get("ref_id")))
    fc = dataset["fc"]
    await asyncio.to_thread(
        field_contract_store.update_assessment, assessment_id, status="running",
        verified={
            "data_digest": dataset["fingerprint"],
            "content_revision": dataset["content_revision"],
            "features_total": len(fc.get("features") or []),
            "rules_digest": row.get("normalized_digest"),
        },
    )

    class _CancelProbe:
        """行间取消探针（should_continue 协议；handle.cancelled → StopIteration）。

        取消/崩溃期间绝不产生可激活证明：收据落 incomplete，verified 指纹仍在
        （激活只接受 completed + 零违反，F31）。
        """

        def __iter__(self) -> "_CancelProbe":
            self._i = 0
            return self

        def __next__(self) -> None:
            self._i += 1
            if self._i % 200 == 0 and handle.cancelled:
                raise StopIteration

    outcome = assess_feature_collection(fc, normalized, should_continue=_CancelProbe())
    final_status = "completed" if outcome.complete else "incomplete"
    stop = outcome.stop_reason
    file_payload = {
        "version": 1,
        "assessment_id": assessment_id,
        "contract_id": contract_id,
        "contract_revision": row.get("revision"),
        "rules_digest": row.get("normalized_digest"),
        "verified": {
            "data_digest": dataset["fingerprint"],
            "content_revision": dataset["content_revision"],
            "features_total": outcome.total_features,
            "rules_digest": row.get("normalized_digest"),
        },
        "summary": outcome.summary(),
        "findings": outcome.violations_materialized,
    }
    findings_path: Optional[str] = None
    findings_digest: Optional[str] = None
    try:
        findings_path = _write_findings_atomic(assessment_id, session_id, file_payload)
        findings_digest = _digest_payload(file_payload["summary"])
    except OSError as exc:
        logger.error("[field-contracts] findings file write failed: %s", exc)
        final_status = "incomplete"
        stop = "findings_persist_failed"
    await asyncio.to_thread(
        field_contract_store.update_assessment, assessment_id,
        status=final_status,
        counts={
            **outcome.summary(),
            "materialized_findings": len(outcome.violations_materialized),
        },
        findings_path=findings_path,
        findings_digest=findings_digest,
        error=stop,
    )
    return {"status": final_status, "violations": outcome.violations_total}


async def get_assessment(session_id: str, assessment_id: str) -> Dict[str, Any]:
    row = await asyncio.to_thread(field_contract_store.get_assessment, assessment_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("assessment_not_found", "assessment not found in this session")
    return row


async def assessment_findings_page(
    session_id: str,
    assessment_id: str,
    *,
    cursor: Optional[str] = None,
    limit: int = FINDINGS_PAGE_DEFAULT,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """违规分页（只分页展示；总数/后续页可追溯，F17）。"""
    row = await asyncio.to_thread(field_contract_store.get_assessment, assessment_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("assessment_not_found", "assessment not found in this session")
    payload = await asyncio.to_thread(_read_findings, session_id, assessment_id)
    if payload is None:
        return {
            "findings": [],
            "total_violations": (row.get("counts") or {}).get("violations_total", 0),
            "filtered_total": 0,
            "next_cursor": None,
            "note": "findings file unavailable (incomplete or pruned assessment)",
        }
    findings = payload.get("findings") or []
    if code:
        findings = [f for f in findings if f.get("code") == code]
    limit = max(1, min(limit, FINDINGS_PAGE_MAX))
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            raise FieldContractError("invalid_cursor", "cursor must be an integer offset")
    page = findings[offset:offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(findings) else None
    summary = payload.get("summary") or {}
    return {
        "findings": page,
        "total_violations": summary.get("violations_total", len(payload.get("findings") or [])),
        "filtered_total": len(findings),
        "overflow_note": (
            f"{summary.get('overflow_count', 0)} violations beyond the materialization "
            "limit are counted but not pageable"
        ) if summary.get("overflow_count") else None,
        "next_cursor": next_cursor,
        "sampled": bool(row.get("sampled")),
    }


# ─── 激活 / 退役（与数据写共享竞争边界）─────────────────────────────────


async def activate_contract(
    session_id: str, contract_id: str, *, actor: Optional[str] = None
) -> Dict[str, Any]:
    """owner 激活：完整零违反收据 + 编辑锁内指纹复核 + DB CAS。"""
    from app.services.feature_edit import executor as fe_executor

    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("contract_not_found", "contract not found in this session")
    if row.get("status") == "active":
        return {"activated": True, "already_active": True, "contract": _contract_view(row, {})}
    if row.get("status") != "draft":
        raise FieldContractError(
            "contract_not_activatable",
            "only drafts can be activated",
            {"status": row.get("status")},
        )
    assessments = await asyncio.to_thread(
        field_contract_store.list_assessments, contract_id, limit=50
    )
    usable = [
        a for a in assessments
        if a.get("status") == ACTIVATABLE_ASSESSMENT_STATUS
        and not a.get("sampled")
        and (a.get("verified") or {}).get("rules_digest") == row.get("normalized_digest")
        and int((a.get("counts") or {}).get("violations_total", -1)) == 0
        and (a.get("counts") or {}).get("complete") is True
    ]
    if not usable:
        raise FieldContractError(
            "no_activatable_assessment",
            "activation requires a completed full assessment with zero violations "
            "against the current contract content",
            {"assessments": [
                {"id": a.get("id"), "status": a.get("status"),
                 "violations": (a.get("counts") or {}).get("violations_total")}
                for a in assessments[:6]
            ]},
        )
    assessment = usable[0]
    ref_id = str(row.get("ref_id"))
    verified = assessment.get("verified") or {}

    async with fe_executor._edit_lock(session_id):
        # R1-P0：锁内冷读合同行复核 digest/revision —— 锁外选出的零违反收据
        # 只对**当时**的内容有效；草稿在选收据后被修订（含 Pi 工具路径）时，
        # 行内容 ≠ 收据评估内容，激活必须拒绝（防未审计规则上膛）。
        fresh_row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
        if (
            fresh_row is None
            or fresh_row.get("status") != "draft"
            or str(fresh_row.get("normalized_digest")) != str(row.get("normalized_digest"))
            or int(fresh_row.get("revision") or 0) != int(row.get("revision") or 0)
        ):
            raise FieldContractError(
                "activation_race",
                "contract was revised after the usable assessment was selected; "
                "re-assess the current content and retry",
                {"assessment_id": assessment.get("id")},
            )
        from app.services.session_data import session_data_manager

        payload = await session_data_manager.get(session_id, ref_id)
        if payload is None:
            raise FieldContractError("dataset_not_found", "ref no longer resolvable")
        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
        current_revision = int(descriptor.get("content_revision") or 0)
        from app.services.data_ingest.pipeline import compute_payload_fingerprint

        current_fp = compute_payload_fingerprint(payload)
        if (
            current_fp != verified.get("data_digest")
            or current_revision != int(verified.get("content_revision") or -1)
        ):
            raise FieldContractError(
                "assessment_stale",
                "data changed after the full assessment; re-assess before activating "
                "(F13)",  # noqa: E501 — 语义引用
                {
                    "assessment_id": assessment.get("id"),
                    "assessed_digest": str(verified.get("data_digest"))[:16],
                    "current_digest": current_fp[:16],
                    "assessed_revision": verified.get("content_revision"),
                    "current_revision": current_revision,
                },
            )
        await asyncio.to_thread(
            field_contract_store.retire_other_active, session_id, ref_id, contract_id
        )
        activation = {
            "actor": actor,
            "activated_at": _now_iso(),
            "assessment_id": assessment.get("id"),
            "data_digest": verified.get("data_digest"),
            "content_revision": current_revision,
            "rules_digest": row.get("normalized_digest"),
            "violations": 0,
            "completeness": "complete",
        }
        updated = await asyncio.to_thread(
            field_contract_store.transition_status,
            contract_id,
            from_status=("draft",),
            to_status="active",
            expected_digest=str(row.get("normalized_digest")),
            extra_activation=activation,
            extra_binding={"activation": activation},
        )
        if updated is None:
            raise FieldContractError(
                "activation_race",
                "contract state changed concurrently; reload and retry",
            )
    return {"activated": True, "contract": _contract_view(updated, {})}


async def retire_contract(
    session_id: str, contract_id: str, *, actor: Optional[str] = None
) -> Dict[str, Any]:
    row = await asyncio.to_thread(field_contract_store.get_contract, contract_id)
    if row is None or row.get("session_id") != session_id:
        raise FieldContractError("contract_not_found", "contract not found in this session")
    if row.get("status") != "active":
        raise FieldContractError("contract_not_active", "only active contracts can be retired")
    activation = dict(row.get("activation") or {})
    activation["retired_by"] = actor
    activation["retired_at"] = _now_iso()
    updated = await asyncio.to_thread(
        field_contract_store.transition_status,
        contract_id,
        from_status=("active",),
        to_status="retired",
        extra_activation=activation,
    )
    if updated is None:
        raise FieldContractError("retire_race", "contract state changed concurrently")
    return {"retired": True, "contract": _contract_view(updated, {})}


# ─── 观测 × 声明对照（工作台/Pi inspect）─────────────────────────────────


async def observe_fields(session_id: str, ref_id: str) -> Dict[str, Any]:
    """观测画像（attr_ops 既有扫描）+ active/草稿声明对照（只读，不改其语义）。"""
    from app.services.feature_edit.attr_ops import profile_fields
    from app.services.feature_edit.plan import extract_fc
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        raise FieldContractError("dataset_not_found", f"ref not resolvable: {ref_id[:48]}")
    fc = extract_fc(payload)
    if fc is None:
        raise FieldContractError("dataset_not_editable", "payload is not a geojson FeatureCollection")
    profiles = profile_fields(fc)
    latest = await asyncio.to_thread(
        field_contract_store.latest_for_binding, session_id, ref_id
    )
    declared: List[Dict[str, Any]] = []
    if latest and latest.get("rules"):
        try:
            normalized = normalize_document(latest.get("rules") or {})
            for fr in normalized.fields:
                declared.append({
                    "field": fr.field, "type": fr.type, "required": fr.required,
                    "nullable": fr.nullable, "enum": list(fr.enum) if fr.enum else None,
                    "unit": fr.unit, "label": fr.label,
                })
        except ContractDocumentError:
            declared = []
    observed_enum_note = (
        "observed vocabulary is evidence only — declaring it as an enum must state "
        "coverage; activation enforces the declared domain, not the observed one"
    )
    return {
        "ref_id": ref_id,
        "feature_count": len(fc.get("features") or []),
        "observed": profiles,
        "declared": declared,
        "declared_source": {
            "contract_id": (latest or {}).get("id"),
            "status": (latest or {}).get("status"),
            "revision": (latest or {}).get("revision"),
        } if latest else None,
        "note": observed_enum_note,
    }
