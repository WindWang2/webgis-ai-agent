"""导出投影单测：allowlist 语义（未知字段默认不导出）。"""
from __future__ import annotations

from app.schemas.mapspec_mutation_schema import PatchLayerStyleBody
from app.schemas.review_schema import ProposalStatus, ReviewActor, ReviewProposal
from app.services.review.export import export_proposals


def _actor(uid="u1", kind="user", role="editor"):
    return ReviewActor(actor_id=uid, actor_kind=kind, role=role)


def test_allowlist_export_shape():
    p = ReviewProposal(
        proposal_id="rp_x", session_id="s1", title="t", author=_actor(),
        base_revision=3,
        mutation_intents=[
            PatchLayerStyleBody(
                intent="patch_layer_style", expected_revision=3,
                layer_id="L1", paint={"circle-color": "#0f0"},
            ),
        ],
        status=ProposalStatus.SUBMITTED,
    )
    out = export_proposals([p])
    assert len(out) == 1
    entry = out[0]
    # allowlist 顶层键
    assert set(entry.keys()) <= {
        "proposal_id", "session_id", "title", "description", "author",
        "base_revision", "status", "risk", "created_at", "updated_at",
        "mutation_intents", "comments", "decisions", "merge_evidence",
    }
    # intent 只保留白名单键（client_mutation_id 等不入导出面）
    intent_keys = set(entry["mutation_intents"][0].keys())
    assert "client_mutation_id" not in intent_keys
    assert intent_keys <= {"intent", "expected_revision", "layer_id", "paint"}
    # 无 CoT/secret 字段名
    assert "reasoning" not in entry and "chain_of_thought" not in entry
    # author 只含身份三元组（无 token/凭据面）
    assert set(entry["author"].keys()) == {"actor_id", "actor_kind", "role"}


def test_export_stable_order_and_status():
    p1 = ReviewProposal(
        proposal_id="rp_1", session_id="s1", title="a", author=_actor(),
        base_revision=0,
        mutation_intents=[PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=0,
            layer_id="L", paint={"x": "1"},
        )],
    )
    p2 = ReviewProposal(
        proposal_id="rp_2", session_id="s1", title="b", author=_actor("u2"),
        base_revision=1, status=ProposalStatus.MERGED,
        mutation_intents=[PatchLayerStyleBody(
            intent="patch_layer_style", expected_revision=1,
            layer_id="L", paint={"x": "2"},
        )],
    )
    out = export_proposals([p1, p2])
    assert [e["proposal_id"] for e in out] == ["rp_1", "rp_2"]
    assert out[1]["status"] == "merged"
