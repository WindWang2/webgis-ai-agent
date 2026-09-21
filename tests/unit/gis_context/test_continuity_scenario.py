"""M6 — Multi-turn cartography continuity (the "成都学校" scenario).

A user works one goal across turns: schools → primary schools only → hide
a layer → change palette → switch to district statistics → export. The
harness must keep the working context correct across turns *and* refuse to
carry stale analysis forward when the basis drifts.
"""
from __future__ import annotations

import asyncio

from app.services.gis_context import hotpath as hp
from app.services.gis_context.working_context import (
    DecisionRecord,
    GISWorkingContext,
    WorkingBasis,
)



def _plain(text: str) -> str:
    """Strip the untrusted-fence tags for readability assertions."""
    return text.replace("<untrusted_gis_context>", "").replace(
        "</untrusted_gis_context>", "")
def _user_hidden_prov(layer_id: str, seq: int = 1):
    """Real ProvenanceEntry shape (gis_world_state/provenance.py)."""
    return {"seq": seq, "ts": "", "origin": "user", "actor": "ui",
            "kind": "PatchLayerPresentationIntent", "target": layer_id,
            "revision": seq, "detail": {"visible": False}}


def _mapspec(*, bounds, layers, sources=None, metric=None):
    """Real producer shape: view has center/zoom (bbox is derived),
    measure lives in legend_spec."""
    center = [(bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0]
    ms = {
        "view": {"center": center, "zoom": 11.0},
        "layers": list(layers),
        "sources": dict(sources or {}),
    }
    if metric:
        for layer in ms["layers"]:
            if layer.get("id") == metric["layer"]:
                layer["legend_spec"] = {
                    "type": "graduated",
                    "field": metric["field"],
                    "statistic": metric["statistic"],
                }
    return ms


def _run(coro):
    return asyncio.run(coro)


class _FakeRuntime:
    """Minimal mission runtime double (create + terminal state)."""

    def __init__(self):
        self.missions = {}
        self.counter = 0

    def create(self, **kw):
        self.counter += 1
        mid = f"msn-scenario-{self.counter}"
        self.missions[mid] = {"state": "running"}
        from types import SimpleNamespace

        return SimpleNamespace(mission_id=mid)


def test_schools_to_export_continuity(wc_store, monkeypatch):
    monkeypatch.setattr(hp, "_store", lambda: wc_store)
    runtime = _FakeRuntime()
    monkeypatch.setattr(
        "app.services.mission_runtime.service.get_mission_runtime",
        lambda store=None: runtime,
    )

    schools_layer = {"id": "L-schools", "type": "choropleth"}
    schools_src = {"schools": {"ref_id": "ref:schools", "content_revision": "rev-1"}}
    bounds = [103.9, 30.6, 104.2, 30.8]

    # ── T1: 「成都的学校分布」 — map work appears, mission binds, basis lands.
    state: dict = {}
    t1, r1 = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="成都市学校分布", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[schools_layer], sources=schools_src,
                         metric={"layer": "L-schools", "field": "school_count",
                                 "statistic": "sum"}),
    ))
    from app.services.session_data import session_data_manager

    state = _run(session_data_manager.get_map_state("sess-city"))
    mid = state[hp.MISSION_BINDING_KEY]["mission_id"]
    assert mid.startswith("msn-scenario")
    assert "AOI=成都市" not in _plain(t1)  # scope_name needs snapshot; basis still bounds✓
    assert "基准" in _plain(t1) and "数据集×1" in _plain(t1)
    wc1 = wc_store.load(mid, org_id="org-1")
    from app.lib.cartography.semantic_checks import _viewport_bbox

    derived = _viewport_bbox({"center": [(bounds[0] + bounds[2]) / 2.0,
                                          (bounds[1] + bounds[3]) / 2.0], "zoom": 11.0})
    assert wc1.basis.aoi_bbox == derived
    assert wc1.basis.datasets[0].ref_id == "ref:schools"

    # ── T2: 「只看小学」 — user decision recorded; same AOI, no invalidation.
    wc2 = wc_store.load(mid, org_id="org-1")
    wc2.accepted_assumptions.append(
        DecisionRecord(text="仅统计小学（用户选定）", turn_id="t2", basis_revision=wc2.revision))
    wc_store.save(wc2, expected_revision=wc2.revision)
    t2, _ = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="改成只看小学", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[schools_layer], sources=schools_src,
                         metric={"layer": "L-schools", "field": "school_count",
                                 "statistic": "sum"}),
    ))
    assert "仅统计小学（用户选定）" in _plain(t2)
    wc2b = wc_store.load(mid, org_id="org-1")
    assert wc2b.findings == [] or all(f.status != "stale" for f in wc2b.findings)

    # ── T3: 「隐藏公园图层」 — user edit is durable and user-wins.
    state["_gis_provenance"] = [_user_hidden_prov("L-parks")]
    t3, _ = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="把公园隐藏掉", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[schools_layer, {"id": "L-parks", "type": "fill"}],
                         sources=schools_src,
                         metric={"layer": "L-schools", "field": "school_count",
                                 "statistic": "sum"}),
    ))
    assert "用户已手动编辑 ×1" in _plain(t3) and "勿静默覆盖" in _plain(t3)
    assert wc_store.load(mid, org_id="org-1").user_edits[0].layer_id == "L-parks"

    # ── T4: 「换色板」 — restyle edit; analysis conclusions stay valid.
    state["_gis_provenance"] = [_user_hidden_prov("L-parks")]  # unchanged
    t4, r4 = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="色板换成蓝橙", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[schools_layer], sources=schools_src,
                         metric={"layer": "L-schools", "field": "school_count",
                                 "statistic": "sum"}),
    ))
    wc4 = wc_store.load(mid, org_id="org-1")
    assert wc4.stale == {}  # palette change ≠ basis drift

    # ── T5: 「改成各区统计」 — measure/statistic drift MUST stale old analysis.
    metric_layer = dict(schools_layer)
    t5, _ = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="改成各区统计", state=state,
        mapspec=_mapspec(
            bounds=bounds, layers=[metric_layer], sources=schools_src,
            metric={"layer": "L-schools", "field": "school_count",
                    "statistic": "mean_by_district"},
        ),
    ))
    wc5 = wc_store.load(mid, org_id="org-1")
    assert "basis.measure" in wc5.stale, "statistic drift must invalidate"

    # And a validated finding from the OLD statistic is not re-rendered.
    from app.services.gis_context.working_context import FindingRef

    wc5.accepted_assumptions.append(DecisionRecord(
        text="学校总数集中于武侯区", turn_id="t4", basis_revision=wc5.revision - 1,
        stale_basis=True))
    wc5.findings.append(
        FindingRef(claim_id="claim-old-sum", status="stale", basis_revision=1))
    wc_store.save(wc5, expected_revision=wc5.revision)
    t5b, _ = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="各区统计是多少", state=state,
        mapspec=_mapspec(
            bounds=bounds, layers=[metric_layer], sources=schools_src,
            metric={"layer": "L-schools", "field": "school_count",
                    "statistic": "mean_by_district"},
        ),
    ))
    assert "⚠需复核" in _plain(t5b)
    assert "claim-old-sum" not in t5b  # stale conclusion never rendered as current

    # ── T6: 「导出」 — export target lands in the basis.
    state["_export_target"] = {"format": "png"}
    t6, _ = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="导出成图", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[metric_layer], sources=schools_src),
    ))
    assert "导出=png" in _plain(t6)

    # ── T7: mission goes terminal — context must NOT leak into new turns.
    from types import SimpleNamespace

    runtime.missions[mid]["state"] = "complete"
    terminal_rec = SimpleNamespace(state=SimpleNamespace(value="complete"))
    runtime.store = SimpleNamespace(get_mission=lambda m, org_id=None: terminal_rec)
    t7, r7 = _run(hp.assemble_gis_context_card(
        "sess-city", org_id="org-1", project_id="prj-city", user_id="u-1",
        query_text="新目标", state=state,
        mapspec=_mapspec(bounds=bounds, layers=[metric_layer], sources=schools_src),
    ))
    assert t7 == "" and r7.miss_reason == "mission_terminal"
