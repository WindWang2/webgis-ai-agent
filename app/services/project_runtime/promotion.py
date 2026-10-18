"""Durable ref promotion planner（ADR-0226 §3）。

把 freeze 时收集的 MapSpec source refs 规划并执行为 durable 指针：

- **分类先行**：already_durable / promotable / recomputable /
  external_version_pinned / non_promotable / sensitive；
- **quota-aware**：以项目 quota 余量收缩物化预算 —— 物化预算与 quota 双闸
  （materialize_ref_payload 的 pre-size gate 在写 BlobStore 之前量尺，quota
  余量作为预算上界传入，超限 ref 如实 ``oversized``/``quota_blocked``，
  零字节落盘）；
- **幂等**：BlobStore CAS（内容寻址，同 digest 复用）；已 durable 的 ref
  绝不重物化；``persistence_tier`` 盖章只升不降（与 workspace snapshot
  ``_claim_durability`` 同纪律 —— GC interlock 零改动生效）；
- **诚实回执**：逐 ref 分类 + skip_reason，绝不把死 ref 说成 promoted。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_runtime.contract import (
    ALREADY_DURABLE,
    DurablePointerRef,
    EXTERNAL_PINNED,
    NON_PROMOTABLE,
    OVERSIZED,
    PROMOTED,
    QUOTA_BLOCKED,
    RECOMPUTABLE,
    SENSITIVE,
    SourceBinding,
)

logger = logging.getLogger(__name__)

#: 单次 freeze 的默认物化预算（与 workspace snapshot 同界）。
DEFAULT_FREEZE_BUDGET_BYTES = 256 * 1024 * 1024


@dataclass
class PromotionOutcome:
    """单 ref 的晋升结果（分类 + 指针 + 跳过原因）。"""

    promotion_class: str = NON_PROMOTABLE
    pointer: Optional[DurablePointerRef] = None
    skip_reason: str = ""

    def to_binding(
        self, source_id: str, kind: str, ref: str
    ) -> SourceBinding:
        return SourceBinding(
            source_id=source_id,
            kind=kind,
            ref=ref,
            promotion_class=self.promotion_class,
            durable_pointer=self.pointer,
            skip_reason=self.skip_reason,
        )


@dataclass
class PromotionReceipt:
    """整批晋升回执（进入 manifest 的 disclosure 面）。"""

    outcomes: Dict[str, PromotionOutcome] = field(default_factory=dict)
    promoted_bytes: int = 0
    quota_refusals: int = 0
    budget_skips: int = 0

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for o in self.outcomes.values():
            counts[o.promotion_class] = counts.get(o.promotion_class, 0) + 1
        return {
            "counts": counts,
            "promoted_bytes": self.promoted_bytes,
            "quota_refusals": self.quota_refusals,
            "budget_skips": self.budget_skips,
        }


def _quota_remaining_bytes(db: Any, project_id: str) -> Optional[int]:
    """项目 durable 字节 quota 余量（None = 无 quota 约束）。

    只读判定，绝不写库。quota 语义与 ``check_quota`` 完全同源（同一
    policy + 同一 usage 投影），这里只取余量作为物化预算的上界。
    """
    try:
        from app.services.data_lifecycle.quota import (
            ProjectQuotaPolicy,
            project_quota_usage,
        )

        policy = ProjectQuotaPolicy.from_env()
        if not policy.max_bytes:
            return None
        usage = project_quota_usage(db, project_id)
        used = int(usage.get("bytes", 0) or 0)
        return max(0, int(policy.max_bytes) - used)
    except Exception as e:  # noqa: BLE001 — quota 不可判定 = 不施加 quota 上界
        logger.info("[RuntimePromotion] quota lookup skipped: %s", e)
        return None


async def _existing_durable_pointer(
    project_id: str, ref: str
) -> Optional[DurablePointerRef]:
    """复用既有 promoted 头指针（DB Artifact.metadata_json 真相；只读）。"""
    if not project_id or not ref.startswith("ref:"):
        return None
    try:
        from app.services.workspace.snapshot import _fetch_promoted_pointers_sync

        ptr = await asyncio.to_thread(_fetch_promoted_pointers_sync, project_id, [ref])
        found = ptr.get(ref)
        if isinstance(found, dict) and found.get("content_location"):
            return DurablePointerRef(
                content_location=str(found.get("content_location") or ""),
                content_payload_sha256=str(found.get("content_payload_sha256") or ""),
                content_type=str(found.get("content_type") or "json"),
                byte_size=int(found.get("byte_size") or 0),
            )
    except Exception as e:  # noqa: BLE001 — 指针查找是增值不是前提
        logger.info("[RuntimePromotion] pointer lookup skipped for %s: %s", ref, e)
    return None


async def plan_and_promote(
    session_id: str,
    project_id: str,
    refs: List[Tuple[str, str, str]],
    *,
    db: Any = None,
    budget_bytes: int = DEFAULT_FREEZE_BUDGET_BYTES,
) -> PromotionReceipt:
    """对 ``[(source_id, kind, ref), ...]` 执行晋升规划 + 物化。

    顺序 IO 纪律（低频显式 freeze 路径，正确优先，与 resume 同款）：
    逐 ref 探测 → 分类 → 物化；refs 由调用方截断（≤SOURCE_BINDING_LIMIT）。
    """
    from app.services.artifact_registry import get_artifact, probe_ref
    from app.services.workspace.durability import materialize_ref_payload

    receipt = PromotionReceipt()
    quota_remaining = (
        _quota_remaining_bytes(db, project_id) if db is not None else None
    )
    remaining_budget = int(budget_bytes)

    # 去重（同一 ref 可同时是 source 与 chartRef 绑定 —— 只物化/计费一次；
    # 首见绑定决定 outcome 键，与 freeze 的按 ref 优先查找一致）。
    deduped: List[Tuple[str, str, str]] = []
    seen_refs: set = set()
    for source_id, kind, ref in refs:
        key = ref if ref else source_id
        if key in seen_refs:
            continue
        seen_refs.add(key)
        deduped.append((source_id, kind, ref))

    for source_id, kind, ref in deduped:
        if not ref:
            # inline / 外部 URL 源：非会话 ref，无晋升语义（版本钉扎面归
            # dataset 描述符比对）。
            receipt.outcomes[source_id or ref] = PromotionOutcome(
                promotion_class=EXTERNAL_PINNED, skip_reason="no_session_ref"
            )
            continue
        if not ref.startswith("ref:"):
            receipt.outcomes[source_id or ref] = PromotionOutcome(
                promotion_class=EXTERNAL_PINNED, skip_reason="external_url"
            )
            continue

        record = await get_artifact(session_id, ref)
        metadata = (record.metadata if record is not None else None) or {}
        if bool(metadata.get("sensitive")):
            # sensitive 硬拒绝优先于 already_durable 指针复用（review NEW-4：
            # 先晋升后被标记 sensitive 的 ref 不得经旧指针回流）。
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=SENSITIVE, skip_reason="sensitive"
            )
            continue

        # 已 durable？绝不重物化（promotion 真相的头指针复用）。
        existing = await _existing_durable_pointer(project_id, ref)
        if existing is not None:
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=ALREADY_DURABLE, pointer=existing
            )
            continue

        live = await probe_ref(session_id, ref) is not None
        if not live:
            # 载荷已亡：有血缘父 = 可重算；否则如实 non_promotable。
            has_parents = bool(record is not None and record.inputs)
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=(
                    RECOMPUTABLE if has_parents else NON_PROMOTABLE
                ),
                skip_reason="payload_dead",
            )
            continue

        # 活载荷 → quota + budget 双闸物化（pre-size gate 写盘前量尺）。
        effective_budget = remaining_budget
        if quota_remaining is not None:
            effective_budget = min(effective_budget, quota_remaining)
        if effective_budget <= 0:
            # 归因：quota 余量耗尽（存在 quota 政策）→ quota_blocked；
            # 无 quota 政策（None）下的预算耗尽 → oversized（不虚增 quota 拒绝数）。
            quota_binding = quota_remaining is not None and quota_remaining <= 0
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=QUOTA_BLOCKED if quota_binding else OVERSIZED,
                skip_reason="quota" if quota_binding else "budget",
            )
            if quota_binding:
                receipt.quota_refusals += 1
            else:
                receipt.budget_skips += 1
            continue

        pointer, skip_reason = await materialize_ref_payload(
            session_id, ref, budget_bytes=effective_budget
        )
        if pointer is not None:
            ptr = DurablePointerRef(
                content_location=str(pointer.get("content_location") or ""),
                content_payload_sha256=str(
                    pointer.get("content_payload_sha256") or ""),
                content_type=str(pointer.get("content_type") or "json"),
                byte_size=int(pointer.get("byte_size") or 0),
            )
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=PROMOTED, pointer=ptr
            )
            receipt.promoted_bytes += ptr.byte_size
            remaining_budget = max(0, remaining_budget - ptr.byte_size)
            if quota_remaining is not None:
                quota_remaining = max(0, quota_remaining - ptr.byte_size)
            continue
        if skip_reason == "budget":
            # 物化器统一报 budget；quota 上界是否为约束侧由余量判定。
            quota_binding = (
                quota_remaining is not None and quota_remaining < remaining_budget
            )
            receipt.outcomes[ref] = PromotionOutcome(
                promotion_class=QUOTA_BLOCKED if quota_binding else OVERSIZED,
                skip_reason="quota" if quota_binding else "budget",
            )
            if quota_binding:
                receipt.quota_refusals += 1
            else:
                receipt.budget_skips += 1
            continue
        # 载荷不存活/写盘失败 → 如实 non_promotable（probe miss 与并发
        # 逐出之间的窗口：诚实披露，绝不虚构指针）。
        receipt.outcomes[ref] = PromotionOutcome(
            promotion_class=NON_PROMOTABLE, skip_reason="materialize_failed"
        )

    await _stamp_persistence_tier(session_id, list(receipt.outcomes.keys()))
    return receipt


async def _stamp_persistence_tier(session_id: str, refs: List[str]) -> None:
    """GC interlock：被 manifest 声明的 ref 盖 ``workspace`` 章（只升不降）。

    与 workspace snapshot ``_claim_durability`` 同款 best-effort：无账本
    记录的 ref 更新即 no-op；已是 persistent 章的绝不降级。
    """
    from app.services.artifact_registry import get_artifact, update_record_metadata
    from app.services.workspace.durability import WORKSPACE_TIER

    tier_rank = {"session": 0, WORKSPACE_TIER: 1, "persistent": 2}
    for ref in refs[:128]:
        if not ref.startswith("ref:"):
            continue
        try:
            rec = await get_artifact(session_id, ref)
            existing = str(
                ((rec.metadata if rec is not None else None) or {}).get(
                    "persistence_tier") or "")
        except Exception:  # noqa: BLE001 — 账本缺席按未盖章
            existing = ""
        if tier_rank.get(existing, -1) >= tier_rank[WORKSPACE_TIER]:
            continue
        try:
            await update_record_metadata(
                session_id, ref, metadata={"persistence_tier": WORKSPACE_TIER}
            )
        except Exception:  # noqa: BLE001 — 盖章失败不阻断 freeze（GC 披露面兜底）
            logger.info(
                "[RuntimePromotion] tier stamp skipped for %s", ref)
