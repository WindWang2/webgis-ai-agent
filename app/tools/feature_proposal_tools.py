"""feature-proposals Agent 工具面（G32 WP09）。

四个工具：起草 / 列表·详情 / 差异·冲突检查 / rebase 预览。
**没有 decision、merge、resolutions 工具** —— 几何/结构提案的批准是
owner 在 UI/REST 的人工动作（actor 恒由服务端 auth 裁决），Agent 不能以
`actor=user`、approval 参数或任意函数调用批准自己的提案（P23）。

返回纪律：紧凑对象（proposal_id/status/revision/digest/计数/refs/next_action）；
不把候选全量数据、完整几何或字段快照塞进 LLM 上下文 —— 大载荷经 diff
分页端点按需获取。

注：本模块**不用** `from __future__ import annotations` —— registry 的
_generate_model 直接消费 inspect 注解对象，字符串化注解会破坏 pydantic
模型生成（与 feature_edit_tools 同约束）。
"""
import logging
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def register_feature_proposal_tools(registry: ToolRegistry) -> None:
    """注册 G32 提案工具族（tier 2；create 为 state_mutation 面）。"""

    @tool(
        registry,
        name="vector_change_proposal_create",
        description=(
            "Draft a persistent feature-change proposal on an editable vector dataset ref. "
            "The proposal freezes the current revision as its base evidence; the live data is "
            "NOT modified. The owner reviews field/geometry diffs and conflicts later in the "
            "proposal UI and approves explicitly. Ops vocabulary matches vector_feature_edit_preview "
            "(set_fields/move_by/replace_geometry/create_features/delete_features/split_feature/"
            "merge_features/snap_to_ref/repair_geometry); each op may carry `wfids` to target "
            "specific features. Use feature_ids for plan-level targets (merge parents). "
            "rename_field is not proposable (use the immediate edit path)."
        ),
        tier=2,
        domains=["dataset"],
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        capabilities=["dataset_ingest"],
        latency_class="medium",
        param_descriptions={
            "ref_id": "target editable dataset ref (ref:...)",
            "ops": "edit ops; each op may include wfids to restrict its targets",
            "feature_ids": "plan-level target ids (merge parents / shared targets)",
            "title": "optional human-readable title",
            "submit": "true to submit for review immediately (default false = draft)",
        },
    )
    async def vector_change_proposal_create(
        session_id: Optional[str],
        ref_id: str,
        ops: List[Dict[str, Any]],
        feature_ids: Optional[List[str]] = None,
        title: Optional[str] = None,
        submit: bool = True,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION",
                    "message": "session_id is required"}
        from app.services.feature_proposals import service as fp_service
        from app.services.feature_proposals.contracts import (
            FeatureProposalBlocked,
            FeatureProposalConflict,
            FeatureProposalError,
        )

        try:
            record = await fp_service.create_proposal(
                session_id,
                ref_id=ref_id,
                ops=ops,
                feature_ids=feature_ids,
                title=title,
                author_kind="agent",
                author_id="pi-agent",
                submit=submit,
            )
        except FeatureProposalBlocked as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message,
                    "detail": exc.detail}
        except FeatureProposalConflict as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message,
                    "detail": exc.detail}
        except FeatureProposalError as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message}
        return {
            "success": True,
            "proposal_id": record.proposal_id,
            "status": record.status,
            "revision": record.revision,
            "candidate_digest": record.candidate_digest,
            "base_revision": record.base.content_revision,
            "ops_count": len(record.ops),
            "ref_id": record.ref_id,
            "next_action": (
                "owner reviews the diff/conflicts in the feature-proposals drawer; "
                "approval and merge happen there — the agent cannot approve its own proposal"
            ),
        }

    @tool(
        registry,
        name="vector_change_proposal_list",
        description=(
            "List persisted feature-change proposals for the session (optionally by status), "
            "or fetch one proposal's detail by proposal_id. Returns compact summaries: "
            "status, revision, digest, base evidence, op kinds, approval binding state."
        ),
        tier=2,
        domains=["dataset"],
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        capabilities=["dataset_ingest"],
        latency_class="fast",
        param_descriptions={
            "status": "optional status filter (draft/submitted/approved/merging/merged/...)",
            "proposal_id": "optional — return this proposal's detail instead of a list",
        },
    )
    async def vector_change_proposal_list(
        session_id: Optional[str],
        status: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION",
                    "message": "session_id is required"}
        from app.services.feature_proposals import service as fp_service
        from app.services.feature_proposals.contracts import FeatureProposalError

        try:
            if proposal_id:
                detail = await fp_service.get_proposal_detail(session_id, proposal_id)
                return {"success": True, "proposal": detail}
            listing = await fp_service.list_proposals(session_id, status=status, limit=20)
            return {
                "success": True,
                "total": listing["total"],
                "items": [
                    {k: item[k] for k in (
                        "proposal_id", "ref_id", "title", "status", "revision",
                        "author_kind", "ops_count", "op_kinds", "has_approval", "updated_at",
                    )}
                    for item in listing["items"]
                ],
            }
        except FeatureProposalError as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message}

    @tool(
        registry,
        name="vector_change_proposal_diff",
        description=(
            "Inspect a persisted feature-change proposal: candidate deltas (paged) and the "
            "three-way conflict report against the CURRENT data (field/geometry conflicts, "
            "satisfied changes, independent current changes). Geometry is summarized as "
            "digests; use the REST diff endpoint for full geometry when needed."
        ),
        tier=2,
        domains=["dataset"],
        side_effect="cacheable_read",
        network=False,
        deterministic=False,
        capabilities=["dataset_ingest"],
        latency_class="medium",
        param_descriptions={
            "proposal_id": "proposal to inspect",
            "page": "delta page (1-based, default 1)",
        },
    )
    async def vector_change_proposal_diff(
        session_id: Optional[str],
        proposal_id: str,
        page: int = 1,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION",
                    "message": "session_id is required"}
        from app.services.feature_proposals import service as fp_service
        from app.services.feature_proposals.contracts import FeatureProposalError

        try:
            diff = await fp_service.get_diff(
                session_id, proposal_id, page=page, page_size=20,
            )
        except FeatureProposalError as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message}
        report = diff.get("conflict_report") or {}
        return {
            "success": True,
            "proposal_id": proposal_id,
            "revision": diff.get("revision"),
            "verdict": report.get("verdict"),
            "conflict_count": len(report.get("unresolved_conflicts") or []),
            "conflict_kinds": sorted({
                str(c.get("kind")) for c in (report.get("unresolved_conflicts") or [])
            }),
            "satisfied_count": len(report.get("satisfied") or []),
            "independent_current_changes": report.get("independent_current_changes"),
            "candidate_delta_count": diff.get("candidate_delta_count"),
            "delta_page": [
                {"wfid": d.get("wfid"), "presence": d.get("presence"),
                 "fields": sorted((d.get("fields") or {}).keys())}
                for d in (diff.get("candidate_deltas") or [])
            ],
            "next_action": (
                "if verdict != clean, propose resolutions to the owner (keep_current / "
                "take_proposed / use_new_value); approval happens in the UI"
            ),
        }

    @tool(
        registry,
        name="vector_change_proposal_rebase_preview",
        description=(
            "Preview a rebase of a proposal onto the current data revision: what remains "
            "conflicting, which resolutions are still needed. This is a read-only preview — "
            "applying the rebase (and everything that changes candidate content) is an owner "
            "REST action because it invalidates prior approvals."
        ),
        tier=2,
        domains=["dataset"],
        side_effect="cacheable_read",
        network=False,
        deterministic=False,
        capabilities=["dataset_ingest"],
        latency_class="medium",
        param_descriptions={
            "proposal_id": "proposal to preview a rebase for",
        },
    )
    async def vector_change_proposal_rebase_preview(
        session_id: Optional[str],
        proposal_id: str,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION",
                    "message": "session_id is required"}
        from app.services.feature_proposals import service as fp_service
        from app.services.feature_proposals.contracts import (
            FeatureProposalBlocked,
            FeatureProposalConflict,
            FeatureProposalError,
        )

        try:
            current = await fp_service._get_or_404(session_id, proposal_id)
            preview = await fp_service.rebase_proposal(
                session_id, proposal_id,
                expected_revision=current.revision, apply=False,
                actor_kind="agent", actor_id="pi-agent",
            )
        except (FeatureProposalBlocked, FeatureProposalConflict) as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message,
                    "detail": exc.detail}
        except FeatureProposalError as exc:
            return {"success": False, "code": exc.code.upper(), "message": exc.message}
        return {
            "success": True,
            "proposal_id": proposal_id,
            "verdict": preview.get("verdict"),
            "remaining_conflicts": [
                {"conflict_id": c.get("conflict_id"), "kind": c.get("kind"),
                 "wfid": c.get("wfid"), "field": c.get("field")}
                for c in (preview.get("remaining_conflicts") or [])[:16]
            ],
            "resolutions_needed": preview.get("resolutions_needed", [])[:16],
            "note": preview.get("note"),
        }
