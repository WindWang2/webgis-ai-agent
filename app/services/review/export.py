"""审查记录导出（ADR-0203）：allowlist 投影，不泄露 secret / CoT。

纪律：导出走**字段 allowlist**（不是 blocklist）—— 新增的未知字段默认
不出现在导出面；actor 只暴露 id/kind/role（不存也不导 token/凭据）；
proposal 本就无 CoT 字段，这里再以 allowlist 双保险。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.schemas.review_schema import ReviewProposal

_ACTOR_FIELDS = ("actor_id", "actor_kind", "role")
_ANCHOR_FIELDS = ("kind", "id", "layer_id")
_COMMENT_FIELDS = ("comment_id", "author", "body", "anchor", "created_at")
_DECISION_FIELDS = (
    "decision_id", "decision", "actor", "reason",
    "base_revision_at_decision", "created_at", "counted",
)
_INTENT_FIELDS = ("intent", "expected_revision", "layer_id", "component_id",
                  "new_id", "chart_ref", "table_ref", "paint", "visible",
                  "opacity", "enabled", "position", "placement", "variant",
                  "style", "options", "upsert", "center", "zoom", "pitch",
                  "bearing", "layer_ids", "legend", "controls", "margins",
                  "components", "doc", "base_workbench_revision", "delta",
                  "field", "type", "extent", "current", "window", "playback",
                  "step", "speed", "view")
_MERGE_FIELDS = (
    "merged_at", "actor", "proposal_id", "base_revision", "merged_revision",
    "checkpoint_id", "applied", "rolled_back", "failure", "interleaved",
    "mutation_ids", "approvals_considered", "policy_snapshot",
)
_PROPOSAL_FIELDS = (
    "proposal_id", "session_id", "title", "description", "author",
    "base_revision", "mutation_intents", "status", "risk", "comments",
    "decisions", "merge_evidence", "created_at", "updated_at",
)


def _pick(obj: Any, fields: tuple) -> Dict[str, Any]:
    if obj is None:
        return {}
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else dict(obj)
    return {k: data[k] for k in fields if k in data}


def _actor(a: Any) -> Dict[str, Any]:
    return _pick(a, _ACTOR_FIELDS)


def _anchor(a: Any) -> Dict[str, Any]:
    return _pick(a, _ANCHOR_FIELDS)


def export_proposals(proposals: List[ReviewProposal]) -> List[Dict[str, Any]]:
    """allowlist 审计投影（顺序稳定，可 diff）。"""
    out: List[Dict[str, Any]] = []
    for p in proposals:
        intents = []
        for body in p.mutation_intents:
            data = body.model_dump(mode="json", exclude_none=True)
            intents.append({k: data[k] for k in _INTENT_FIELDS if k in data})
        entry: Dict[str, Any] = {
            "proposal_id": p.proposal_id,
            "session_id": p.session_id,
            "title": p.title,
            "description": p.description,
            "author": _actor(p.author),
            "base_revision": p.base_revision,
            "status": p.status.value,
            "risk": p.risk.value if p.risk else None,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "mutation_intents": intents,
            "comments": [
                {
                    **_pick(c, _COMMENT_FIELDS),
                    "author": _actor(c.author),
                    **({"anchor": _anchor(c.anchor)} if c.anchor else {}),
                }
                for c in p.comments
            ],
            "decisions": [
                {**_pick(d, _DECISION_FIELDS), "actor": _actor(d.actor)}
                for d in p.decisions
            ],
            "merge_evidence": (
                {**_pick(p.merge_evidence, _MERGE_FIELDS),
                 "actor": _actor(p.merge_evidence.actor)}
                if p.merge_evidence else None
            ),
        }
        out.append(entry)
    return out


def export_session(proposals: List[ReviewProposal], session_id: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "proposal_count": len(proposals),
        "proposals": export_proposals(proposals),
    }
