"""ArtifactLedger 账本与 CartographerAgent 出券/修复边界（ADR-0189 D1/D3）。

账本是 Zero Big Data in Context 的进程内取货位：有界（FIFO 逐出）、
同 ref 覆盖 = 原地修订。Cartographer 的 fail-loud 输入校验、诚实降级
（数值不足不出分级）、revise 前置条件（无账本/券不可达均 typed 失败）。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.agent_swarm.base import SpecialistTimeoutError
from app.services.agent_swarm.contracts import MapSpecDeliveryRef
from app.services.agent_swarm.specialists.cartographer import (
    CartographerAgent,
    _walk_coordinates,
)
from app.services.agent_swarm.specialists.ledger import (
    ArtifactLedger,
    payload_digest,
)


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _square(lng: float, lat: float) -> List[List[float]]:
    d = 0.02
    return [
        [lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat],
    ]


def _geojson(values: List[float], field: str = "schools") -> Dict[str, Any]:
    features = []
    for i, v in enumerate(values):
        features.append({
            "type": "Feature",
            "properties": {field: v, "district": f"d{i}"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [_square(104.0 + i * 0.05, 30.5 + i * 0.05)],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _request(values: List[float], **overrides: Any) -> Dict[str, Any]:
    req = {
        "geojson": _geojson(values),
        "field": "schools",
        "title": "测试专题图",
        "purpose": "screen_16_9",
        "source_id": "districts",
    }
    req.update(overrides)
    return req


_MODERATE = [10, 14, 18, 25, 30, 40, 52, 68, 90, 120]


class TestArtifactLedger:
    """ref→payload 有界账本。"""

    def test_allocate_ref_sequence(self):
        ledger = ArtifactLedger()
        assert ledger.allocate_ref("ref:mapspec") == "ref:mapspec-000001"
        assert ledger.allocate_ref("ref:mapspec") == "ref:mapspec-000002"
        assert ledger.allocate_ref("ref:audit") == "ref:audit-000003"

    def test_put_get_contains(self):
        ledger = ArtifactLedger()
        ref = ledger.allocate_ref("ref:mapspec")
        ledger.put(ref, {"v": 1})
        assert ledger.get(ref) == {"v": 1}
        assert ledger.contains(ref) is True
        assert len(ledger) == 1

    def test_get_missing_returns_none(self):
        ledger = ArtifactLedger()
        assert ledger.get("ref:mapspec-999999") is None
        assert ledger.contains("ref:mapspec-999999") is False

    def test_same_ref_overwrite_no_new_slot(self):
        ledger = ArtifactLedger()
        ref = ledger.allocate_ref("ref:mapspec")
        ledger.put(ref, {"v": 1})
        ledger.put(ref, {"v": 2})  # 原地修订
        assert len(ledger) == 1
        assert ledger.get(ref) == {"v": 2}

    def test_fifo_eviction_at_capacity(self):
        ledger = ArtifactLedger(max_entries=3)
        refs = [ledger.allocate_ref("ref:mapspec") for _ in range(4)]
        for i, ref in enumerate(refs):
            ledger.put(ref, {"i": i})
        assert len(ledger) == 3
        assert ledger.get(refs[0]) is None  # 最旧逐出
        assert ledger.get(refs[3]) == {"i": 3}

    def test_overwrite_refreshes_recency(self):
        ledger = ArtifactLedger(max_entries=2)
        r1 = ledger.allocate_ref("ref:mapspec")
        r2 = ledger.allocate_ref("ref:mapspec")
        r3 = ledger.allocate_ref("ref:mapspec")
        ledger.put(r1, {"i": 1})
        ledger.put(r2, {"i": 2})
        ledger.put(r1, {"i": 11})  # r1 刷新为最新
        ledger.put(r3, {"i": 3})  # 逐出最旧 = r2
        assert ledger.get(r2) is None
        assert ledger.get(r1) == {"i": 11}
        assert ledger.get(r3) == {"i": 3}

    def test_min_capacity_is_one(self):
        ledger = ArtifactLedger(max_entries=0)
        assert ledger._max_entries == 1

    def test_thread_safe_concurrent_puts(self):
        import threading

        ledger = ArtifactLedger(max_entries=64)
        errors: list = []

        def _worker(worker_id: int) -> None:
            try:
                for i in range(50):
                    ref = ledger.allocate_ref("ref:mapspec")
                    ledger.put(ref, {"worker": worker_id, "i": i})
            except Exception as exc:  # pragma: no cover - 防御
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(ledger) == 64  # 有界保持

    def test_payload_digest_deterministic(self):
        payload = {"a": 1, "b": [1, 2, 3], "c": "中文"}
        assert payload_digest(payload) == payload_digest({"c": "中文", "b": [1, 2, 3], "a": 1})

    def test_payload_digest_key_order_independent(self):
        assert payload_digest({"x": 1, "y": 2}) == payload_digest({"y": 2, "x": 1})

    def test_payload_digest_sensitive_to_content(self):
        assert payload_digest({"a": 1}) != payload_digest({"a": 2})

    def test_payload_digest_length_twelve(self):
        assert len(payload_digest({"a": 1})) == 12


class TestComposeInputGuards:
    """compose 的 fail-loud 输入校验。"""

    def test_missing_geojson_rejected(self):
        agent = CartographerAgent()
        with pytest.raises(ValueError, match="缺少 geojson.features"):
            agent.compose({"field": "schools", "title": "t"})

    def test_geojson_without_features_list_rejected(self):
        agent = CartographerAgent()
        with pytest.raises(ValueError, match="缺少 geojson.features"):
            agent.compose({"geojson": {"type": "Point"}, "field": "f", "title": "t"})

    def test_missing_field_rejected(self):
        agent = CartographerAgent()
        with pytest.raises(ValueError, match="缺少 field / title"):
            agent.compose({"geojson": _geojson([1, 2]), "title": "t"})

    def test_missing_title_rejected(self):
        agent = CartographerAgent()
        with pytest.raises(ValueError, match="缺少 field / title"):
            agent.compose({"geojson": _geojson([1, 2]), "field": "schools"})

    def test_unmapped_purpose_warns_and_falls_back(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        ref = agent.compose(_request(_MODERATE, purpose="holodeck_42"))
        assert any("purpose_unmapped" in w for w in ref.warnings)
        payload = ledger.get(ref.ref_id)
        assert payload["layout"]["purpose"] == "screen_16_9"


class TestComposeHonestDegradation:
    """无数值/类别证据 → 诚实降级（不出分级、常量底色、审计红线兜底）。"""

    def test_empty_features_warns_and_uses_fallback_color(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        ref = agent.compose(_request([], field="schools"))
        assert any("classification_unavailable" in w for w in ref.warnings)
        payload = ledger.get(ref.ref_id)
        layer = payload["layers"][0]
        assert layer["paint"]["fill-color"] == "#cccccc"
        assert "legend_spec" not in layer

    def test_classification_absent_from_ref(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        ref = agent.compose(_request([], field="schools"))
        assert ref.classification == {}

    def test_no_ledger_means_no_ref_id(self):
        agent = CartographerAgent()  # 未绑账本
        ref = agent.compose(_request(_MODERATE))
        assert ref.ref_id is None
        assert ref.mapspec_fingerprint  # 指纹仍可计算

    def test_delivery_ref_bounded(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        ref = agent.compose(_request(_MODERATE))
        assert len(ref.summary) <= 600
        assert len(ref.warnings) <= 8
        assert len(ref.components) <= 12
        assert ref.layer_count == 1

    def test_categorical_field_builds_legend_with_collapse(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        request = _request(_MODERATE, field="district", title="类别专题图")
        ref = agent.compose(request)
        assert ref.classification["method"] == "categorical"
        # 10 类超过 MAX_CATEGORICAL_CLASSES(8) → 收纳（top-N + Other）
        assert ref.classification["k"] == 8
        assert any("category_collapse" in w for w in ref.warnings)
        payload = ledger.get(ref.ref_id)
        layer = payload["layers"][0]
        assert isinstance(layer["legend_spec"], dict)

    def test_bbox_from_geometry_used_for_center(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        ref = agent.compose(_request(_MODERATE))
        payload = ledger.get(ref.ref_id)
        center = payload["view"]["center"]
        assert 104.0 <= center[0] <= 104.6
        assert 30.5 <= center[1] <= 31.2

    def test_recipe_hint_miss_disclosed(self):
        class _EmptyRegistry:
            def get(self, hint: str):
                return None

            def keyword_hits(self, hint: str):
                return []

        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger, recipe_registry=_EmptyRegistry())
        ref = agent.compose(_request(_MODERATE, recipe_hint="flood-risk"))
        assert any("recipe_miss" in w for w in ref.warnings)

    def test_recipe_hit_disclosed_as_prior_only(self):
        class _Recipe:
            id = "recipe-42"
            primary_cartography = "choropleth"

        class _Registry:
            def get(self, hint: str):
                return _Recipe()

            def keyword_hits(self, hint: str):
                return []

        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger, recipe_registry=_Registry())
        ref = agent.compose(_request(_MODERATE, recipe_hint="flood-risk"))
        assert any("recipe_prior" in w and "recipe-42" in w for w in ref.warnings)


class TestReviseGuards:
    """revise 前置条件：账本绑定 + 券可达（fail-closed）。"""

    def test_revise_without_ledger_rejected(self):
        agent = CartographerAgent()
        delivery = MapSpecDeliveryRef(ref_id="ref:mapspec-000001")
        with pytest.raises(ValueError, match="revise 需要 ArtifactLedger"):
            agent.revise(delivery)

    def test_revise_unreachable_ref_rejected(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        delivery = MapSpecDeliveryRef(ref_id="ref:mapspec-999999")
        with pytest.raises(ValueError, match="revise 目标不可达"):
            agent.revise(delivery)

    def test_revise_increments_revision_and_emits_new_ref(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        first = agent.compose(_request(_MODERATE))
        second = agent.revise(first)
        assert second.revision == first.revision + 1
        assert second.ref_id != first.ref_id
        assert ledger.get(second.ref_id) is not None

    def test_revise_keeps_classification(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        first = agent.compose(_request(_MODERATE))
        second = agent.revise(first)
        assert second.classification == first.classification

    def test_revise_no_op_disclosed_when_nothing_repairable(self):
        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger)
        first = agent.compose(_request(_MODERATE))
        second = agent.revise(first)
        # 干净交付无审计建议 → 无可安全自动执行的修复（诚实披露）
        assert any("revise_no_op" in w for w in second.warnings)


class TestEmitRefBounds:
    """_emit_ref 的警告有界与 truncated 诚实标注。"""

    def test_warnings_over_eight_truncates(self):
        agent = CartographerAgent()
        ref = agent._emit_ref(
            {"layers": [], "layout": {}},
            {"method": "test"},
            [f"warning-{i}" for i in range(12)],
            revision=0,
        )
        assert len(ref.warnings) == 8
        assert ref.truncated is True

    def test_warnings_within_eight_not_truncated(self):
        agent = CartographerAgent()
        ref = agent._emit_ref(
            {"layers": [], "layout": {}},
            {"method": "test"},
            [f"warning-{i}" for i in range(3)],
            revision=0,
        )
        assert len(ref.warnings) == 3
        assert ref.truncated is False

    def test_individual_warning_clipped(self):
        agent = CartographerAgent()
        ref = agent._emit_ref(
            {"layers": [], "layout": {}},
            {},
            ["w" * 500],
            revision=0,
        )
        assert len(ref.warnings[0]) == 200  # _MAX_DELIVERY_WARNING_LEN


class TestWalkCoordinates:
    """几何坐标递归收集（Point/Line/Polygon/Multi 通吃）。"""

    def test_point(self):
        assert _walk_coordinates({"coordinates": [1.0, 2.0]}) == [(1.0, 2.0)]

    def test_polygon(self):
        coords = _walk_coordinates({"coordinates": [_square(0.0, 0.0)]})
        assert len(coords) == 5
        assert coords[0] == (0.0, 0.0)

    def test_multipolygon_nested(self):
        geom = {"coordinates": [[[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0]]]}
        assert _walk_coordinates(geom) == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]

    def test_empty_geometry(self):
        assert _walk_coordinates({}) == []
        assert _walk_coordinates({"coordinates": None}) == []

    def test_non_numeric_leaves_ignored(self):
        assert _walk_coordinates({"coordinates": ["a", "b"]}) == []


class TestCartographerDeadline:
    def test_default_deadline_from_role(self):
        agent = CartographerAgent()
        assert agent._deadline_s == 240.0  # cartography_specialist 档

    def test_deadline_breach_between_stages_raises(self):
        """时钟在心跳之间快进（长任务僵死面）→ check_deadline typed 熔断。"""

        class _JumpingClock(FakeClock):
            def __call__(self) -> float:
                self.now += 10.0  # 每次读时钟都快进 10s
                return self.now

        ledger = ArtifactLedger()
        agent = CartographerAgent(ledger=ledger, clock=_JumpingClock(), deadline_s=5.0)
        with pytest.raises(SpecialistTimeoutError):
            agent.compose(_request(_MODERATE))
