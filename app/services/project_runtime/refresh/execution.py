"""G10 UPD-05：隔离候选执行、CAS 原子采纳、前向回退。

红线：
- **不重新实现 #2009/#2019 的锁借用**：直接复用
  ``app.services.distributed_lock.session_lock_registry``；
- 采纳走既有 MapSpec 权威持久层 ``MapSpecStore.save_mapspec`` 的单事务
  ``mutation_revision`` 原子落地，锁内二次读取 CAS（expected revision 与
  proposal fingerprint 双核对）；不另写状态仓库；
- 失败/取消/崩溃：候选只写 ``data-refresh/`` 隔离命名空间，当前可见
  MapSpec 零触碰；lease 由 kernel 持有，进程死亡自动释放；
- 回退**不倒写 revision**：优先调既有 ``resume_runtime_snapshot``（新
  session + ResumeReceipt）；无快照时以「采纳前 MapSpec」作一次新的 CAS
  提交（forward revert，revision 单调增），旧快照字节不变；
- 幂等键 = ``source-version set + proposal_fingerprint + operation``。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple

from app.lib.data.fingerprints import canonical_fingerprint

from .contracts import PROPOSAL_FINGERPRINT_PREFIX
from .records import AdoptionReceipt, RefreshCandidate, RefreshProposal


def retry_key(proposal: RefreshProposal, operation: str) -> str:
    """幂等键：版本集 + 提案指纹 + 操作（绝不混入时间/随机量）。"""
    return canonical_fingerprint({
        "proposal": proposal.proposal_id,
        "fp": proposal.proposal_fingerprint,
        "versions": dict(sorted(proposal.source_versions.items())),
        "operation": operation,
    })[:64]


def verify_no_drift(
    *,
    proposal: RefreshProposal,
    current_versions: Dict[str, str],
    current_revision: int,
    current_mapspec_fingerprint: str,
    owner_id: str,
) -> List[str]:
    """预览后漂移检查：版本 / revision / MapSpec / 权限任一变化都要重预览。"""
    drift: List[str] = []
    if dict(sorted(current_versions.items())) != dict(sorted(proposal.source_versions.items())):
        drift.append("source_version_set_changed")
    if int(current_revision) != int(proposal.mapspec_revision):
        drift.append(f"mapspec_revision_drift:{proposal.mapspec_revision}->{current_revision}")
    if (proposal.mapspec_fingerprint and current_mapspec_fingerprint
            and proposal.mapspec_fingerprint != current_mapspec_fingerprint):
        drift.append("mapspec_content_changed_after_preview")
    if proposal.owner_id and owner_id and proposal.owner_id != owner_id:
        drift.append("owner_permission_changed")
    if not proposal.proposal_fingerprint.startswith(PROPOSAL_FINGERPRINT_PREFIX):
        drift.append("invalid_proposal_fingerprint")
    return drift


class MapSpecStatePort(Protocol):
    """当前可见 MapSpec 的读/写端口（生产实现走既有 session 权威）。"""

    async def read(self, session_id: str) -> Tuple[Dict[str, Any], int]: ...

    async def commit(
        self, session_id: str, doc: Dict[str, Any], *,
        expected_revision: Optional[int], mutation_id: str,
    ) -> Dict[str, Any]: ...


class SessionMapSpecPort:
    """生产实现：``session_data_manager`` + 既有 ``session_lock_registry``
    + ``MapSpecStore.save_mapspec``（spec 与 revision 同一事务原子落地）。
    """

    async def read(self, session_id: str) -> Tuple[Dict[str, Any], int]:
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
        doc = state.get("mapspec")
        if not isinstance(doc, dict):
            doc = await mapspec_store_instance.get_mapspec(session_id) or {}
        revision = int(state.get("_cartographic_mutation_revision") or 0)
        return dict(doc), revision

    async def commit(
        self, session_id: str, doc: Dict[str, Any], *,
        expected_revision: Optional[int], mutation_id: str,
    ) -> Dict[str, Any]:
        from app.services.distributed_lock import session_lock_registry
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.session_data import session_data_manager

        # 复用既有 session 锁（不新建第二执行器/锁注册表）
        async with session_lock_registry.lock(session_id):
            state = await session_data_manager.get_map_state(session_id)
            current = int(state.get("_cartographic_mutation_revision") or 0)
            if expected_revision is not None and current != int(expected_revision):
                return {
                    "status": "superseded",
                    "revision_before": current,
                    "revision_after": current,
                    "detail": f"expected {expected_revision}, current {current}",
                }
            new_revision = current + 1
            try:
                persisted = await mapspec_store_instance.save_mapspec(
                    session_id, dict(doc), mutation_revision=new_revision,
                )
            except Exception as exc:  # noqa: BLE001 — 失败不得留下半新半旧
                return {"status": "failed", "revision_before": current,
                        "revision_after": current, "detail": str(exc)[:200]}
            return {
                "status": "committed",
                "revision_before": current,
                "revision_after": new_revision,
                "mutation_id": mutation_id,
                "revision_persisted": bool((persisted or {}).get("revision_persisted")),
            }


class InMemoryMapSpecPort:
    """本地合成数据端口（无真实会话后端时走同一套编排/CAS 代码）。"""

    def __init__(self, doc: Optional[Dict[str, Any]] = None, revision: int = 0) -> None:
        self.doc: Dict[str, Any] = dict(doc or {})
        self.revision = int(revision)
        self.commits: List[Dict[str, Any]] = []

    async def read(self, session_id: str) -> Tuple[Dict[str, Any], int]:
        return dict(self.doc), self.revision

    async def commit(
        self, session_id: str, doc: Dict[str, Any], *,
        expected_revision: Optional[int], mutation_id: str,
    ) -> Dict[str, Any]:
        if expected_revision is not None and self.revision != int(expected_revision):
            return {"status": "superseded", "revision_before": self.revision,
                    "revision_after": self.revision}
        before = self.revision
        self.doc = dict(doc)
        self.revision = before + 1
        entry = {"mutation_id": mutation_id, "before": before, "after": self.revision}
        self.commits.append(entry)
        return {"status": "committed", "revision_before": before,
                "revision_after": self.revision, "revision_persisted": True}


async def commit_candidate(
    *,
    port: MapSpecStatePort,
    proposal: RefreshProposal,
    candidate: RefreshCandidate,
    candidate_doc: Dict[str, Any],
    current_versions: Dict[str, str],
    owner_id: str,
) -> Tuple[AdoptionReceipt, Optional[Dict[str, Any]]]:
    """受控 CAS 采纳：返回 (回执, 采纳前 MapSpec 用于 forward revert)。"""
    current_doc, current_revision = await port.read(proposal.session_id)
    current_fp = canonical_fingerprint(current_doc)
    drift = verify_no_drift(
        proposal=proposal, current_versions=current_versions,
        current_revision=current_revision,
        current_mapspec_fingerprint=current_fp, owner_id=owner_id,
    )
    if drift:
        return AdoptionReceipt(
            proposal_id=proposal.proposal_id, action="adopt", status="blocked",
            tool_execution="ok", task_execution="ok",
            map_observation="unverified", version_adoption="unchanged",
            revision_before=current_revision, revision_after=current_revision,
            proposal_fingerprint=proposal.proposal_fingerprint,
            warnings=["drift: " + "; ".join(drift)],
        ), current_doc
    if not candidate.adoptable:
        return AdoptionReceipt(
            proposal_id=proposal.proposal_id, action="adopt", status="blocked",
            tool_execution="ok", task_execution="ok",
            map_observation="unverified", version_adoption="unchanged",
            revision_before=current_revision, revision_after=current_revision,
            proposal_fingerprint=proposal.proposal_fingerprint,
            warnings=["candidate not adoptable: hard semantic errors or unresolved conflicts"],
        ), current_doc
    if candidate.candidate_fingerprint and candidate.candidate_fingerprint != \
            canonical_fingerprint(candidate_doc):
        return AdoptionReceipt(
            proposal_id=proposal.proposal_id, action="adopt", status="blocked",
            warnings=["candidate document fingerprint mismatch"],
            revision_before=current_revision, revision_after=current_revision,
        ), current_doc

    result = await port.commit(
        proposal.session_id, candidate_doc,
        expected_revision=current_revision,
        mutation_id=retry_key(proposal, "adopt"),
    )
    status = str(result.get("status") or "failed")
    adopted = status == "committed"
    return AdoptionReceipt(
        proposal_id=proposal.proposal_id, action="adopt",
        status="adopted" if adopted else status,
        tool_execution="ok",
        task_execution="ok" if adopted else "failed",
        map_observation="unverified",   # 采纳 ≠ 地图已正确显示
        version_adoption="adopted" if adopted else "unchanged",
        revision_before=int(result.get("revision_before") or current_revision),
        revision_after=int(result.get("revision_after") or current_revision),
        proposal_fingerprint=proposal.proposal_fingerprint,
        mapspec_fingerprint=(canonical_fingerprint(candidate_doc) if adopted
                             else canonical_fingerprint(current_doc)),
        warnings=([str(result.get("detail"))] if result.get("detail") else []),
    ), current_doc


async def forward_rollback(
    *,
    port: MapSpecStatePort,
    proposal: RefreshProposal,
    baseline_doc: Dict[str, Any],
    owner_id: str,
) -> AdoptionReceipt:
    """前向回退：把采纳前 MapSpec 作为**新 revision**提交，不倒写、不改快照。"""
    _doc, current_revision = await port.read(proposal.session_id)
    result = await port.commit(
        proposal.session_id, baseline_doc,
        expected_revision=current_revision,
        mutation_id=retry_key(proposal, "rollback"),
    )
    ok = str(result.get("status")) == "committed"
    return AdoptionReceipt(
        proposal_id=proposal.proposal_id, action="rollback",
        status="rolled_back" if ok else str(result.get("status") or "blocked"),
        tool_execution="ok",
        task_execution="ok" if ok else "failed",
        map_observation="unverified",
        version_adoption="rolled_back" if ok else "unchanged",
        revision_before=int(result.get("revision_before") or current_revision),
        revision_after=int(result.get("revision_after") or current_revision),
        proposal_fingerprint=proposal.proposal_fingerprint,
        mapspec_fingerprint=canonical_fingerprint(baseline_doc),
        warnings=["forward revert creates a NEW revision; immutable snapshots untouched",
                  "map must be re-observed after rollback"],
    )


__all__ = [
    "MapSpecStatePort", "SessionMapSpecPort", "InMemoryMapSpecPort",
    "retry_key", "verify_no_drift", "commit_candidate", "forward_rollback",
]
