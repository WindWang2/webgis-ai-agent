"""Compile receipt —— 编译回执与对账（F12 / ADR-0214 D5）。

receipt 是「IR 编译 → mutation 提交 → MapSpec revision」链路的**机械可查
证据**：内容寻址（同编译同 id）、有界投影（复用 DecisionRecord 纪律）、
可 stale（``receipt_is_stale``：产物指纹 ≠ 当前 spec 指纹即漂移）。

回链：applier 把 receipt 写入 map_state ``_plan_receipts`` 有界环（≤8，
仿 export_lineage receipts / ``_final_display_ack`` 先例）——前端 ACK
推进 cursor 到 ``final_revision`` 即与本次 plan 对上号。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.plan_ir import digest_of
from app.lib.runtime.decision_record import (
    DECISION_KIND_PLAN_COMPILE,
    decision_record,
    reason_code,
)

__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "PLAN_RECEIPTS_KEY",
    "MAX_PLAN_RECEIPTS",
    "AppliedMutationRecord",
    "CompileReceipt",
    "receipt_is_stale",
    "make_plan_compile_decision",
    "bound_receipt_ring",
    "new_receipt_skeleton",
]

RECEIPT_SCHEMA_VERSION = 1
#: map_state 单键（additive `_` 键族；白名单先例 = _final_display_ack）。
PLAN_RECEIPTS_KEY = "_plan_receipts"
MAX_PLAN_RECEIPTS = 8

ReceiptStatus = Literal["pending", "applied", "partial", "superseded",
                        "blocked", "failed", "empty"]  # empty = desired 已满足，零 mutation


class AppliedMutationRecord(BaseModel):
    """单步提交回执（与引擎 MapSpecResult 对齐的有界转录）。"""

    model_config = ConfigDict(frozen=True)

    step: int = Field(ge=1)
    client_mutation_id: str = Field(max_length=128)
    intent: str = Field(max_length=48)
    target: str = Field(default="", max_length=200)
    ok: bool = True
    duplicate: bool = False
    mutation_revision: int = Field(default=0, ge=0)
    error_code: str = Field(default="", max_length=64)
    error_msg: str = Field(default="", max_length=256)


class CompileReceipt(BaseModel):
    """一次 plan 编译+提交的完整回执（可序列化、可 replay、可 stale）。"""

    model_config = ConfigDict(frozen=True)

    schema_version: int = RECEIPT_SCHEMA_VERSION
    receipt_id: str = Field(min_length=1, max_length=64)
    compile_id: str = Field(default="", max_length=48)
    session_id: str = Field(default="", max_length=128)
    ir_id: str = Field(max_length=128)
    ir_fingerprint: str = Field(default="", max_length=96)
    supersedes: str = Field(default="", max_length=128)
    plan_revision: int = Field(default=1, ge=1)
    base_revision: int = Field(ge=0)
    base_fingerprint: str = Field(default="", max_length=96)
    obligations_status: str = Field(default="ok", max_length=16)
    status: ReceiptStatus = "pending"
    applied: List[AppliedMutationRecord] = Field(default_factory=list, max_length=64)
    final_revision: int = Field(default=0, ge=0)
    final_fingerprint: str = Field(default="", max_length=96)
    compile_digest: str = Field(default="", max_length=80)
    reason_codes: List[str] = Field(default_factory=list, max_length=24)
    decision: Dict[str, Any] = Field(default_factory=dict)
    #: 编译期落定的期望显示面（layer_id → visible / 必需组件 id 列表）——
    #: finalize 操作从 receipt 重建期望面，不另设第二存储。
    display_expectations: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default="", max_length=40)
    #: created_at 不参与 receipt_id —— 同编译同 id（重放对齐键）。

    def sort_key(self) -> tuple:
        return (self.base_revision, self.receipt_id)


def receipt_digest_fields(
    *,
    ir_id: str,
    ir_fingerprint: str,
    base_revision: int,
    base_fingerprint: str,
    compile_digest: str,
    plan_revision: int,
) -> Dict[str, Any]:
    """receipt 身份字段（created_at/applied 时序证据**不**入身份）。"""
    return {
        "ir_id": ir_id,
        "ir_fingerprint": ir_fingerprint,
        "base_revision": int(base_revision),
        "base_fingerprint": base_fingerprint,
        "compile_digest": compile_digest,
        "plan_revision": int(plan_revision),
    }


def new_receipt_skeleton(
    compilation: Any,
    *,
    session_id: str = "",
) -> CompileReceipt:
    """编译后、提交前构造 receipt 骨架（status=pending）。"""
    ident = receipt_digest_fields(
        ir_id=compilation.ir_id,
        ir_fingerprint=compilation.ir_fingerprint,
        base_revision=compilation.base_revision,
        base_fingerprint=compilation.base_fingerprint,
        compile_digest=compilation.compile_digest,
        plan_revision=1,
    )
    rid = "mpr-" + digest_of(ident)[:16]
    return CompileReceipt(
        receipt_id=rid,
        compile_id=compilation.compile_id,
        session_id=str(session_id)[:128],
        ir_id=compilation.ir_id,
        ir_fingerprint=compilation.ir_fingerprint,
        supersedes=compilation.supersedes,
        base_revision=compilation.base_revision,
        base_fingerprint=compilation.base_fingerprint,
        obligations_status=compilation.obligations.status,
        status="blocked" if compilation.status == "blocked" else "pending",
        compile_digest=compilation.compile_digest,
        reason_codes=list(compilation.reason_codes)[:24],
        display_expectations=compilation.display_expectations.model_dump(),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def receipt_is_stale(receipt: CompileReceipt, current_fingerprint: str) -> bool:
    """产物指纹 ≠ 当前 spec 指纹 ⇒ 该 plan 的期望终态已不在位。"""
    if not receipt.final_fingerprint:
        return True
    return receipt.final_fingerprint != current_fingerprint


def make_plan_compile_decision(receipt: CompileReceipt) -> Dict[str, Any]:
    """receipt → additive DecisionRecord（plan_compile；有界投影）。"""
    selected = receipt.compile_id or receipt.receipt_id
    codes = [
        reason_code("plan_compile", code, "clean") for code in receipt.reason_codes[:6]
    ]
    return decision_record(
        DECISION_KIND_PLAN_COMPILE,
        selected=selected,
        reason_codes=codes,
        inputs={
            "ir_id": receipt.ir_id,
            "ir_fingerprint": receipt.ir_fingerprint[:40],
            "base_revision": receipt.base_revision,
            "final_revision": receipt.final_revision,
            "status": receipt.status,
            "mutation_count": len(receipt.applied),
        },
        evidence_refs=[r.client_mutation_id for r in receipt.applied[:8]],
        policy_version=f"plan-compiler@{receipt.schema_version}",
    )


def bound_receipt_ring(
    existing: Optional[List[Dict[str, Any]]],
    receipt: CompileReceipt,
) -> List[Dict[str, Any]]:
    """有界环：新 receipt 入尾，超 MAX_PLAN_RECEIPTS 从头驱逐。"""
    ring = list(existing or [])
    ring.append(receipt.model_dump(mode="json"))
    if len(ring) > MAX_PLAN_RECEIPTS:
        del ring[:-MAX_PLAN_RECEIPTS]
    return ring
