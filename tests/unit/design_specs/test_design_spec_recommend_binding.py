"""WP2 推荐 + 绑定校验测试（确定性 / 分母纪律 / 负例 / not_evaluated 诚实）。"""
from __future__ import annotations

import pytest

from app.services.design_specs.recommend import recommend_design


class TestDenominatorDiscipline:
    def test_density_without_denominator_is_blocked_with_clarification(self):
        result = recommend_design({
            "purposes": ["distribution"],
            "geometry_kind": "point",
            "statistic": "density",
            "denominator": {"available": False},
        })
        codes = [c["code"] for c in result["clarifications"]]
        assert "DENOMINATOR_MISSING" in codes
        # 密度族模型全部出现在 unavailable 且带证据
        unavailable_ids = {u["model_id"] for u in result["model_unavailable"]}
        assert any("density" in m or "heatmap" in m for m in unavailable_ids)
        # 密度族不进候选
        candidate_ids = {c["model_id"] for c in result["model_candidates"]}
        assert not any(
            "density" in m or "heatmap" in m for m in candidate_ids
        ), candidate_ids

    def test_density_with_denominator_recommended(self):
        result = recommend_design({
            "purposes": ["distribution"],
            "geometry_kind": "point",
            "statistic": "density",
            "denominator": {"available": True, "field": "population"},
        })
        codes = [c["code"] for c in result["clarifications"]]
        assert "DENOMINATOR_MISSING" not in codes

    def test_count_statistic_never_triggers_denominator_gate(self):
        result = recommend_design({
            "purposes": ["distribution"],
            "geometry_kind": "point",
            "statistic": "count",
        })
        assert not any(
            c["code"] == "DENOMINATOR_MISSING" for c in result["clarifications"]
        )


class TestCandidateDiscipline:
    def test_unknown_vocabulary_surfaced_as_clarification(self):
        result = recommend_design({
            "purposes": ["要看哪里的热力"],  # 非法
            "geometry_kind": "cloud",
            "statistic": "magic",
        })
        codes = {c["code"] for c in result["clarifications"]}
        assert {"UNKNOWN_PURPOSE", "UNKNOWN_GEOMETRY", "UNKNOWN_STATISTIC"} <= codes

    def test_deterministic_ordering(self):
        facts = {"purposes": ["flow"], "geometry_kind": "line", "statistic": "count"}
        r1 = recommend_design(facts)
        r2 = recommend_design(facts)
        assert [c["model_id"] for c in r1["model_candidates"]] == \
               [c["model_id"] for c in r2["model_candidates"]]

    def test_flow_purpose_ranks_flow_models_first(self):
        result = recommend_design({
            "purposes": ["flow"], "geometry_kind": "line", "statistic": "count",
        })
        candidates = result["model_candidates"]
        if candidates:
            assert any("flow" in c["model_id"] for c in candidates[:2])

    def test_bounded_candidates(self):
        result = recommend_design({"purposes": ["distribution"], "geometry_kind": "point"})
        assert len(result["model_candidates"]) <= 5
        assert len(result["clarifications"]) <= 3


class TestSpecCandidates:
    def test_spec_scoring_and_conflict(self, tmp_path, monkeypatch):
        from app.core.database import Base, Engine, SessionLocal
        from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
        from app.services.design_specs.versioning import create_spec

        Base.metadata.create_all(bind=Engine)
        try:
            with SessionLocal() as db:
                spec = create_spec(db, {
                    "spec_version": "design_spec.v1",
                    "name": "区级统计规范",
                    "applicability": {
                        "purposes": ["analysis"],
                        "geometry_kinds": ["polygon"],
                    },
                    "classification": {"default_method": "quantiles", "shared_required": True},
                }, creator_id="rec-user")
                spec_id = spec.id

            result = recommend_design(
                {"purposes": ["analysis"], "geometry_kind": "polygon",
                 "statistic": "rate", "denominator": {"available": True}},
                user_id="rec-user",
            )
            ids = [c["spec_id"] for c in result["spec_candidates"]]
            assert spec_id in ids
        finally:
            with SessionLocal() as db:
                db.query(CartographyDesignSpecVersion).delete()
                db.query(CartographyDesignSpec).delete()
                db.commit()


