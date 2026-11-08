"""G10 项目数据刷新 · 编排门面（对外唯一入口）。

真实调用链：
  collect_project_sources（DB + session ref descriptor/payload）
    → compute_source_diff（四态差异）
    → build_refresh_plan（既有 compute_affected_subgraph 影响子图）
    → three_way_merge（user-wins 合并）
    → 隔离候选命名空间
    → commit_candidate（既有 session 锁 + MapSpecStore 原子 CAS）
    → forward_rollback / 既有 resume_runtime_snapshot

诚实披露：
- preview 只写 proposal 账本，**不改 MapSpec / 数据集 / 版本 / 基线**；
- 候选产物是「数据版本重绑定 + 人工编辑保留」的 MapSpec，不伪造 GIS
  分析数值；科学重算节点由既有 Workflow Runtime 依据本计划执行
  （本线不新建第二执行器）；
- 工具执行 / 任务执行 / 地图观察 / 版本采纳四态分开上报。
"""
from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.lib.data.fingerprints import canonical_fingerprint

from .contracts import (
    ChangeEvidence,
    DiffBudget,
    EvidenceLevel,
    PlanDecision,
    ProposalStatus,
    CandidateStatus,
    SourceSnapshot,
)
from .diff import compute_source_diff
from .execution import (
    InMemoryMapSpecPort,
    MapSpecStatePort,
    SessionMapSpecPort,
    commit_candidate,
    forward_rollback,
    retry_key,
    verify_no_drift,
)
from .impact import build_refresh_plan
from .merge import three_way_merge
from .records import (
    AdoptionReceipt,
    CheckReceipt,
    RefreshCandidate,
    RefreshProposal,
)
from .sources import (
    SourcePayloadProvider,
    SessionRefPayloadProvider,
    collect_project_sources,
)
from .store import RefreshStore, get_refresh_store


class RefreshError(Exception):
    """带稳定 code 的领域错误（路由层映射 HTTP 状态）。"""

    def __init__(self, code: str, detail: str = "", status: int = 409) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.status = status


