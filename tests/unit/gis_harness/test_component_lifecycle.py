"""Component Lifecycle V3（Runtime V4 §17-19）契约测试。

不变量：
- 真删除：条目离开 layout.components（≠ enabled=False）；
- 复制：仅多实例；新 id 唯一；floating 偏移；
- 重绑定：per-type 字段白名单 + 互斥纪律；
- CAS：expected_revision 落后 → superseded（用户最新交互优先）；
- 用户路由与 agent 工具同一入口（mapspec_store facade）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.components import (
    duplicate_component,
    remove_component,
    rebind_component,
    chart_panel_component,
    CartographyComponent,
    MULTI_INSTANCE_TYPES,
)


def _components(*comps: CartographyComponent) -> list:
    return list(comps)


class TestPureFunctions:
    def test_remove_drops_the_entry(self):
        comps = _components(chart_panel_component(component_id="chart-panel"))
        remaining, change = remove_component(comps, component_id="chart-panel")
        assert remaining == []
        assert change == {
            "id": "chart-panel", "type": "chart_panel", "removed": True,
            "had_binding": {},
        }

    def test_remove_unknown_returns_none(self):
        remaining, change = remove_component([], component_id="ghost")
        assert change is None

    def test_duplicate_only_for_multi_instance(self):
        comps = _components(chart_panel_component(component_id="chart-panel"))
        with_copy, copy, err = duplicate_component(comps, component_id="chart-panel")
        assert err is None
        assert copy is not None
        assert copy.id == "chart-panel-copy"
        assert copy.type == "chart_panel"
        assert copy.placement.mode == "floating"
        assert len(with_copy) == 2

    def test_duplicate_singleton_refused(self):
        from app.services.gis_harness.components import north_arrow_component

        comps = _components(north_arrow_component())
        _, copy, err = duplicate_component(comps, component_id="north-arrow")
        assert copy is None
        assert "单例" in err

    def test_duplicate_respects_explicit_new_id_and_avoids_collision(self):
        comps = _components(
            chart_panel_component(component_id="c"),
        )
        _, copy1, _ = duplicate_component(comps, component_id="c")
        with_two = _components(comps[0], copy1)
        _, copy2, _ = duplicate_component(with_two, component_id="c")
        assert copy2.id not in {"c", copy1.id}

    def test_rebind_whitelist(self):
        comps = _components(chart_panel_component(component_id="c", chart={"type": "bar", "title": "t", "data": [{"name": "x", "value": 1}]}))
        rebound, change, err = rebind_component(
            comps, component_id="c", bindings={"chartRef": "ref:chart-new"},
        )
        assert err is None
        assert rebound[0].options["chartRef"] == "ref:chart-new"
        # 互斥纪律：换 chartRef 清掉 layerId 残留。
        assert "layerId" not in rebound[0].options
        assert change["rebound"]["chartRef"]["to"] == "ref:chart-new"

    def test_rebind_rejects_foreign_fields(self):
        comps = _components(chart_panel_component(component_id="c"))
        _, _, err = rebind_component(comps, component_id="c", bindings={"tableRef": "ref:t"})
        assert "不接受绑定字段" in err

    def test_rebind_empty_bindings_rejected(self):
        comps = _components(chart_panel_component(component_id="c"))
        _, _, err = rebind_component(comps, component_id="c", bindings={})
        assert err

    def test_multi_instance_vocabulary_includes_table_panel(self):
        assert "table_panel" in MULTI_INSTANCE_TYPES


pytestmark = pytest.mark.anyio


class TestEngineIntents:
    async def test_remove_intent_through_engine(self, tmp_path, monkeypatch):
        """RemoveComponentIntent 经 lifecycle engine 真删除 + revision 前进。"""
        from app.services.mapspec import (
            MapSpecLifecycleEngine,
            PatchComponentIntent,
            RemoveComponentIntent,
        )

        engine = MapSpecLifecycleEngine()
        sid = "lifecycle-test-session"
        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        res = await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id="chart-panel", component_type="chart_panel", upsert=True,
            ),
            origin="agent",
        )
        assert not res.is_error, res.error_msg
        assert any(
            c.get("id") == "chart-panel"
            for c in (res.mapspec or {}).get("layout", {}).get("components", [])
        )
        res2 = await engine.apply_mutation(
            sid, RemoveComponentIntent(component_id="chart-panel"), origin="agent",
        )
        assert not res2.is_error
        assert not any(
            c.get("id") == "chart-panel"
            for c in (res2.mapspec or {}).get("layout", {}).get("components", [])
        )

    async def test_duplicate_and_rebind_intents(self, tmp_path, monkeypatch):
        from app.services.mapspec import (
            DuplicateComponentIntent,
            MapSpecLifecycleEngine,
            PatchComponentIntent,
            RebindComponentIntent,
        )

        engine = MapSpecLifecycleEngine()
        sid = "lifecycle-dup-session"
        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id="chart-panel", component_type="chart_panel", upsert=True,
            ),
            origin="agent",
        )
        res = await engine.apply_mutation(
            sid,
            DuplicateComponentIntent(component_id="chart-panel"),
            origin="agent",
        )
        comps = (res.mapspec or {}).get("layout", {}).get("components", [])
        ids = {c.get("id") for c in comps}
        assert {"chart-panel", "chart-panel-copy"} <= ids

        res2 = await engine.apply_mutation(
            sid,
            RebindComponentIntent(
                component_id="chart-panel-copy",
                bindings={"chartRef": "ref:chart-xyz"},
            ),
            origin="agent",
        )
        comps2 = (res2.mapspec or {}).get("layout", {}).get("components", [])
        copy = next(c for c in comps2 if c.get("id") == "chart-panel-copy")
        assert copy["options"].get("chartRef") == "ref:chart-xyz"

    async def test_user_cas_superseded(self, tmp_path, monkeypatch):
        from app.services.mapspec import (
            MapSpecLifecycleEngine,
            PatchComponentIntent,
            RemoveComponentIntent,
        )

        engine = MapSpecLifecycleEngine()
        sid = "lifecycle-cas-session"
        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id="c", component_type="chart_panel", upsert=True,
            ),
            origin="agent",
        )
        res = await engine.apply_mutation(
            sid,
            RemoveComponentIntent(component_id="c"),
            origin="user",
            expected_revision=0,  # 落后（revision 已是 1）
        )
        assert res.superseded
        assert not res.is_error