# ── 绑定校验 ─────────────────────────────────────────────────────────


def _doc_with_profile(min_v=1.0, max_v=100.0, field_type="number", field="value"):
    """合成会话文档：专题层 + 源 profile 字段统计（Spatial Meta Profile 形状）。"""
    return {
        "version": "1.1",
        "sources": {
            "src": {
                "type": "geojson",
                "profile": {
                    "fields": {field: {"type": field_type, "stats": {
                        "min": min_v, "max": max_v, "mean": (min_v + max_v) / 2}}},
                },
            },
        },
        "layers": [{
            "id": "thematic", "source": "src", "type": "fill",
            "legend_spec": {"type": "graduated", "field": field,
                            "unit": "所/万人", "method": "quantiles",
                            "palette": "YlOrRd", "classes": 4},
        }],
    }


def _spec_payload(**role_overrides):
    role = {
        "name": "indicator",
        "value_type": "number",
        "measurement": "rate",
        "unit": "所/万人",
        "unit_required": True,
        "comparable_across_maps": True,
    }
    role.update(role_overrides)
    return {
        "spec_version": "design_spec.v1",
        "name": "绑定测试规范",
        "data_roles": [role],
    }


class TestBindingValidation:
    @pytest.mark.asyncio
    async def test_field_missing(self, monkeypatch):
        from app.services.design_specs.binding import FIELD_MISSING, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile(field="other_field")

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == FIELD_MISSING
        assert not result["ok"]

    @pytest.mark.asyncio
    async def test_all_zero_field_detected(self, monkeypatch):
        from app.services.design_specs.binding import ALL_ZERO, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile(min_v=0.0, max_v=0.0)

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == ALL_ZERO

    @pytest.mark.asyncio
    async def test_negative_count_detected(self, monkeypatch):
        from app.services.design_specs.binding import NEGATIVE_VALUES, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile(min_v=-5.0, max_v=100.0)

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(measurement="count"),
        )
        assert result["results"][0]["code"] == NEGATIVE_VALUES

    @pytest.mark.asyncio
    async def test_negative_rate_semantics_also_blocked(self, monkeypatch):
        # rate 语义同样禁负值
        from app.services.design_specs.binding import NEGATIVE_VALUES, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile(min_v=-5.0, max_v=100.0)

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),  # measurement=rate（默认）
        )
        assert result["results"][0]["code"] == NEGATIVE_VALUES

    @pytest.mark.asyncio
    async def test_type_mismatch(self, monkeypatch):
        from app.services.design_specs.binding import FIELD_TYPE_MISMATCH, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile(field_type="string")

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == FIELD_TYPE_MISMATCH

    @pytest.mark.asyncio
    async def test_unit_contract_mismatch(self, monkeypatch):
        from app.services.design_specs.binding import UNIT_MISMATCH, validate_role_bindings

        async def fake_get_mapspec(session_id):
            doc = _doc_with_profile()
            doc["layers"][0]["legend_spec"]["unit"] = "所"  # 契约单位是 所/万人
            return doc

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == UNIT_MISMATCH
        assert result["comparability_findings"]

    @pytest.mark.asyncio
    async def test_session_unavailable_is_not_evaluated_not_pass(self, monkeypatch):
        from app.services.design_specs.binding import SESSION_UNAVAILABLE, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return None

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "gone", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == SESSION_UNAVAILABLE
        assert result["not_evaluated_count"] == 1
        assert result["ok"] is False  # fail-closed：not_evaluated 不算通过

    @pytest.mark.asyncio
    async def test_unknown_role_rejected(self, monkeypatch):
        from app.services.design_specs.binding import ROLE_UNKNOWN, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile()

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "no_such_role", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == ROLE_UNKNOWN

    @pytest.mark.asyncio
    async def test_happy_binding(self, monkeypatch):
        from app.services.design_specs.binding import BOUND, validate_role_bindings

        async def fake_get_mapspec(session_id):
            return _doc_with_profile()

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        result = await validate_role_bindings(
            [{"role": "indicator", "session_id": "s1", "layer_id": "thematic",
              "field": "value"}],
            _spec_payload(),
        )
        assert result["results"][0]["code"] == BOUND
        assert result["ok"] is True
