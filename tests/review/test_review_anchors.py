"""锚点 stale 检测测试（诚实语义：未知 ≠ stale）。"""
from __future__ import annotations

import pytest

from app.schemas.review_schema import Anchor, AnchorKind
from app.services.review.anchors import (
    AnchorState,
    evaluate_anchor,
    evaluate_anchors,
    proposal_anchor_states,
)


def _spec(layers=None, sources=None, components=None):
    spec = {
        "version": "1.0",
        "layers": list(layers or []),
        "sources": dict(sources or {}),
        "view": {},
        "layout": {},
    }
    if components is not None:
        spec["layout"]["components"] = list(components)
    return spec


def _geojson_source(feature_ids):
    return {
        "type": "geojson",
        "data": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": fid, "properties": {}, "geometry": None}
                for fid in feature_ids
            ],
        },
    }


class TestLayerAnchor:
    async def test_present_ok(self):
        spec = _spec(layers=[{"id": "L1"}])
        a = Anchor(kind=AnchorKind.LAYER, id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.OK

    async def test_removed_stale(self):
        spec = _spec(layers=[])
        a = Anchor(kind=AnchorKind.LAYER, id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.STALE


class TestComponentAnchor:
    async def test_present_ok(self):
        spec = _spec(components=[{"id": "legend-1"}])
        a = Anchor(kind=AnchorKind.COMPONENT, id="legend-1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.OK

    async def test_removed_stale(self):
        spec = _spec(components=[])
        a = Anchor(kind=AnchorKind.COMPONENT, id="legend-1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.STALE


class TestFeatureAnchor:
    async def test_feature_present(self):
        spec = _spec(
            layers=[{"id": "L1", "source": "s1"}],
            sources={"s1": _geojson_source(["f-1", "f-2"])},
        )
        a = Anchor(kind=AnchorKind.FEATURE, id="f-1", layer_id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.OK

    async def test_feature_gone_stale(self):
        spec = _spec(
            layers=[{"id": "L1", "source": "s1"}],
            sources={"s1": _geojson_source(["f-2"])},
        )
        a = Anchor(kind=AnchorKind.FEATURE, id="f-1", layer_id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.STALE

    async def test_parent_layer_gone_stale(self):
        spec = _spec(layers=[], sources={})
        a = Anchor(kind=AnchorKind.FEATURE, id="f-1", layer_id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.STALE

    async def test_unresolvable_source_unverified(self):
        """source 是 ref（数据不在 spec 内联）→ unverified（未知≠stale）。"""
        spec = _spec(
            layers=[{"id": "L1", "source": "ref:abc123"}],
            sources={},
        )
        a = Anchor(kind=AnchorKind.FEATURE, id="f-1", layer_id="L1")
        assert await evaluate_anchor("s1", a, spec) is AnchorState.UNVERIFIED


class TestClaimAnchor:
    async def test_claim_present_ok(self, monkeypatch):
        from app.services.review import anchors as anchors_mod

        class _FakeStore:
            def get_claim(self, cid):
                return object() if cid == "claim-1" else None

        monkeypatch.setattr(
            anchors_mod, "_lookup_claim_store", lambda sid: _FakeStore(),
        )
        a = Anchor(kind=AnchorKind.CLAIM, id="claim-1")
        assert await evaluate_anchor("s1", a, None) is AnchorState.OK

    async def test_claim_missing_stale(self, monkeypatch):
        from app.services.review import anchors as anchors_mod

        class _FakeStore:
            def __init__(self):
                self.seen = False

            def get_claim(self, cid):
                return None

            def all_claims(self):
                return [object()]

        monkeypatch.setattr(
            anchors_mod, "_lookup_claim_store", lambda sid: _FakeStore(),
        )
        a = Anchor(kind=AnchorKind.CLAIM, id="claim-1")
        assert await evaluate_anchor("s1", a, None) is AnchorState.STALE

    async def test_claim_store_unreachable_unverified(self, monkeypatch):
        from app.services.review import anchors as anchors_mod

        monkeypatch.setattr(anchors_mod, "_lookup_claim_store", lambda sid: None)
        a = Anchor(kind=AnchorKind.CLAIM, id="claim-1")
        assert await evaluate_anchor("s1", a, None) is AnchorState.UNVERIFIED


class TestArtifactAnchor:
    async def test_artifact_syntactic_unverified(self):
        """artifact 活性校验 v1 不接线注册表 → unverified（记录边界）。"""
        a = Anchor(kind=AnchorKind.ARTIFACT, id="artifact-xyz")
        assert await evaluate_anchor("s1", a, None) is AnchorState.UNVERIFIED


class TestBatch:
    async def test_evaluate_anchors_mixed(self):
        spec = _spec(layers=[{"id": "L1"}])
        anchors = [
            Anchor(kind=AnchorKind.LAYER, id="L1"),
            Anchor(kind=AnchorKind.LAYER, id="L-gone"),
        ]
        states = await evaluate_anchors("s1", anchors, spec)
        assert states == [AnchorState.OK, AnchorState.STALE]

    async def test_proposal_anchor_states_ignores_unanchored(self):
        from datetime import datetime, timezone

        from app.schemas.mapspec_mutation_schema import PatchLayerStyleBody
        from app.schemas.review_schema import (
            AnchoredComment,
            ReviewActor,
            ReviewProposal,
        )

        spec = _spec(layers=[{"id": "L1"}])
        p = ReviewProposal(
            proposal_id="rp_1", session_id="s1", title="t",
            author=ReviewActor(actor_id="u1", actor_kind="user", role="editor"),
            base_revision=1,
            mutation_intents=[
                PatchLayerStyleBody(
                    intent="patch_layer_style", expected_revision=1,
                    layer_id="L1", paint={"circle-color": "#0f0"},
                ),
            ],
            comments=[
                AnchoredComment(
                    comment_id="rc_1",
                    author=ReviewActor(actor_id="u2", actor_kind="user", role="viewer"),
                    body="see this layer",
                    anchor=Anchor(kind=AnchorKind.LAYER, id="L1"),
                    created_at=datetime.now(timezone.utc).isoformat(),
                ),
                AnchoredComment(
                    comment_id="rc_2",
                    author=ReviewActor(actor_id="u2", actor_kind="user", role="viewer"),
                    body="general note",
                    anchor=None,
                    created_at=datetime.now(timezone.utc).isoformat(),
                ),
            ],
        )
        states = await proposal_anchor_states("s1", p, spec)
        assert states == {"rc_1": AnchorState.OK}
