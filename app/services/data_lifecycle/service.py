"""Artifact Lifecycle Service —— V3 生命周期与 staleness 传播（§四/§八/§九/§十一）。

职责边界：既有 ``artifact_registry`` 仍是会话产物存在性/状态的**唯一
真相**（ADR-0082 不变式不动）。本服务在其上加 V3 语义：

- **角色 / 持久层赋值**：``assign_role`` / ``set_persistence`` 写入
  record metadata（``logical_role`` / ``persistence_tier`` 等有界键），
  ArtifactContract 桥接器据此投影 —— Agent / Workflow 的显式声明通道；
- **状态转移**：``apply_state`` 用 vocabulary 生命周期迁移表校验，
  再落到账本 5 态（V3 态 → 账本态映射见 ``_LIFECYCLE_TO_SESSION``；
  无对应账本态的目标（declared/…）明确拒绝，绝不静默降级）；
- **来源变更感知**：``detect_source_change`` 用 content_revision
  计数器（token 证据）或显式 SourceRevision 判定变更类别；
- **staleness 传播**：``propagate_staleness`` 沿 ArtifactGraph 下游
  闭包把 valid 产物标记 stale 并记录 stale_source/stale_verdict ——
  「上游改了，下游不能无提示继续展示旧结果」（§十一）；
- **版本链 / 影响分析**：``version_history`` / ``impact_analysis``
  只读投影（versioning.build_version_chain / graph.dependents）。

容错契约与 registry 一致：记录失败降级为 False/空报告，绝不阻断
工具路径；所有写入走 registry 既有 per-session 锁。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.lib.data import vocabulary as vocab
from app.lib.data.artifact_contract import ArtifactContract, from_artifact_record
from app.lib.data.fingerprints import ChangeClass, staleness_verdict
from app.lib.data.versioning import (
    SourceRevision,
    VersionChain,
    build_version_chain,
    compare_revisions,
)

logger = logging.getLogger(__name__)

# V3 生命周期态 → 会话账本态（可落盘的投影；declared/ingesting 等
# 摄入期状态属于 ingest pipeline 的瞬时态，会话账本不承载）。
_LIFECYCLE_TO_SESSION: Dict[vocab.LifecycleState, str] = {
    vocab.LifecycleState.READY: "valid",
    vocab.LifecycleState.STALE: "stale",
    vocab.LifecycleState.SUPERSEDED: "superseded",
    vocab.LifecycleState.DELETED: "expired",
    vocab.LifecycleState.ERROR: "failed",
}

# metadata 键（有界短值；消费方：contract 桥接 / catalog / agent tools）
_MD_ROLE = "logical_role"
_MD_POLICY = "materialization_policy"
_MD_TIER = "persistence_tier"
_MD_STALE_REASON = "stale_reason"
_MD_STALE_SOURCE = "stale_source"
_MD_STALE_VERDICT = "stale_verdict"
_MD_STALE_AT = "stale_at"
_MD_SOURCE_REV = "source_revision_token"


class LifecycleOpResult:
    """操作结果（成功位 + 诚实原因）。"""

    __slots__ = ("ok", "reason")

    def __init__(self, ok: bool, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover — 诊断便利
        return f"LifecycleOpResult(ok={self.ok}, reason={self.reason!r})"


class PropagationReport:
    """一次 staleness 传播的结果（只读可序列化）。"""

    def __init__(
        self,
        source_artifact_id: str,
        verdict: str,
        marked: List[str],
        already_stale: List[str],
        not_in_ledger: List[str],
    ) -> None:
        self.source_artifact_id = source_artifact_id
        self.verdict = verdict
        self.marked = marked
        self.already_stale = already_stale
        self.not_in_ledger = not_in_ledger

    def to_dict(self) -> dict:
        return {
            "source_artifact_id": self.source_artifact_id,
            "verdict": self.verdict,
            "marked_stale": self.marked,
            "already_stale": self.already_stale,
            "not_in_ledger": self.not_in_ledger,
            "marked_count": len(self.marked),
        }


class ArtifactLifecycleService:
    """V3 生命周期服务（无状态薄层；状态都在 registry/session store）。"""

    # ── 契约视图 ────────────────────────────────────────────────────
    async def get_contract(
        self, session_id: str, artifact_id: str
    ) -> Optional[ArtifactContract]:
        """账本记录 → ArtifactContract（含新鲜 descriptor 修订号）。"""
        from app.services.artifact_registry import get_artifact

        record = await get_artifact(session_id, artifact_id)
        if record is None:
            return None
        contract = from_artifact_record(record)
        # 用 store 实时修订号增强 source_revision（记录层的 expires_at
        # 是 TTL 预估；revision 计数器才是覆写真相）。
        try:
            from app.services.session_data import session_data_manager

            descriptor = await session_data_manager.get_ref_descriptor(session_id, artifact_id)
            if isinstance(descriptor, dict):
                contract.source.source_revision = str(descriptor.get("content_revision") or "")
        except Exception:  # noqa: BLE001 — 探测失败不阻断投影
            pass
        return contract

    # ── 显式声明通道 ────────────────────────────────────────────────
    async def assign_role(
        self,
        session_id: str,
        artifact_id: str,
        role: object,
    ) -> LifecycleOpResult:
        """显式逻辑角色赋值（§五；覆盖 category 缺省）。"""
        from app.services.artifact_registry import update_record_metadata

        try:
            role_value = role.value if isinstance(role, vocab.LogicalRole) else str(role)
            role_enum = vocab.LogicalRole(role_value)
        except ValueError:
            return LifecycleOpResult(False, f"unknown logical role: {role!r}")
        ok = await update_record_metadata(
            session_id, artifact_id, metadata={_MD_ROLE: role_enum.value}
        )
        return LifecycleOpResult(ok, "" if ok else "record not found or store write failed")

    async def set_persistence(
        self,
        session_id: str,
        artifact_id: str,
        policy: object,
    ) -> LifecycleOpResult:
        """物化策略 / 持久层赋值（§十二）。"""
        from app.services.artifact_registry import update_record_metadata

        try:
            policy_value = (
                policy.value if isinstance(policy, vocab.MaterializationPolicy) else str(policy)
            )
            policy_enum = vocab.MaterializationPolicy(policy_value)
        except ValueError:
            return LifecycleOpResult(False, f"unknown materialization policy: {policy!r}")
        tier = vocab.policy_tier(policy_enum)
        ok = await update_record_metadata(
            session_id,
            artifact_id,
            metadata={_MD_POLICY: policy_enum.value, _MD_TIER: tier.value},
        )
        return LifecycleOpResult(ok, "" if ok else "record not found or store write failed")

    # ── 状态转移 ────────────────────────────────────────────────────
    async def apply_state(
        self,
        session_id: str,
        artifact_id: str,
        target: object,
    ) -> LifecycleOpResult:
        """V3 生命周期态转移（迁移表校验 → 账本态落盘）。"""
        from app.services.artifact_registry import get_artifact, mark_status

        try:
            target_value = (
                target.value if isinstance(target, vocab.LifecycleState) else str(target)
            )
            target_state = vocab.LifecycleState(target_value)
        except ValueError:
            return LifecycleOpResult(False, f"unknown lifecycle state: {target!r}")
        record = await get_artifact(session_id, artifact_id)
        if record is None:
            return LifecycleOpResult(False, "artifact not in ledger")
        current_state = vocab.lifecycle_from_session_status(record.status)
        if current_state is None:
            return LifecycleOpResult(False, f"unmappable current status: {record.status!r}")
        if not vocab.can_transition(current_state, target_state):
            return LifecycleOpResult(
                False,
                f"illegal transition {current_state.value} → {target_state.value}",
            )
        session_status = _LIFECYCLE_TO_SESSION.get(target_state)
        if session_status is None:
            return LifecycleOpResult(
                False,
                f"state {target_state.value} has no session-ledger projection "
                "(transient ingest states live in the ingest pipeline)",
            )
        ok = await mark_status(session_id, artifact_id, session_status)
        return LifecycleOpResult(ok, "" if ok else "ledger write failed")

    # ── 来源变更感知 ────────────────────────────────────────────────
    async def current_source_revision(
        self, session_id: str, ref: str
    ) -> Optional[SourceRevision]:
        """ref 当前修订证据（descriptor revision 计数器为 token）。"""
        try:
            from app.services.session_data import session_data_manager

            descriptor = await session_data_manager.get_ref_descriptor(session_id, ref)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(descriptor, dict):
            return None
        return SourceRevision(
            source_type="session_ref",
            source_ref=ref,
            revision_token=str(descriptor.get("content_revision") or ""),
            crs=str(descriptor.get("crs") or ""),
        )

    async def detect_source_change(
        self,
        session_id: str,
        ref: str,
        *,
        recorded: Optional[SourceRevision] = None,
    ) -> ChangeClass:
        """ref 是否相对记录快照发生变化（§九）。

        ``recorded`` 缺省时从账本 metadata 读取生产时快照
        （``source_revision_token`` 键）。
        """
        from app.services.artifact_registry import get_artifact

        if recorded is None:
            record = await get_artifact(session_id, ref)
            if record is None:
                return ChangeClass.UNKNOWN
            md = record.metadata if isinstance(record.metadata, dict) else {}
            recorded = SourceRevision(
                source_type="session_ref",
                source_ref=ref,
                revision_token=str(md.get(_MD_SOURCE_REV) or ""),
            )
        current = await self.current_source_revision(session_id, ref)
        return compare_revisions(recorded, current)

    # ── staleness 传播（§十一）──────────────────────────────────────
    async def propagate_staleness(
        self,
        session_id: str,
        source_artifact_id: str,
        *,
        change: ChangeClass = ChangeClass.CONTENT,
        reason: str = "upstream_changed",
        upstream_alive: bool = True,
    ) -> PropagationReport:
        """上游变更 → 下游闭包标记（valid → stale + 诊断 metadata）。

        幂等：已是 stale/superseded/expired/failed 的下游不重复标记。
        verdict 由变更类别决定（metadata-only → stale 但可提示续用；
        content/schema/crs → recompute）。
        """
        from app.services.artifact_registry import (
            build_artifact_graph,
            list_artifacts,
            update_record_metadata,
        )

        records = {r.artifact_id: r for r in await list_artifacts(session_id)}
        if source_artifact_id not in records:
            return PropagationReport(
                source_artifact_id,
                staleness_verdict(change, upstream_alive).value,
                [], [], [source_artifact_id],
            )
        graph = build_artifact_graph(records)
        downstream = graph.dependents(source_artifact_id)
        verdict = staleness_verdict(change, upstream_alive=upstream_alive)
        marked: List[str] = []
        already: List[str] = []
        now = time.time()
        for dep_id in downstream:
            rec = records.get(dep_id)
            if rec is None:
                continue
            if rec.status == "valid":
                # verdict 为 valid（change=NONE）时只附诊断不改状态。
                mark_stale = verdict is not staleness_verdict(ChangeClass.NONE)
                ok = await update_record_metadata(
                    session_id,
                    dep_id,
                    status="stale" if mark_stale else None,
                    metadata={
                        _MD_STALE_REASON: str(reason)[:64],
                        _MD_STALE_SOURCE: str(source_artifact_id)[:96],
                        _MD_STALE_VERDICT: verdict.value,
                        _MD_STALE_AT: now,
                    },
                )
                if ok:
                    marked.append(dep_id)
                else:
                    logger.warning(
                        "[Lifecycle] staleness mark failed session=%s artifact=%s",
                        session_id, dep_id,
                    )
            else:
                already.append(dep_id)
        return PropagationReport(
            source_artifact_id, verdict.value, marked, already, []
        )

    # ── 版本链 / 影响分析 ───────────────────────────────────────────
    async def version_history(
        self, session_id: str, artifact_id: str
    ) -> VersionChain:
        """replaces 链 → 版本史（旧 → 新；有界截断声明）。"""
        from app.services.artifact_registry import list_artifacts

        records = await list_artifacts(session_id)
        return build_version_chain(records, head_artifact_id=artifact_id)

    async def impact_analysis(
        self,
        session_id: str,
        artifact_id: str,
        *,
        change: ChangeClass = ChangeClass.CONTENT,
        upstream_alive: bool = True,
    ) -> Dict[str, Any]:
        """只读影响分析（§十 impact analysis）：不写状态，只给裁决。"""
        from app.services.artifact_registry import build_artifact_graph, list_artifacts

        records = {r.artifact_id: r for r in await list_artifacts(session_id)}
        verdict = staleness_verdict(change, upstream_alive=upstream_alive)
        if artifact_id not in records:
            return {
                "artifact_id": artifact_id,
                "exists": False,
                "verdict": verdict.value,
                "affected": [],
            }
        graph = build_artifact_graph(records)
        affected = []
        for dep_id in graph.dependents(artifact_id):
            rec = records[dep_id]
            affected.append(
                {
                    "artifact_id": dep_id,
                    "artifact_type": rec.artifact_type,
                    "status": rec.status,
                    "required_action": verdict.value,
                }
            )
        return {
            "artifact_id": artifact_id,
            "exists": True,
            "verdict": verdict.value,
            "affected": affected,
            "affected_count": len(affected),
        }


_service: Optional[ArtifactLifecycleService] = None


def get_lifecycle_service() -> ArtifactLifecycleService:
    global _service
    if _service is None:
        _service = ArtifactLifecycleService()
    return _service


def reset_lifecycle_service() -> None:
    global _service
    _service = None