def _mint(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _bounded_dag_from_analysis_graph(graph: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把既有 analysis_graph 投影转成 compute_affected_subgraph 的 bounded DAG 形状。"""
    if not isinstance(graph, dict):
        return None
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    nodes: List[Dict[str, Any]] = []
    for n in raw_nodes[:64]:
        if not isinstance(n, dict):
            continue
        nid = n.get("node_id") or n.get("id") or n.get("key")
        if not nid:
            continue
        nodes.append({
            "node_id": str(nid)[:64],
            "parameters": n.get("parameters") if isinstance(n.get("parameters"), list) else [],
            "depends_on": [str(x) for x in (n.get("depends_on") or [])[:16]],
        })
    if not nodes:
        return None
    edges: List[Dict[str, str]] = []
    for e in (graph.get("edges") or [])[:256]:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            edges.append({"from": str(e["from"]), "to": str(e["to"])})
    return {"nodes": nodes, "edges": edges}


def _mapspec_derived_dag(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    """无会话分析图时的有界回退：由真实 MapSpec 图层派生的投影。

    诚实标注为 projection fallback；不猜测不存在的分析节点。
    """
    layers = [x for x in (mapspec.get("layers") or []) if isinstance(x, dict) and x.get("id")]
    nodes: List[Dict[str, Any]] = [
        {"node_id": f"data:{str(x['id'])[:56]}", "parameters": []} for x in layers[:32]
    ]
    nodes += [
        {"node_id": "analysis:normalized", "parameters": []},
        {"node_id": "output:legend", "parameters": []},
        {"node_id": "output:map", "parameters": []},
    ]
    edges = [{"from": f"data:{str(x['id'])[:56]}", "to": "output:map"} for x in layers[:32]]
    edges += [
        {"from": "analysis:normalized", "to": "output:map"},
        {"from": "output:legend", "to": "output:map"},
    ]
    return {"nodes": nodes, "edges": edges}


def _rebind_sources(
    mapspec: Dict[str, Any], *, diffs: List[Any], source_versions: Dict[str, str]
) -> Dict[str, Any]:
    """把新数据版本证据重绑定到候选 MapSpec（不伪造分析数值）。

    只写版本证据字段（``content_revision`` / ``version_token``），不改几何、
    属性、样式、层序。匹配不到来源时如实跳过（warnings 在调用方披露）。
    """
    doc = copy.deepcopy(mapspec)
    changed_ids = {d.logical_id for d in diffs if d.change_class != "none"}
    tokens = {k: v for k, v in source_versions.items() if k in changed_ids}
    if not tokens:
        return doc

    sources = doc.get("sources")
    if isinstance(sources, dict):
        for key, entry in list(sources.items())[:64]:
            if isinstance(entry, dict) and any(t and t in str(key) + str(entry.get("ref", ""))
                                               for t in tokens.values()):
                entry["content_revision"] = next(iter(tokens.values()))[:64]
    elif isinstance(sources, list):
        for entry in sources[:64]:
            if isinstance(entry, dict):
                entry["content_revision"] = next(iter(tokens.values()))[:64]

    for layer in (doc.get("layers") or [])[:64]:
        if not isinstance(layer, dict):
            continue
        binding = layer.get("data_binding")
        if isinstance(binding, dict):
            binding["version_token"] = next(iter(tokens.values()))[:64]
        elif changed_ids:
            layer["data_binding"] = {"version_token": next(iter(tokens.values()))[:64]}
    return doc


class ProjectDataRefreshService:
    """刷新工作流门面（依赖注入端口，生产/合成数据共用同一编排）。"""

    def __init__(
        self,
        *,
        store: Optional[RefreshStore] = None,
        port: Optional[MapSpecStatePort] = None,
        payload_provider: Optional[SourcePayloadProvider] = None,
    ) -> None:
        self.store = store or get_refresh_store()
        self.port = port or SessionMapSpecPort()
        self.payload_provider = payload_provider or SessionRefPayloadProvider()

    # ── 内部：装载当前事实 ──────────────────────────────────────────
    async def _facts(
        self, db: Session, *, project_id: str, session_id: str,
        user_id: str, establish_baseline: bool = False,
    ) -> Dict[str, Any]:
        baselines = self.store.load_baselines(project_id)
        inventory, base_snaps, cur_snaps, meta = await collect_project_sources(
            db, project_id=project_id, session_id=session_id, user_id=user_id,
            payload_provider=self.payload_provider,
            baselines=(baselines or {}).get("snapshots") if isinstance(baselines, dict) else None,
            runtime_descriptor_versions=(
                (baselines or {}).get("runtime_descriptor_versions")
                if isinstance(baselines, dict) else None
            ),
            establish_baseline=establish_baseline,
        )
        if meta.get("baseline_established"):
            await self.store.save_baselines(project_id, {
                "snapshots": {
                    k: v.model_dump(mode="json") for k, v in cur_snaps.items()
                },
                "established_at": meta["checked_at"],
                "established_by": user_id,
            })
        current_doc, revision = await self.port.read(session_id)
        return {
            "inventory": inventory, "baseline": base_snaps, "current": cur_snaps,
            "meta": meta, "mapspec": current_doc, "revision": revision,
            "mapspec_fingerprint": canonical_fingerprint(current_doc),
        }

    def _diffs(self, facts: Dict[str, Any], budget: Optional[DiffBudget]) -> List[Any]:
        out = []
        for logical_id, cur in facts["current"].items():
            base = facts["baseline"].get(logical_id)
            if base is None:
                continue
            out.append(compute_source_diff(
                baseline=base, current=cur, budget=budget,
            ))
        return out

    async def _dag(self, session_id: str, mapspec: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
        try:
            from app.services.gis_harness.analysis_graph import (
                build_analysis_graph_for_session,
            )
            graph = await build_analysis_graph_for_session(session_id)
            dag = _bounded_dag_from_analysis_graph(graph)
            if dag:
                return dag, "analysis_graph"
        except Exception:  # noqa: BLE001 — 分析图是检视图，缺席 → 诚实回退
            pass
        return _mapspec_derived_dag(mapspec), "mapspec_projection_fallback"

    # ── J1 检查更新 ─────────────────────────────────────────────────
    async def check_updates(
        self, db: Session, *, project_id: str, session_id: str,
        user_id: str, establish_baseline: bool = True,
    ) -> CheckReceipt:
        facts = await self._facts(
            db, project_id=project_id, session_id=session_id,
            user_id=user_id, establish_baseline=establish_baseline,
        )
        inv = facts["inventory"]
        counts = {
            "changed": sum(1 for i in inv.items if i.verdict == "changed"),
            "unchanged": sum(1 for i in inv.items if i.verdict == "unchanged"),
            "missing": sum(1 for i in inv.items if i.state.value == "missing"),
            "unverifiable": sum(1 for i in inv.items
                                if i.verdict == "unverifiable" or
                                i.evidence in (EvidenceLevel.NONE, EvidenceLevel.TIMESTAMP_ONLY)),
        }
        warnings = list(inv.warnings)
        if facts["meta"].get("baseline_established"):
            warnings.append(
                "baseline established on first check: no data-change claim is made; "
                "run check again after data changes"
            )
        return CheckReceipt(
            project_id=project_id, checked_at=facts["meta"]["checked_at"],
            sources_total=len(inv.items), changed=counts["changed"],
            unchanged=counts["unchanged"], missing=counts["missing"],
            unverifiable=counts["unverifiable"], inventory=inv,
            stale=counts["changed"] > 0, warnings=warnings[:16],
            truncated=inv.truncated,
        )

    # ── J2 解释过期 ─────────────────────────────────────────────────
    async def explain_stale(
        self, *, project_id: str, proposal_id: str, user_id: str
    ) -> Dict[str, Any]:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        reasons: List[str] = []
        for d in proposal.diffs:
            if d.change_class == "none":
                continue
            reasons.append(
                f"{d.logical_id}: {d.change_class} ({d.evidence.value}) — {d.reason}"
            )
        return {
            "proposal_id": proposal_id,
            "project_id": project_id,
            "stale": bool(reasons),
            "reasons": reasons[:16],
            "affected_nodes": proposal.plan.recompute[:32],
            "decisions": proposal.plan.decisions,
            "conservative": proposal.plan.conservative,
            "unverifiable": [d.logical_id for d in proposal.diffs
                             if d.evidence in (ChangeEvidence.UNKNOWN,
                                               ChangeEvidence.UNVERIFIED)],
            "warnings": proposal.plan.warnings[:16],
            "truncated": any(d.truncated for d in proposal.diffs),
        }

    # ── J3 预览（只读：不改 MapSpec / 数据集 / 版本 / 基线） ──────────
    async def preview(
        self, db: Session, *, project_id: str, session_id: str,
        user_id: str, org_id: str = "", style_only: bool = False,
        dag: Optional[Dict[str, Any]] = None,
        budget: Optional[DiffBudget] = None,
    ) -> RefreshProposal:
        facts = await self._facts(
            db, project_id=project_id, session_id=session_id, user_id=user_id,
        )
        diffs = self._diffs(facts, budget)
        if dag is not None:
            used_dag, dag_source = dag, "explicit"
        else:
            used_dag, dag_source = await self._dag(session_id, facts["mapspec"])
        if dag_source == "mapspec_projection_fallback":
            facts["inventory"].warnings.append(
                "analysis graph unavailable; using bounded MapSpec projection"
            )

        mapspec = facts["mapspec"]
        locks = list((mapspec.get("workbench") or {}).get("lockedLayerIds") or []) + \
                list((mapspec.get("workbench") or {}).get("lockedComponentIds") or [])
        plan = build_refresh_plan(
            diffs=diffs, dag=used_dag, style_only=style_only,
            source_versions=facts["meta"]["source_versions"],
            mapspec_revision=facts["revision"], user_locks=locks,
            owner_scope=f"{user_id}:{org_id}",
        )
        if dag_source == "mapspec_projection_fallback" and not style_only and not plan.conservative:
            # 图只是投影时不能证明局部安全 → 保守整项重算
            plan.conservative = True
            plan.recompute = sorted({n["node_id"] for n in used_dag["nodes"]})
            plan.reuse = []
            plan.decisions = {n: PlanDecision.RECOMPUTE.value for n in plan.recompute}
            plan.warnings.append(
                "conservative full recompute: analysis graph unavailable, "
                "partial-safety could not be proven"
            )
        if style_only:
            plan.preserved_user_edits = sorted(set(locks))
        for d in diffs:
            if d.warnings:
                plan.warnings.extend(f"{d.logical_id}: {w}" for w in d.warnings[:4])
        plan.warnings = plan.warnings[:16]

        now = time.time()
        proposal = RefreshProposal(
            proposal_id=_mint("g10p"), project_id=project_id,
            session_id=session_id, owner_id=user_id, org_id=org_id,
            status=ProposalStatus.PREVIEW, created_at=now, updated_at=now,
            baseline_snapshot_id=str((self.store.load_baselines(project_id) or {})
                                     .get("runtime_snapshot_id") or ""),
            mapspec_revision=facts["revision"],
            mapspec_fingerprint=facts["mapspec_fingerprint"],
            user_locks=sorted(set(locks)),
            source_versions=facts["meta"]["source_versions"],
            proposal_fingerprint=plan.proposal_fingerprint,
            inventory=facts["inventory"],
            diffs=diffs,
            plan=plan,
            warnings=plan.warnings[:16],
        )
        if not await self.store.save_proposal(proposal):
            raise RefreshError("proposal_persist_failed", status=500)
        # 预览时把当前 MapSpec 存为合并基线（隔离命名空间，不改可见状态）
        await self.store.save_doc(project_id, f"g10p-{proposal.proposal_id}-baseline",
                                  mapspec)
        return proposal

    # ── 查询 / 恢复 ─────────────────────────────────────────────────
    def _require_proposal(
        self, project_id: str, proposal_id: str, user_id: str
    ) -> RefreshProposal:
        proposal = self.store.load_proposal(project_id, proposal_id)
        if proposal is None:
            raise RefreshError("proposal_not_found", status=404)
        if proposal.owner_id and user_id and proposal.owner_id != user_id:
            raise RefreshError("forbidden", "proposal belongs to another owner", status=404)
        return proposal

    async def get_proposal(self, *, project_id: str, proposal_id: str,
                           user_id: str) -> RefreshProposal:
        return self._require_proposal(project_id, proposal_id, user_id)

    async def list_proposals(self, *, project_id: str) -> List[Dict[str, Any]]:
        return await self.store.list_proposals(project_id)

    async def status(self, *, project_id: str, proposal_id: str,
                     user_id: str) -> Dict[str, Any]:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        candidate = None
        for item in await self.store.list_proposals(project_id):
            if item["proposal_id"] == proposal_id:
                break
        cid = f"g10c-{proposal.proposal_id}"
        candidate = self.store.load_candidate(project_id, cid)
        return {
            "proposal_id": proposal_id,
            "proposal_status": proposal.status.value,
            "proposal_fingerprint": proposal.proposal_fingerprint,
            "mapspec_revision": proposal.mapspec_revision,
            "candidate_status": candidate.status.value if candidate else "not_started",
            "candidate_attempts": candidate.attempts if candidate else 0,
            "progress": candidate.progress[-8:] if candidate else [],
            "adopted_revision": proposal.adopted_revision,
            "recoverable": True,
        }

    # ── J4 执行隔离候选 ─────────────────────────────────────────────
    async def execute(
        self, db: Session, *, project_id: str, proposal_id: str,
        user_id: str, cancel: bool = False,
        candidate_source_mapspec: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        if proposal.status in (ProposalStatus.ADOPTED, ProposalStatus.DISCARDED):
            raise RefreshError("proposal_terminal", f"proposal is {proposal.status.value}")
        cid = f"g10c-{proposal_id}"
        key = retry_key(proposal, "execute")
        existing = self.store.load_candidate(project_id, cid)
        if existing and existing.retry_key == key and existing.status in (
            CandidateStatus.SUCCEEDED, CandidateStatus.PARTIAL, CandidateStatus.ADOPTED
        ):
            return {"candidate": existing, "replayed": True,
                    "receipt": {"tool_execution": "ok", "task_execution": "ok",
                                "map_observation": "unverified",
                                "version_adoption": "unchanged"}}
        if cancel:
            if existing:
                existing.cancelled = True
                existing.status = CandidateStatus.CANCELLED
                existing.updated_at = time.time()
                existing.last_error = "cancelled by owner"
                await self.store.save_candidate(existing)
            return {"candidate": existing, "replayed": False,
                    "receipt": {"tool_execution": "ok", "task_execution": "failed",
                                "map_observation": "unverified",
                                "version_adoption": "unchanged"}}

        acquired, holder = self.store.acquire_lease(project_id, f"g10p-{proposal_id}-exec")
        if not acquired:
            raise RefreshError("execution_in_progress", str(holder), status=409)
        try:
            facts = await self._facts(
                db, project_id=project_id, session_id=proposal.session_id,
                user_id=user_id,
            )
            drift = verify_no_drift(
                proposal=proposal,
                current_versions=facts["meta"]["source_versions"],
                current_revision=facts["revision"],
                current_mapspec_fingerprint=facts["mapspec_fingerprint"],
                owner_id=user_id,
            )
            if drift:
                proposal.status = ProposalStatus.DRIFTED
                proposal.warnings = ["drift: " + "; ".join(drift)]
                proposal.updated_at = time.time()
                await self.store.save_proposal(proposal)
                raise RefreshError("proposal_drift", "; ".join(drift), status=409)

            baseline_doc = self.store.load_doc(
                project_id, f"g10p-{proposal_id}-baseline"
            ) or {}
            current_doc = facts["mapspec"]
            candidate_src = candidate_source_mapspec or _rebind_sources(
                current_doc, diffs=proposal.diffs,
                source_versions=proposal.source_versions,
            )
            merged = three_way_merge(
                baseline=baseline_doc, current=current_doc,
                candidate=candidate_src,
                removed_fields={
                    d.logical_id: d.fields_removed for d in proposal.diffs
                    if d.fields_removed
                },
                hard_semantic_errors=[
                    f"{d.logical_id}: CRS/unit semantic mismatch"
                    for d in proposal.diffs
                    if d.crs_changed and d.fields_unit_changed
                ],
            )
            doc_path = await self.store.save_candidate_doc(
                project_id, cid, merged.merged_mapspec
            )
            now = time.time()
            candidate = RefreshCandidate(
                candidate_id=cid, proposal_id=proposal_id, project_id=project_id,
                status=(CandidateStatus.SUCCEEDED if merged.adoptable
                        else CandidateStatus.PARTIAL),
                created_at=(existing.created_at if existing else now),
                updated_at=now, attempts=(existing.attempts + 1 if existing else 1),
                retry_key=key, source_version_set=dict(proposal.source_versions),
                progress=[
                    {"stage": "baseline_frozen", "at": now},
                    {"stage": "recompute_plan_verified", "at": now,
                     "nodes": proposal.plan.recompute[:16]},
                    {"stage": "user_wins_merge", "at": now,
                     "preserved": merged.preserved[:16]},
                    {"stage": "candidate_written", "at": now,
                     "namespace": "data-refresh"},
                ],
                completed_nodes=proposal.plan.recompute[:64],
                candidate_doc_path=doc_path,
                candidate_fingerprint=canonical_fingerprint(merged.merged_mapspec),
                baseline_fingerprint=canonical_fingerprint(current_doc),
                merge_preserved=merged.preserved,
                merge_conflicts=[c.model_dump() for c in merged.conflicts],
                hard_semantic_errors=merged.hard_semantic_errors,
                adoptable=merged.adoptable,
                last_error=("" if merged.adoptable else
                            "candidate has unresolved conflicts / hard semantic errors"),
            )
            await self.store.save_candidate(candidate)
            proposal.status = ProposalStatus.READY
            proposal.updated_at = now
            await self.store.save_proposal(proposal)
            return {
                "candidate": candidate, "replayed": False,
                "conflicts": merged.conflicts,
                "preserved": merged.preserved,
                "receipt": {
                    "tool_execution": "ok",
                    "task_execution": "ok" if merged.adoptable else "partial",
                    "map_observation": "unverified",
                    "version_adoption": "unchanged",
                    "note": "candidate is isolated; current visible MapSpec untouched; "
                            "scientific recompute plan delegates to existing Workflow Runtime",
                },
            }
        finally:
            self.store.release_lease(project_id, f"g10p-{proposal_id}-exec")

    # ── J5 候选对比 ─────────────────────────────────────────────────
    async def compare(self, *, project_id: str, proposal_id: str,
                      user_id: str) -> Dict[str, Any]:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        cid = f"g10c-{proposal_id}"
        candidate = self.store.load_candidate(project_id, cid)
        doc = self.store.load_candidate_doc(project_id, cid)
        baseline = self.store.load_doc(project_id, f"g10p-{proposal_id}-baseline") or {}
        return {
            "proposal_id": proposal_id,
            "candidate_status": candidate.status.value if candidate else "not_started",
            "baseline_fingerprint": canonical_fingerprint(baseline),
            "candidate_fingerprint": (candidate.candidate_fingerprint if candidate else ""),
            "candidate_available": isinstance(doc, dict),
            "diff_counts": [
                {"logical_id": d.logical_id, "change_class": d.change_class,
                 "evidence": d.evidence.value,
                 "counts": d.exact_counts.model_dump() if d.exact_counts else None}
                for d in proposal.diffs
            ],
            "recompute": proposal.plan.recompute,
            "reuse": proposal.plan.reuse,
            "preserved_user_edits": (candidate.merge_preserved if candidate
                                     else proposal.plan.preserved_user_edits),
            "conflicts": candidate.merge_conflicts if candidate else [],
            "hard_semantic_errors": candidate.hard_semantic_errors if candidate else [],
            "adoptable": bool(candidate and candidate.adoptable),
            "truncated": proposal.inventory.truncated,
            "warnings": proposal.warnings[:16],
        }

    # ── J6 采纳（原子 CAS） ─────────────────────────────────────────
    async def adopt(self, db: Session, *, project_id: str, proposal_id: str,
                    user_id: str) -> AdoptionReceipt:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        if proposal.status is ProposalStatus.ADOPTED:
            return AdoptionReceipt(
                proposal_id=proposal_id, action="adopt", status="adopted",
                tool_execution="ok", task_execution="ok",
                map_observation="unverified", version_adoption="adopted",
                revision_before=proposal.mapspec_revision,
                revision_after=int(proposal.adopted_revision or proposal.mapspec_revision),
                proposal_fingerprint=proposal.proposal_fingerprint,
                replayed=True,
            )
        cid = f"g10c-{proposal_id}"
        candidate = self.store.load_candidate(project_id, cid)
        if candidate is None or candidate.status in (
            CandidateStatus.FAILED, CandidateStatus.CANCELLED, CandidateStatus.DISCARDED
        ):
            raise RefreshError("candidate_not_ready", "execute the candidate first")
        doc = self.store.load_candidate_doc(project_id, cid)
        if not isinstance(doc, dict):
            raise RefreshError("candidate_doc_missing", status=409)

        acquired, holder = self.store.acquire_lease(project_id, f"g10p-{proposal_id}-adopt")
        if not acquired:
            raise RefreshError("adoption_in_progress", str(holder), status=409)
        try:
            facts = await self._facts(
                db, project_id=project_id, session_id=proposal.session_id,
                user_id=user_id,
            )
            receipt, before_doc = await commit_candidate(
                port=self.port, proposal=proposal, candidate=candidate,
                candidate_doc=doc,
                current_versions=facts["meta"]["source_versions"],
                owner_id=user_id,
            )
            if receipt.status == "adopted":
                # 采纳前 MapSpec 存隔离命名空间，供 forward revert（不倒写 revision）
                await self.store.save_doc(project_id, f"g10c-{proposal_id}-pre-adopt",
                                          before_doc or {})
                proposal.status = ProposalStatus.ADOPTED
                proposal.adopted_revision = receipt.revision_after
                proposal.updated_at = time.time()
                await self.store.save_proposal(proposal)
                candidate.status = CandidateStatus.ADOPTED
                candidate.updated_at = time.time()
                await self.store.save_candidate(candidate)
                # 基线推进到新版本（原子写；不修改不可变快照）
                await self.store.save_baselines(project_id, {
                    "snapshots": {k: v.model_dump(mode="json")
                                  for k, v in facts["current"].items()},
                    "established_at": time.time(),
                    "established_by": user_id,
                    "adopted_proposal": proposal_id,
                })
            elif receipt.status == "blocked":
                proposal.status = ProposalStatus.DRIFTED
                proposal.updated_at = time.time()
                await self.store.save_proposal(proposal)
            return receipt
        finally:
            self.store.release_lease(project_id, f"g10p-{proposal_id}-adopt")

    # ── J7 放弃候选 ─────────────────────────────────────────────────
    async def discard(self, *, project_id: str, proposal_id: str,
                      user_id: str) -> AdoptionReceipt:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        if proposal.status is ProposalStatus.ADOPTED:
            raise RefreshError("already_adopted", "use rollback instead", status=409)
        cid = f"g10c-{proposal_id}"
        candidate = self.store.load_candidate(project_id, cid)
        if candidate:
            candidate.status = CandidateStatus.DISCARDED
            candidate.updated_at = time.time()
            await self.store.save_candidate(candidate)
        released = await self.store.release_candidate_pin(project_id, cid)
        proposal.status = ProposalStatus.DISCARDED
        proposal.updated_at = time.time()
        await self.store.save_proposal(proposal)
        return AdoptionReceipt(
            proposal_id=proposal_id, action="discard", status="discarded",
            tool_execution="ok", task_execution="ok",
            map_observation="unverified", version_adoption="unchanged",
            revision_before=proposal.mapspec_revision,
            revision_after=proposal.mapspec_revision,
            proposal_fingerprint=proposal.proposal_fingerprint,
            warnings=["candidate GC pin released; references/quota now reclaimable"
                      if released else "no candidate pin to release"],
        )

    # ── J8 回退（新 revision，不改不可变快照） ──────────────────────
    async def rollback(self, *, project_id: str, proposal_id: str,
                       user_id: str, db: Optional[Session] = None) -> AdoptionReceipt:
        proposal = self._require_proposal(project_id, proposal_id, user_id)
        baseline_doc = self.store.load_doc(project_id, f"g10c-{proposal_id}-pre-adopt")
        if baseline_doc and self.port is not None:
            receipt = await forward_rollback(
                port=self.port, proposal=proposal,
                baseline_doc=baseline_doc, owner_id=user_id,
            )
            if receipt.status == "rolled_back":
                proposal.status = ProposalStatus.DISCARDED
                proposal.adopted_revision = None
                proposal.updated_at = time.time()
                await self.store.save_proposal(proposal)
            return receipt
        if proposal.baseline_snapshot_id and db is not None:
            # 既有版本恢复接口：创建新 session + ResumeReceipt（不倒写 revision）
            try:
                from app.services.project_runtime.resume import resume_runtime_snapshot
                result = await resume_runtime_snapshot(
                    db, project_id=project_id,
                    snapshot_id=proposal.baseline_snapshot_id,
                    user_id=user_id, allow_stale=True,
                )
                return AdoptionReceipt(
                    proposal_id=proposal_id, action="rollback",
                    status="rolled_back", tool_execution="ok", task_execution="ok",
                    map_observation="unverified", version_adoption="rolled_back",
                    revision_before=proposal.mapspec_revision,
                    revision_after=int(getattr(result, "mapspec_mutation_revision", 0) or 1),
                    restored_snapshot_id=proposal.baseline_snapshot_id,
                    warnings=["restored via existing runtime snapshot resume into a NEW session",
                              "map must be re-observed after rollback"],
                )
            except Exception as exc:  # noqa: BLE001
                raise RefreshError("rollback_failed", str(exc)[:200], status=409) from exc
        raise RefreshError("rollback_unavailable",
                           "no pre-adopt baseline document and no runtime snapshot",
                           status=409)


__all__ = ["ProjectDataRefreshService", "RefreshError", "InMemoryMapSpecPort"]
