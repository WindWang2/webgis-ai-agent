"""turn 时刻的附件解析与送模台账（G36 / ADR-0291，WP06/WP09）。

- **解析**：ChatRequest 携带的只是 evidence refs（≤4）；在 bridge 持有
  turn 身份后由本模块做：会话归属复核（V16）→ tombstone 复核（V28）→
  blob 受权读取 + sha 校验 → base64 → 预算裁决（逐图报告，不静默裁，
  V09）→ 原生 ``ImageContent`` 列表 + 逐附件回执。
- **送模台账**（V33）：同一 vendor 会话（transcript）里送过的 pixels 会
  参与后续每一次请求 —— 删除/撤权**不能只挡 ref resolver**。台账按
  (session, vendor_generation) 记录已送 sha；新 turn / 排队附图交付前
  复验全部在场且未撤销；发现失效 → 调用方强制 vendor 换代（new_session
  + 台账换代）或阻断请求。
- 能力门（WP08）：``allowed=False`` 时全部拒绝并给 typed reason，文本
  会话不受影响（V10）。
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.services.visual_context import refs
from app.services.visual_context.decoder import MAX_DERIVED_BYTES
from app.services.visual_context.store import load_evidence_index

logger = logging.getLogger(__name__)

#: 每 turn 附件上限（与 ChatRequest 校验一致）。
MAX_ATTACHMENTS_PER_TURN = 4
#: 每 turn 送模 base64 总预算。
MAX_TURN_BASE64_BYTES = 4 * 1024 * 1024
#: 单图 base64 预算（派生字节 1.5MiB × 4/3 ≈ 2MiB）。
MAX_IMAGE_BASE64_BYTES = 2 * 1024 * 1024

_SENT = "sent"
_REFUSED = "refused"


@dataclass
class AttachmentReceipt:
    """单附件回执（SSE 披露 + Message.attachments 持久化面）。"""

    evidence_id: str
    source_type: str = ""
    status: str = _REFUSED
    reason: str = ""
    derived_sha256: str = ""
    width: int = 0
    height: int = 0
    mapspec_revision: Optional[int] = None
    viewport_hash: str = ""
    observations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "status": self.status,
            "reason": self.reason,
            "derived_sha256": self.derived_sha256,
            "width": self.width,
            "height": self.height,
            "mapspec_revision": self.mapspec_revision,
            "viewport_hash": self.viewport_hash,
            "observations": list(self.observations)[:8],
        }


@dataclass
class ResolveOutcome:
    """一次 turn 的解析结果。"""

    images: List[Dict[str, Any]] = field(default_factory=list)   # 原生 ImageContent
    receipts: List[AttachmentReceipt] = field(default_factory=list)
    sent_derived_shas: List[str] = field(default_factory=list)

    @property
    def any_sent(self) -> bool:
        return bool(self.images)

    @property
    def refusal_summary(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.receipts if r.status == _REFUSED]


def _image_content(data: bytes, mime: str) -> Dict[str, Any]:
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"),
            "mimeType": mime}


async def resolve_attachments(
    session_id: str,
    attachments: Optional[Sequence[Any]],
    *,
    images_allowed: bool,
    allowed_reason: str = "",
    max_bytes: int = MAX_DERIVED_BYTES,
) -> ResolveOutcome:
    """解析附件 → 有界 ImageContent[] + 逐附件回执。

    ``attachments`` 元素须有 ``evidence_id``（ChatAttachmentRef / dict）。
    拒绝逐图给 reason（V09），绝不静默丢图。
    """
    outcome = ResolveOutcome()
    if not attachments:
        return outcome
    items = [a for a in attachments if a]
    records = {r.evidence_id: r for r in await load_evidence_index(session_id)} \
        if session_id else {}
    total_b64 = 0
    for idx, item in enumerate(items):
        evidence_id = str(getattr(item, "evidence_id", None)
                          or (item.get("evidence_id") if isinstance(item, dict) else "")
                          or "")[:40]
        receipt = AttachmentReceipt(evidence_id=evidence_id)
        if idx >= MAX_ATTACHMENTS_PER_TURN:
            # V09：超出每 turn 上限的附件也逐条回执 —— 绕过 ChatRequest 校验
            # 的调用方不能指望这里静默丢图。
            receipt.reason = "attachment_limit_exceeded"
            outcome.receipts.append(receipt)
            continue
        rec = records.get(evidence_id)
        if rec is not None:
            receipt.source_type = rec.source_type
            receipt.mapspec_revision = rec.mapspec_revision
            receipt.viewport_hash = rec.viewport_hash
            receipt.observations = list(rec.observations)
        if not evidence_id.startswith("vev-"):
            receipt.reason = "invalid_evidence_ref"
        elif not images_allowed:
            receipt.reason = allowed_reason or "model_not_configured_for_vision"
        elif rec is None:
            receipt.reason = "evidence_not_found_or_foreign_session"
        elif rec.state == "revoked":
            receipt.reason = "evidence_revoked"
        else:
            data = await __import__("asyncio").to_thread(
                refs.read_evidence_bytes, rec.derived_sha256, session_id, max_bytes)
            if data is None:
                receipt.reason = "evidence_blob_unavailable"
            else:
                b64_len = (len(data) * 4 + 2) // 3
                if b64_len > MAX_IMAGE_BASE64_BYTES:
                    receipt.reason = "image_exceeds_encoding_budget"
                elif total_b64 + b64_len > MAX_TURN_BASE64_BYTES:
                    receipt.reason = "turn_image_budget_exceeded"
                else:
                    receipt.status = _SENT
                    receipt.derived_sha256 = rec.derived_sha256
                    receipt.width = rec.width
                    receipt.height = rec.height
                    outcome.images.append(_image_content(data, rec.mime or "image/png"))
                    outcome.sent_derived_shas.append(rec.derived_sha256)
                    total_b64 += b64_len
        outcome.receipts.append(receipt)
    return outcome


class SentImageLedger:
    """已送 pixels 台账（bridge 内存；vendor generation 维度，V33）。

    vendor generation：每次 spawn ``new_session`` 后 transcript 清空 ——
    ``bump`` 由 bridge 在换代时调用。台账只影响图片通道；文本会话照常。
    """

    def __init__(self) -> None:
        self._by_session: Dict[str, Dict[int, Dict[str, Dict[str, Any]]]] = {}
        self._gen: Dict[str, int] = {}

    def generation(self, session_id: str) -> int:
        return self._gen.get(session_id, 1)

    def bump(self, session_id: str) -> int:
        gen = self._gen.get(session_id, 1) + 1
        self._gen[session_id] = gen
        self._by_session.pop(session_id, None)
        return gen

    def record(self, session_id: str, receipts: Sequence[AttachmentReceipt]) -> None:
        """送模后记账（sha → 所属 evidence；复验以 evidence 状态为准）。"""
        gen = self.generation(session_id)
        bucket = self._by_session.setdefault(session_id, {}).setdefault(gen, {})
        for r in receipts:
            if r.status == _SENT and r.derived_sha256:
                bucket[r.derived_sha256] = {
                    "evidence_id": r.evidence_id,
                    "ts": time.time(),
                }
        # 有界（极端多 turn 场景；换代会整体清空）。
        if len(bucket) > 128:
            for sha in list(bucket.keys())[: len(bucket) - 128]:
                bucket.pop(sha, None)

    async def find_revoked(self, session_id: str) -> Optional[List[str]]:
        """当前 vendor generation 里已失效（撤销/消失）的已送 sha 列表。

        复验口径：evidence 记录仍在本会话索引、state != revoked、blob 引用
        仍在（读得到）。任一不成立 = 该 sha 的历史 pixels 不再获准。

        review P2#10：索引**瞬态不可用**（存储异常）≠ 全部已撤 —— 返回
        None（调用方按「复验失败」只阻断图片，绝不换代毁记忆）。
        """
        gen = self.generation(session_id)
        bucket = (self._by_session.get(session_id) or {}).get(gen) or {}
        if not bucket:
            return []
        from app.services.visual_context.store import load_evidence_index_strict

        try:
            records_list = await load_evidence_index_strict(session_id)
        except Exception:  # noqa: BLE001 — 不可用 ≠ 已撤（fail-closed 只阻图）
            return None
        records = {r.evidence_id: r for r in records_list}
        revoked: List[str] = []
        for sha, meta in bucket.items():
            rec = records.get(str(meta.get("evidence_id") or ""))
            if rec is None or rec.state == "revoked" or rec.derived_sha256 != sha:
                revoked.append(sha)
                continue
            alive = await __import__("asyncio").to_thread(
                refs.blob_has_live_ref, sha, session_id)
            if not alive:
                revoked.append(sha)
        return revoked


# bridge 进程级单例（vendor transcript 生命周期与 bridge 一致）。
_ledger = SentImageLedger()


def get_sent_image_ledger() -> SentImageLedger:
    return _ledger
