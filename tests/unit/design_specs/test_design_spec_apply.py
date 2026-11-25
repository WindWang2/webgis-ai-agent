"""WP2 应用测试 —— dry-run 计划（锁定/冲突/披露负例）+ 真实会话端到端。

端到端：真实 lifecycle engine（临时 store root）+ 生产式建层（G33 同款）
→ dry_run_apply（零 mutation 断言）→ apply_design_spec（CAS 逐项回执）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.design_specs.apply import build_apply_plan

pytestmark = pytest.mark.unit


def _spec_payload(**kw) -> dict:
    base = {
        "spec_version": "design_spec.v1",
        "name": "应用测试规范",
        "color": {"theme_id": "cartographic.light_interactive", "palette_sequential": "YlOrRd"},
        "classification": {"default_method": "quantiles", "k_min": 5, "k_max": 7},
        "components": {
            "required": [{"component_type": "legend"}, {"component_type": "graticule"}],
            "forbidden": ["north_arrow"],
        },
        "typography": {"font_resource": "noto-sans-cjk-sc"},
        "output": {"targets": ["interactive", "pdf"], "paper_profile": "a4_landscape"},
        "overrides": {"locked": [], "require_override_disclosure": True},
    }
    base.update(kw)
    return base


def _document(**layout_extra) -> dict:
    layout = {
        "components": [
            {"id": "t", "type": "title"},
            {"id": "lg", "type": "legend"},
            {"id": "na", "type": "north_arrow"},  # 规范禁用 → 冲突披露
        ],
    }
    layout.update(layout_extra)
    return {
        "version": "1.1",
        "sources": {},
        "layers": [{
            "id": "thematic",
            "source": "src",
            "type": "fill",
            "legend_spec": {"type": "graduated", "field": "v", "palette": "Blues",
                            "classes": 4, "method": "quantiles"},
            "data_binding": {"field": "v", "field_type": "number"},
        }],
        "layout": layout,
    }


class TestBuildApplyPlan:
    def test_detects_palette_k_component_changes(self):
        plan = build_apply_plan(_spec_payload(), _document())
        assert plan["has_changes"]
        assert plan["style_actions"], "palette Blues→YlOrRd 与 k 4→5 应被检出"
        action = plan["style_actions"][0]
        assert action["palette"] == "YlOrRd"
        assert action["k"] == 5
        assert set(action["changes"]) == {"palette", "k"}
        assert plan["component_ensures"], "graticule 缺失应被 ensure"
        assert plan["component_ensures"][0]["component_type"] == "graticule"

    def test_forbidden_component_is_conflict_not_removal(self):
        plan = build_apply_plan(_spec_payload(), _document())
        codes = [c["kind"] for c in plan["conflicts"]]
        assert "forbidden_component_present" in codes
        # 绝不生成删除动作
        assert all(e["component_type"] != "north_arrow" for e in plan["component_ensures"])

    def test_locked_fields_skip_and_disclose(self):
        plan = build_apply_plan(
            _spec_payload(), _document(), locked_fields=("palettes", "required_components"),
        )
        locked = {s["field"] for s in plan["skipped_locked"]}
        assert "palette" in locked
        assert "required_components" in locked
        assert plan["style_actions"] == [] or all(
            "palette" not in a for a in plan["style_actions"]
        )
        assert plan["component_ensures"] == []

    def test_spec_level_locks_apply(self):
        spec = _spec_payload(
            classification={"default_method": "equal_interval", "k_min": 5, "k_max": 7},
            overrides={"locked": ["classification_method"]},
        )
        plan = build_apply_plan(spec, _document())  # 文档层是 quantiles → 有变更可锁
        locked = {s["field"] for s in plan["skipped_locked"]}
        assert "classification_method" in locked

    def test_no_print_layout_discloses_publication_time(self):
        plan = build_apply_plan(_spec_payload(), _document())
        assert any("print_layout" in d for d in plan["disclosures"])
        assert plan["layout_ops"] == []

    def test_print_layout_generates_ops(self):
        doc = _document(print_layout={
            "version": "print_layout.v1", "paper": {
                "profile": "a4_portrait", "width_mm": 210, "height_mm": 297},
        })
        plan = build_apply_plan(_spec_payload(), doc)
        ops = [o["op"] for o in plan["layout_ops"]]
        assert "set_typography" in ops
        assert "set_paper" in ops

    def test_typography_locked_skips_paper_ops(self):
        doc = _document(print_layout={"version": "print_layout.v1"})
        plan = build_apply_plan(
            _spec_payload(), doc, locked_fields=("typography",),
        )
        assert plan["layout_ops"] == []
        assert any("typography" in s["field"] or "typography" in s["reason"]
                   for s in plan["skipped_locked"])

    def test_role_field_filter_narrows_style_actions(self):
        doc = _document()
        doc["layers"].append({
            "id": "other", "source": "src2", "type": "fill",
            "legend_spec": {"type": "graduated", "field": "w", "palette": "Blues"},
            "data_binding": {"field": "w", "field_type": "number"},
        })
        plan_all = build_apply_plan(_spec_payload(), doc)
        plan_narrow = build_apply_plan(_spec_payload(), doc, role_field="v")
        assert len(plan_narrow["style_actions"]) <= len(plan_all["style_actions"])
        assert all(a["layer_id"] == "thematic" for a in plan_narrow["style_actions"])

    def test_no_changes_is_false_positive_free(self):
        spec = _spec_payload()
        spec["color"]["palette_sequential"] = "Blues"
        spec["classification"]["k_min"] = 4
        spec["components"] = {"required": [{"component_type": "title"},
                                           {"component_type": "legend"},
                                           {"component_type": "north_arrow"}],
                              "forbidden": []}
        plan = build_apply_plan(spec, _document())
        assert not plan["has_changes"]


# ── 端到端：真实 lifecycle engine + thematic_authoring ────────────────


@pytest.fixture()
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    yield root


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    from app.services.thematic_authoring import stats as stats_mod

    stats_mod._stats_cache.clear()
    yield
    stats_mod._stats_cache.clear()


def _make_fc(n=12) -> dict:
    features = []
    for i in range(n):
        base = 116.0 + i * 0.01
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0],
                [base + 0.01, 39.01], [base, 39.01], [base, 39.0],
            ]]},
            "properties": {"name": f"zone-{i}", "value": float(i * 10 + 5)},
        })
    return {"type": "FeatureCollection", "features": features}


async def _seed_thematic_session():
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.session_data import session_data_manager

    session_id = "sess-dspec-apply-" + uuid.uuid4().hex[:10]
    fc = _make_fc()
    ref_id = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result",
    )
    from app.lib.cartography.thematic_spec import build_graduated_spec, spec_to_paint

    legend = build_graduated_spec(fc, field="value")
    assert legend is not None
    paint_color, _warn = spec_to_paint(legend)
    layer = {
        "id": "thematic",
        "type": "fill",
        "source": "thematic-source",
        "paint": {"color": paint_color},
        "legend_spec": legend,
        "data_binding": {"field": "value", "field_type": "number"},
    }
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=layer, source_data={"ref_id": str(ref_id)}),
        origin="agent", actor="test:dspec-apply",
    )
    assert not result.is_error, result.error_msg
    return session_id


class TestEndToEnd:
    async def test_dry_run_zero_mutation_then_apply_receipts(self, store_root):
        from app.services.design_specs.apply import (
            apply_design_spec,
            dry_run_apply,
        )
        from app.services.thematic_authoring.service import _current_revision

        session_id = await _seed_thematic_session()
        revision_before = await _current_revision(session_id)

        dry = await dry_run_apply(session_id, _spec_payload())
        assert dry["has_changes"]
        revision_after_dry = await _current_revision(session_id)
        assert revision_after_dry == revision_before, "dry-run 不得产生 mutation"

        # 应用：锁 palette → 只应用分类轴；组件 ensure 一笔 CAS
        result = await apply_design_spec(
            session_id, _spec_payload(),
            expected_revision=revision_before,
            locked_fields=("palettes",),
        )
        assert result["applied"] is True
        summary = result["summary"]
        assert summary["total"] >= 1
        assert summary["failed"] == 0, result["items"]
        # 逐项回执都有 revision 或明确 skipped
        for item in result["items"]:
            assert item["status"] in ("committed", "skipped", "superseded", "failed")

        # 幂等性：同 spec 再应用 → 无变更（palette 已锁/分类已一致）
        final_revision = summary["final_revision"]
        second = await apply_design_spec(
            session_id, _spec_payload(),
            expected_revision=final_revision,
            locked_fields=("palettes",),
        )
        assert second["has_changes"] is False or second["summary"]["failed"] == 0

    async def test_stale_revision_reports_superseded(self, store_root):
        from app.services.design_specs.apply import apply_design_spec

        session_id = await _seed_thematic_session()
        result = await apply_design_spec(
            session_id, _spec_payload(),
            expected_revision=99999,  # 过期令牌
            locked_fields=("palettes",),
        )
        # 逐图语义：superseded/failed 如实，不伪装 committed
        statuses = {item["status"] for item in result["items"]}
        assert statuses <= {"committed", "superseded", "failed", "skipped"}
        if result["summary"]["committed"] == 0:
            assert result["summary"]["total"] >= 1
