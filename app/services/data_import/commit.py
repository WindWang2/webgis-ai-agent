"""G17 导入工作台 —— 逐成员提交（接入既有摄入/持久化链）。

合同（任务书 WP08 + 产品合同 6/8/10）：
- 幂等：成员提交键 (member_key, plan_hash)。既有 completed 收据且输出
  ref 仍可读 → 复用（duplicate 语义），不重复挂层/登记。
- 边界诚实：不把跨 文件系统/SessionStore/registry 的步骤假称 ACID；
  store 成功但登记失败等以收据 error_code 披露并可重试。
- 空间结果 → IngestPipeline（继承 profile/quality/descriptor/dedup/
  license/cancel 语义）；纯表 → ref:table- 前缀 + artifact 登记（既有
  table-artifacts 端点可取回）。
- 导入不暗中修复数据：拒绝行如实计数，非法几何按策略拒绝（不修补）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from app.services.data_import import budgets
from app.services.data_import.interpretation import (
    InterpretResult,
    InterpretationError,
    feature_collection,
    interpret_table,
    interpret_vector,
    table_payload,
)
from app.services.data_import.models import (
    CommitReceipt,
    CrsEvidence,
    ImportBatchState,
    MemberKind,
    MemberRecord,
    SourceFile,
    TableInterpretation,
    VectorInterpretation,
)
from app.services.data_import import staging

logger = logging.getLogger(__name__)

_CONFIRMED_CRS_SOURCES = {"source_metadata", "declared", "rfc7946_default"}
_SOURCE_ID_FIELDS = ("fid", "id", "ogc_fid", "objectid", "gid", "uuid", "code")


class CommitError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)[:300]


def _detect_source_identity(columns: list) -> str:
    lowered = {str(c).strip().lower(): c for c in columns}
    for cand in _SOURCE_ID_FIELDS:
        if cand in lowered:
            return f"source_id_field:{lowered[cand]}"
    return "none"


def _write_diag(
    state: ImportBatchState, member: MemberRecord, result: InterpretResult
) -> Optional[str]:
    """拒绝行明细落 diag/（超内联上限时），返回相对路径。"""
    if len(result.rejects) <= budgets.REJECT_INLINE_MAX:
        return None
    rel = f"{staging.member_safe_name(member.member_key)}-rejected.jsonl"
    path = staging.diag_dir(state) / rel
    rows = result.rejects[: budgets.DIAG_MAX_ROWS]
    lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in rows]
    if len(result.rejects) > len(rows):
        # 截断标记行（评审 P2-6 续：明细产物本身有界）
        lines.append(
            json.dumps(
                {"truncated": True, "shown": len(rows),
                 "reason": f"明细超过上限 {budgets.DIAG_MAX_ROWS} 行"},
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    state.diagnostics[member.member_key] = rel
    return rel


async def _verify_output_readable(ref: str, session_id: str) -> bool:
    """收据复用前验证输出仍可读（合同 8：仅 ref 字符串不算就绪）。"""
    from app.services.session_data import session_data_manager

    data = await session_data_manager.get(session_id, ref)
    return data is not None


async def commit_member(
    state: ImportBatchState,
    member: MemberRecord,
    source: SourceFile,
    interpretation: Any,
    plan_hash: str,
    *,
    cancel_event: Optional[asyncio.Event] = None,
) -> CommitReceipt:
    """单成员提交。异常不外抛（收据承载失败），除取消（上抛给批次层）。"""
    started = time.monotonic()
    from app.services.data_import.models import utc_now_iso

    receipt = CommitReceipt(
        member_key=member.member_key,
        plan_hash=plan_hash,
        status="failed",
        started_at=utc_now_iso(),
    )

    def _check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            from app.lib.cancellation import OperationCancelled

            raise OperationCancelled(f"import batch {state.batch_id} cancelled")

    # 1. 幂等复用：同计划已成功且输出可读
    prior = state.receipts.get(member.member_key)
    if (
        prior is not None
        and prior.status == "completed"
        and prior.plan_hash == plan_hash
        and prior.output_ref
    ):
        try:
            if await _verify_output_readable(prior.output_ref, state.session_id):
                prior.duplicate = True
                return prior
        except Exception:  # noqa: BLE001 — 输出不可读则重新提交
            pass

    # 2. 解释（与 preview 同一引擎；行预算 = COMMIT_ROW_LIMIT）
    src_path = staging.source_path(state, member.source_id)
    extract_root = staging.batch_root(state.batch_id) / "sources" / "extract"
    try:
        if member.kind == MemberKind.VECTOR:
            import pyogrio

            read_path = src_path
            layer = None
            if member.container.value in ("gpkg", "kml"):
                layer = member.container_ref
                if not layer:
                    # 防线：绝不静默回落 GDAL 默认第一层（故障注入反验 1）
                    raise CommitError("CONTAINER_REF_MISSING", member.member_key)
            elif member.container.value == "zipset":
                from app.services.data_import.preview import _zipset_shp_path

                read_path = _zipset_shp_path(member, source, extract_root)
            kwargs: Dict[str, Any] = {}
            if layer is not None:
                kwargs["layer"] = layer
            try:
                gdf = await asyncio.to_thread(pyogrio.read_dataframe, str(read_path), **kwargs)
            except Exception as e:  # noqa: BLE001
                raise CommitError("VECTOR_READ_FAILED", f"{member.display_name}: {e}")
            interp = (
                interpretation
                if isinstance(interpretation, VectorInterpretation)
                else VectorInterpretation()
            )
            crs_hint = (
                "rfc7946_default"
                if member.crs_source == "rfc7946_default"
                else "source_metadata"
            )
            result = await asyncio.to_thread(
                interpret_vector, gdf, interp,
                row_limit=budgets.COMMIT_ROW_LIMIT,
                cancel=_check_cancel,
                crs_source_hint=crs_hint,
            )
        else:
            interp = (
                interpretation
                if isinstance(interpretation, TableInterpretation)
                else TableInterpretation()
            )
            sheet = member.container_ref if member.container.value == "sheet" else None
            result = await asyncio.to_thread(
                interpret_table, src_path, interp,
                sheet=sheet,
                hints=member.hints,
                row_limit=budgets.COMMIT_ROW_LIMIT,
                cancel=_check_cancel,
            )
    except InterpretationError as e:
        receipt.error_code = e.code
        receipt.error_message = e.detail
        receipt.finished_at = utc_now_iso()
        receipt.elapsed_ms = int((time.monotonic() - started) * 1000)
        return receipt
    except CommitError as e:
        receipt.error_code = e.code
        receipt.error_message = e.detail
        receipt.finished_at = utc_now_iso()
        receipt.elapsed_ms = int((time.monotonic() - started) * 1000)
        return receipt

    if member.kind == MemberKind.TABLE and result.truncated:
        # 评审 P1-2：表格路径与矢量一致 —— 超 COMMIT_ROW_LIMIT 诚实失败，
        # 不静默截断（预览用小限额是有意的，提交用全量预算）。
        receipt.input_rows = result.rows_scanned
        receipt.rejected_rows = len(result.rejects)
        receipt.error_code = "ROW_BUDGET_EXCEEDED"
        receipt.error_message = (
            f"表格行数超过提交预算 {budgets.COMMIT_ROW_LIMIT}，"
            "已拒绝（不静默截断）；请分批或缩减数据"
        )
        receipt.finished_at = utc_now_iso()
        receipt.elapsed_ms = int((time.monotonic() - started) * 1000)
        return receipt

    receipt.input_rows = result.rows_scanned
    receipt.rejected_rows = len(result.rejects)
    receipt.geometry_type = result.geometry_family or None
    receipt.crs_evidence = result.crs_evidence
    receipt.source_identity = _detect_source_identity(result.columns)
    diag_rel = _write_diag(state, member, result)
    receipt.diagnostics_ref = diag_rel

    # 3. 空间 vs 纯表落链
    is_spatial = member.kind == MemberKind.VECTOR or result.effective_mode in ("points", "wkt")
    ev = result.crs_evidence or CrsEvidence(crs_source="unknown")
    crs_confirmed = ev.crs_source in _CONFIRMED_CRS_SOURCES and bool(ev.crs)

    if is_spatial and not crs_confirmed:
        receipt.error_code = "CRS_UNCONFIRMED"
        receipt.error_message = (
            "源 CRS 未确认（无元数据、无声明）。请先声明源 CRS，或将该成员按纯表导入。"
        )
        receipt.finished_at = utc_now_iso()
        receipt.elapsed_ms = int((time.monotonic() - started) * 1000)
        return receipt

    try:
        if is_spatial:
            fc, transformed = await asyncio.to_thread(
                feature_collection, result, to_wgs84=True
            )
            if transformed and ev:
                ev.transform_target = "EPSG:4326"
            payload_bytes = len(json.dumps(fc, ensure_ascii=False))
            if payload_bytes > budgets.MAX_MEMBER_OUTPUT_BYTES:
                raise CommitError(
                    "OUTPUT_BUDGET_EXCEEDED",
                    f"成员输出 {payload_bytes} 字节超过预算 {budgets.MAX_MEMBER_OUTPUT_BYTES}",
                )
            from app.services.data_ingest.pipeline import get_ingest_pipeline

            # 输出 CRS 诚实（评审 P1-1）：重投影后坐标已是 EPSG:4326 —— 摄入
            # 收到的 crs 必须是 OUTPUT CRS；源 CRS 由收据 crs_evidence 承载
            # （与旧上传 data_parser.py:357-365 → upload.py:363 契约一致）。
            confirmed_crs = "EPSG:4326" if transformed else (ev.crs if crs_confirmed else "")
            ingest = await get_ingest_pipeline().ingest(
                state.session_id,
                fc,
                name=member.display_name,
                crs=confirmed_crs,
                source_type="import_workbench",
                prefix="upload",
                license=state.license or None,
                attribution=state.attribution or None,
                cancel_event=cancel_event,
            )
            if not ingest.ok or not ingest.ref_id:
                raise CommitError(
                    ingest.error_code or "INGEST_FAILED", ingest.error or ""
                )
            receipt.output_kind = "feature_collection"
            receipt.output_ref = ingest.ref_id
            receipt.duplicate = bool(ingest.duplicate)
            receipt.quality_status = (
                (ingest.quality_summary or {}).get("status")
                if isinstance(ingest.quality_summary, dict)
                else None
            )
            # durable 恢复证据：成员 FC 落 outputs/（预算内）
            try:
                out_path = staging.outputs_dir(state) / (
                    staging.member_safe_name(member.member_key) + ".geojson"
                )
                out_path.write_text(
                    json.dumps(fc, ensure_ascii=False), encoding="utf-8"
                )
            except OSError as e:  # noqa: BLE001 — outputs 副本失败不阻断
                state.notes.append(f"{member.display_name}: outputs 副本写入失败（{e}）")
        else:
            from app.services.session_data import session_data_manager

            payload = table_payload(result)
            ref = await session_data_manager.store(
                state.session_id, payload, prefix="table"
            )
            if not ref or not str(ref).startswith("ref:"):
                raise CommitError("TABLE_STORE_FAILED", f"store 返回 {ref!r}")
            receipt.output_kind = "table"
            receipt.output_ref = str(ref)
            try:
                out_path = staging.outputs_dir(state) / (
                    staging.member_safe_name(member.member_key) + ".table.json"
                )
                out_path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:  # noqa: BLE001
                pass
            # 登记为增值记录（失败不阻断，收据披露）
            try:
                from app.services.artifact_registry import register_artifact

                await register_artifact(
                    state.session_id,
                    artifact_id=str(ref),
                    artifact_type="table",
                    producer_tool="import_workbench",
                    metadata={
                        "source_type": "import_workbench",
                        "display_name": member.display_name,
                        "batch_id": state.batch_id,
                        "member_key": member.member_key,
                        "plan_hash": plan_hash,
                        "row_count": len(result.records),
                        "rejected_rows": len(result.rejects),
                        "columns": result.columns[: budgets.MAX_COLUMNS_SHOWN],
                    },
                )
            except Exception as e:  # noqa: BLE001 — 登记失败披露可重试
                state.notes.append(
                    f"{member.display_name}: 表格 artifact 登记失败（{e}）；ref 已可用"
                )
        receipt.output_rows = len(result.records)
        receipt.output_name = member.display_name
        receipt.status = "completed"
    except CommitError as e:
        receipt.error_code = e.code
        receipt.error_message = e.detail
    except Exception as e:  # noqa: BLE001 — 未预期失败：收据披露，不假称成功
        logger.exception("import commit failed for %s", member.member_key)
        receipt.error_code = "COMMIT_FAILED"
        receipt.error_message = str(e)[:300]
    receipt.finished_at = utc_now_iso()
    receipt.elapsed_ms = int((time.monotonic() - started) * 1000)
    return receipt
