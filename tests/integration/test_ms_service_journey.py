"""多源联动服务旅程集成测试（WP3）：逐源冻结/传播键集/指标/保存重开。

真实链：session_data_manager.store（强版本 ref）→ freeze_source 逐源同代
核对 → per-source 条件编译 → 传播复合键集半连接（DAG 单遍）→ buckets
facet → metrics 指标 → exploration_views v3 持久化（逐源锚校验）→
open 重放。

oracle：期望计数/键集/指标全部独立手算（fixture 属性表枚举），不调用
生产 evaluator 生成 expected。
"""
from __future__ import annotations

import pytest

from app.services.linked_exploration import service
from app.services.linked_exploration.errors import LinkedExplorationError

pytestmark = pytest.mark.asyncio

SESSION = "ms-journey-session"
ACTOR = {"user_id": "ms-user", "org_id": None, "owner_token": None,
         "trusted_session_id": SESSION}

SCHOOL_REF: str = ""
POP_REF: str = ""


def _schools_fc():
    """学校点：手算表 ——
    武侯区: pub, pub, priv (3 所；public 2)
    锦江区: pub (1 所；public 1)
    青羊区: pub, priv (2 所；public 1)
    港区:   无学校
    """
    rows = [
        ("武侯区", "public"), ("武侯区", "public"), ("武侯区", "private"),
        ("锦江区", "public"),
        ("青羊区", "public"), ("青羊区", "private"),
    ]
    feats = []
    for i, (district, kind) in enumerate(rows):
        feats.append({
            "type": "Feature",
            "properties": {"_wfid": f"sch-{i}", "district": district,
                           "type": kind, "students": 100 * (i + 1)},
            "geometry": {"type": "Point", "coordinates": [104.0 + i, 30.6]},
        })
    return {"type": "FeatureCollection", "features": feats}


def _population_fc():
    """区县人口表：武侯 100 万 / 锦江 50 万 / 青羊 30 万 / 港区 10 万。"""
    rows = [("武侯区", 1_000_000), ("锦江区", 500_000),
            ("青羊区", 300_000), ("港区", 100_000)]
    feats = []
    for i, (district, pop) in enumerate(rows):
        feats.append({
            "type": "Feature",
            "properties": {"_wfid": f"pop-{i}", "district": district,
                           "pop": pop},
            "geometry": None,
        })
    return {"type": "FeatureCollection", "features": feats}


async def _seed() -> tuple:
    from app.services.session_data import session_data_manager

    global SCHOOL_REF, POP_REF
    if not SCHOOL_REF:
        SCHOOL_REF = await session_data_manager.store(
            SESSION, _schools_fc(), prefix="mssch")
    if not POP_REF:
        POP_REF = await session_data_manager.store(
            SESSION, _population_fc(), prefix="mspop")
    school_inspect = await service.inspect_source(SESSION, SCHOOL_REF, ACTOR)
    pop_inspect = await service.inspect_source(SESSION, POP_REF, ACTOR)
    return school_inspect["anchor"], pop_inspect["anchor"]


def _v3_plan(school_anchor, pop_anchor, *, links=None, conditions=None,
             charts=None, metrics=None):
    return {
        "schema_version": 3,
        "sources": [
            {"source_id": "school", "anchor": school_anchor, "role": "primary"},
            {"source_id": "pop", "anchor": pop_anchor, "role": "context"},
        ],
        "links": links or [],
        "metrics": metrics or [],
        "charts": charts or [],
        "conditions": conditions or [],
        "base_scope_note": "",
    }


def _l2r_link(propagate="left_to_right"):
    return {"link_id": "l-sch-pop", "kind": "attribute",
            "left_source_id": "school", "right_source_id": "pop",
            "keys": [{"left_field": "district", "right_field": "district"}],
            "cardinality": "many_to_one", "propagate": propagate}


def _type_public_condition(chart="ch-type"):
    return {"kind": "category", "condition_id": f"cond-{chart}",
            "chart_id": chart, "field": "type", "values": ["public"]}


class TestMultiSourcePreview:
    async def test_per_source_counts_and_no_propagation(self):
        school_anchor, pop_anchor = await _seed()
        plan = _v3_plan(school_anchor, pop_anchor)
        out = await service.preview_plan(SESSION, plan, ACTOR)
        # 手算：学校 6 行、人口 4 行（无条件、无传播 → 各自全量）
        assert out["per_source"]["school"]["match_count"] == 6
        assert out["per_source"]["pop"]["match_count"] == 4
        assert out["primary_source_id"] == "school"
        assert out["per_source"]["school"]["total"] == 6
        assert out["completeness"] == "complete"

    async def test_propagation_restricts_downstream(self):
        school_anchor, pop_anchor = await _seed()
        plan = _v3_plan(
            school_anchor, pop_anchor,
            links=[_l2r_link()],
            conditions=[_type_public_condition()],
            charts=[{"chart_id": "ch-type", "kind": "category",
                     "field": "type", "source_id": "school"}],
        )
        out = await service.preview_plan(SESSION, plan, ACTOR)
        # 手算：public 学校 → {武侯区, 锦江区, 青羊区}（键集 3 个）→
        # 人口表匹配 = 3 行（港区被排除）
        assert out["per_source"]["school"]["match_count"] == 4  # public×4
        assert out["per_source"]["pop"]["match_count"] == 3
        props = out["per_source"]["pop"]["propagations"]
        assert len(props) == 1
        assert props[0]["key_count"] == 3
        assert props[0]["from_unfiltered"] is False
        # 单键对可投影 → 地图过滤可得
        assert props[0]["map_filter"] is not None
        assert out["per_source"]["pop"]["map_filter_projected"] is True
        # 下游 selection handle 可表达（键 3 ≤ 64）
        assert "selection_handle" in out["per_source"]["pop"]

    async def test_unfiltered_upstream_imposes_no_restriction(self):
        school_anchor, pop_anchor = await _seed()
        plan = _v3_plan(school_anchor, pop_anchor, links=[_l2r_link()])
        out = await service.preview_plan(SESSION, plan, ACTOR)
        # 上游无条件 → 不产生传播限制（下游全量 4 行；不是键集全量过滤）
        assert out["per_source"]["pop"]["match_count"] == 4
        props = out["per_source"]["pop"]["propagations"]
        assert props[0]["from_unfiltered"] is True
        assert props[0]["key_count"] == 0

    async def test_empty_upstream_selection_empty_downstream(self):
        """上游条件选空（无 public 之外类别不存在 → 换 never-match 值）→
        下游诚实空集（不是回退全量）。"""
        school_anchor, pop_anchor = await _seed()
        plan = _v3_plan(
            school_anchor, pop_anchor,
            links=[_l2r_link()],
            conditions=[{"kind": "category", "condition_id": "cond-ch-type",
                         "chart_id": "ch-type", "field": "type",
                         "values": ["never-matches"]}],
        )
        out = await service.preview_plan(SESSION, plan, ACTOR)
        assert out["per_source"]["school"]["match_count"] == 0
        assert out["per_source"]["pop"]["match_count"] == 0

    async def test_metric_schools_per_10k_in_preview(self):
        school_anchor, pop_anchor = await _seed()
        metric = {
            "metric_id": "m-per10k", "op": "rate", "source_id": "school",
            "denominator_source_id": "pop", "denominator_field": "pop",
            "link_id": "l-sch-pop", "scale": 10000, "unit": "所/万人",
        }
        plan = _v3_plan(school_anchor, pop_anchor,
                        links=[_l2r_link()], metrics=[metric])
        out = await service.preview_plan(SESSION, plan, ACTOR)
        m_out = [m for m in out["metrics"] if m["metric_id"] == "m-per10k"]
        assert len(m_out) == 1
        by_key = {g["key"]: g for g in m_out[0]["groups"]}
        # oracle：武侯 3/100万×1万 = 0.03；锦江 1/50万×1万 = 0.02；
        # 青羊 2/30万×1万 ≈ 0.0667；港区 0/10万 = 0（分母非零 → 诚实 0）
        assert by_key["武侯区"]["value"] == pytest.approx(0.03)
        assert by_key["锦江区"]["value"] == pytest.approx(0.02)
        assert by_key["青羊区"]["value"] == pytest.approx(2 / 300_000 * 10_000)
        assert by_key["港区"]["available"] is True
        assert by_key["港区"]["value"] == 0.0

    async def test_metric_scope_follows_propagation(self):
        """指标在联动范围过滤后的行集上求值（public 学校 → 每万人下降）。"""
        school_anchor, pop_anchor = await _seed()
        metric = {
            "metric_id": "m-per10k", "op": "rate", "source_id": "school",
            "denominator_source_id": "pop", "denominator_field": "pop",
            "link_id": "l-sch-pop", "scale": 10000,
        }
        plan = _v3_plan(
            school_anchor, pop_anchor, links=[_l2r_link()],
            metrics=[metric], conditions=[_type_public_condition()])
        out = await service.preview_plan(SESSION, plan, ACTOR)
        m_out = out["metrics"][0]
        by_key = {g["key"]: g for g in m_out["groups"]}
        # 手算：public 学校 武侯 2、锦江 1、青羊 1
        assert by_key["武侯区"]["value"] == pytest.approx(0.02)
        assert by_key["锦江区"]["value"] == pytest.approx(0.02)
        assert by_key["青羊区"]["value"] == pytest.approx(1 / 300_000 * 10_000)

    async def test_stale_anchor_rejected_with_source_detail(self):
        school_anchor, pop_anchor = await _seed()
        plan = _v3_plan(school_anchor, pop_anchor)
        plan["sources"][1]["anchor"] = {
            **pop_anchor, "content_revision": pop_anchor["content_revision"] + 99}
        with pytest.raises(LinkedExplorationError) as ei:
            await service.preview_plan(SESSION, plan, ACTOR)
        assert ei.value.code == "STALE_SOURCE"
        assert ei.value.detail["sources"][0]["source_id"] == "pop"

    async def test_key_cap_rejected_not_truncated(self):
        """上游键集超帽 typed 拒绝（构造 >256 个不同键的**已过滤**上游
        选择 —— 无条件上游不产生传播限制，是设计语义）。"""
        from app.services.session_data import session_data_manager

        sid = "ms-journey-cap"
        actor = {**ACTOR, "trusted_session_id": sid}
        n = 300
        big_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"_wfid": f"k{i}", "district": f"d{i:04d}",
                            "n": i},
             "geometry": None} for i in range(n)]}
        ref_big = await session_data_manager.store(sid, big_fc, prefix="mscap")
        small_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"_wfid": "p0",
                                               "district": "d0000", "pop": 1},
             "geometry": None}]}
        ref_small = await session_data_manager.store(
            sid, small_fc, prefix="mscap2")
        a_big = (await service.inspect_source(sid, ref_big, actor))["anchor"]
        a_small = (await service.inspect_source(sid, ref_small, actor))["anchor"]
        plan = {
            "schema_version": 3,
            "sources": [
                {"source_id": "big", "anchor": a_big, "role": "primary"},
                {"source_id": "pop", "anchor": a_small, "role": "context"},
            ],
            "links": [{"link_id": "l1", "kind": "attribute",
                       "left_source_id": "big", "right_source_id": "pop",
                       "keys": [{"left_field": "district",
                                 "right_field": "district"}],
                       "cardinality": "one_to_many",
                       "propagate": "left_to_right"}],
            "metrics": [], "charts": [],
            "conditions": [{"kind": "range", "condition_id": "cond-ch-n",
                            "chart_id": "ch-n", "field": "n", "gte": 0}],
            "base_scope_note": "",
        }
        with pytest.raises(LinkedExplorationError) as ei:
            await service.preview_plan(sid, plan, actor)
        assert ei.value.code == "PROPAGATION_KEYSET_OVER_LIMIT"
        assert ei.value.detail["key_count"] == n


class TestCompositePropagation:
    async def test_composite_keys_propagate_and_handle_skipped(self):
        from app.services.session_data import session_data_manager

        sid = "ms-journey-composite"
        actor = {**ACTOR, "trusted_session_id": sid}
        up_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"_wfid": "u0", "district": "武侯区",
                            "year": 2023, "kind": "keep"},
             "geometry": None},
            {"type": "Feature",
             "properties": {"_wfid": "u1", "district": "锦江区",
                            "year": 2023, "kind": "keep"},
             "geometry": None},
            {"type": "Feature",
             "properties": {"_wfid": "u2", "district": "青羊区",
                            "year": 2022, "kind": "drop"},
             "geometry": None},
        ]}
        down_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"_wfid": "d0", "district": "武侯区",
                            "year": 2023, "pop": 10},
             "geometry": None},
            {"type": "Feature",
             "properties": {"_wfid": "d1", "district": "锦江区",
                            "year": 2023, "pop": 20},
             "geometry": None},
            {"type": "Feature",
             "properties": {"_wfid": "d2", "district": "青羊区",
                            "year": 2022, "pop": 40},
             "geometry": None},
            {"type": "Feature",
             "properties": {"_wfid": "d3", "district": "港区",
                            "year": 2023, "pop": 80},
             "geometry": None},
        ]}
        up_ref = await session_data_manager.store(sid, up_fc, prefix="msup")
        down_ref = await session_data_manager.store(sid, down_fc, prefix="msdn")
        a_up = (await service.inspect_source(sid, up_ref, actor))["anchor"]
        a_down = (await service.inspect_source(sid, down_ref, actor))["anchor"]
        plan = {
            "schema_version": 3,
            "sources": [
                {"source_id": "up", "anchor": a_up, "role": "primary"},
                {"source_id": "dn", "anchor": a_down, "role": "context"},
            ],
            "links": [{"link_id": "l1", "kind": "attribute",
                       "left_source_id": "up", "right_source_id": "dn",
                       "keys": [
                           {"left_field": "district",
                            "right_field": "district"},
                           {"left_field": "year", "right_field": "year"},
                       ],
                       "cardinality": "one_to_one",
                       "propagate": "left_to_right"}],
            "metrics": [], "charts": [],
            "conditions": [{"kind": "category",
                            "condition_id": "cond-ch-kind",
                            "chart_id": "ch-kind", "field": "kind",
                            "values": ["keep"]}],
            "base_scope_note": "",
        }
        out = await service.preview_plan(sid, plan, actor)
        # 手算：上游 keep 行键集 = {(武侯,2023), (锦江,2023)} → 下游匹配
        # d0, d1 = 2 行（青羊 2022 不在键集、港区无上游对应）
        assert out["per_source"]["up"]["match_count"] == 2
        assert out["per_source"]["dn"]["match_count"] == 2
        prop = out["per_source"]["dn"]["propagations"][0]
        assert prop["key_count"] == 2
        # 复合键 → 地图投影拒绝 + handle 跳过（诚实披露，不过宽投影）
        assert prop["map_filter"] is None
        assert out["per_source"]["dn"]["map_filter_projected"] is False
        assert "selection_handle_skipped" in out["per_source"]["dn"]


class TestSaveOpenMultiSource:
    async def _isolated(self, name):
        from app.services.session_data import session_data_manager

        sid = f"ms-journey-save-{name}"
        actor = {**ACTOR, "trusted_session_id": sid}
        school_ref = await session_data_manager.store(
            sid, _schools_fc(), prefix=f"{name}s")
        pop_ref = await session_data_manager.store(
            sid, _population_fc(), prefix=f"{name}p")
        return sid, actor, school_ref, pop_ref

    async def test_save_and_open_ready_with_metrics(self):
        sid, actor, school_ref, pop_ref = await self._isolated("ready")
        a_school = (await service.inspect_source(sid, school_ref, actor))["anchor"]
        a_pop = (await service.inspect_source(sid, pop_ref, actor))["anchor"]
        plan = _v3_plan(
            a_school, a_pop, links=[_l2r_link()],
            metrics=[{"metric_id": "m1", "op": "rate", "source_id": "school",
                      "denominator_source_id": "pop",
                      "denominator_field": "pop", "link_id": "l-sch-pop",
                      "scale": 10000}])
        view = await service.save_view(sid, actor, title="联动视图",
                                       plan_raw=plan)
        assert view["source_ref"] == school_ref   # 冗余列 = primary
        opened = await service.open_view(view["view_id"], sid, actor)
        assert opened["state"] == "ready"
        preview = opened["preview"]
        assert preview["per_source"]["pop"]["match_count"] == 4
        assert preview["metrics"][0]["metric_id"] == "m1"

    async def test_open_stale_lists_advanced_source(self):
        sid, actor, school_ref, pop_ref = await self._isolated("stale")
        a_school = (await service.inspect_source(sid, school_ref, actor))["anchor"]
        a_pop = (await service.inspect_source(sid, pop_ref, actor))["anchor"]
        plan = _v3_plan(a_school, a_pop)
        view = await service.save_view(sid, actor, title="stale 视图",
                                       plan_raw=plan)
        # 推进 pop 源（overwrite → revision +1，同内容也推进 generation）
        ok = await session_data_manager_overwrite(pop_ref, _population_fc(),
                                                  sid)
        assert ok is True
        opened = await service.open_view(view["view_id"], sid, actor)
        assert opened["state"] == "stale"
        assert opened["stale_reason"]["code"] == "SOURCE_ADVANCED"
        sources = opened["stale_reason"]["sources"]
        assert any(s["source_id"] == "pop" for s in sources)
        # school 未推进 → 不在漂移清单
        assert all(s["source_id"] != "school" for s in sources)

    async def test_v2_view_reopen_via_unified_path(self):
        """v2 单源视图经统一 preview_plan 重放（兼容不退化）。"""
        sid, actor, school_ref, _pop_ref = await self._isolated("v2compat")
        a_school = (await service.inspect_source(sid, school_ref, actor))["anchor"]
        plan = {
            "schema_version": 2, "anchor": a_school,
            "charts": [{"chart_id": "c1", "kind": "category",
                        "field": "type"}],
            "conditions": [], "base_scope_note": "",
        }
        view = await service.save_view(sid, actor, title="v2 视图",
                                       plan_raw=plan)
        opened = await service.open_view(view["view_id"], sid, actor)
        assert opened["state"] == "ready"
        preview = opened["preview"]
        assert preview["primary_source_id"] == "primary"
        assert preview["per_source"]["primary"]["match_count"] == 6


async def session_data_manager_overwrite(ref, fc, sid):
    """辅助：overwrite 包装（保持与既有 E20 相同的真实推进语义）。"""
    from app.services.session_data import session_data_manager

    return await session_data_manager.overwrite(sid, ref, fc)


class TestSelectionRef:
    """选择史指代（"只看刚才选中的那组"）：解析 → id 条件注入 → 传播。"""

    async def test_selection_ref_restricts_primary_and_propagates(self, monkeypatch):
        from app.services.session_data import session_data_manager

        sid = "ms-journey-selref"
        actor = {**ACTOR, "trusted_session_id": sid}
        school_ref = await session_data_manager.store(
            sid, _schools_fc(), prefix="mssel")
        pop_ref = await session_data_manager.store(
            sid, _population_fc(), prefix="msselp")
        a_school = (await service.inspect_source(sid, school_ref, actor))["anchor"]
        a_pop = (await service.inspect_source(sid, pop_ref, actor))["anchor"]
        # 选择史：用户在地图上选了 2 个武侯区学校（_wfid sch-0, sch-1）。
        # map_state 走 Redis（本环境无）—— 按既有 IR 测试模式以 monkeypatch
        # 提供账本读取；resolve_selection_ref/条件注入/传播链全为真实代码。
        # 两代选择：seq2（当前代）替换 seq1（→ superseded = "previous"）
        ledger = [
            {"kind": "feature_selected", "server_seq": 1,
             "payload": {"selected_ids": ["sch-0", "sch-1"],
                         "layer_id": "lyr-schools",
                         "dataset_ref": school_ref, "matched_count": 2}},
            {"kind": "feature_selected", "server_seq": 2,
             "payload": {"selected_ids": ["sch-5"],
                         "layer_id": "lyr-schools",
                         "dataset_ref": school_ref, "matched_count": 1}},
        ]

        async def _fake_get_state_field(session_id_arg, field):
            if field == "_interaction_ir_ledger":
                return ledger
            return await session_data_manager.get_state_field(
                session_id_arg, field)

        import app.services.session_data as sd_mod
        monkeypatch.setattr(
            sd_mod.session_data_manager, "get_state_field",
            _fake_get_state_field)
        plan = _v3_plan(a_school, a_pop, links=[_l2r_link()])
        out = await service.preview_plan(
            sid, plan, actor, selection_ref="previous")
        # 手算：只看 sch-0/sch-1（都武侯区 public）→ 学校 2；传播键 {武侯区}
        # → 人口 1 行
        assert out["per_source"]["school"]["match_count"] == 2
        assert out["per_source"]["pop"]["match_count"] == 1
        disc = out["selection_ref_resolved"]
        assert disc["id_field"] == "_wfid"
        assert disc["id_count"] == 2
        assert disc["ids_truncated"] is False

    async def test_selection_ref_unresolvable_typed(self):
        from app.services.session_data import session_data_manager

        sid = "ms-journey-selref2"
        actor = {**ACTOR, "trusted_session_id": sid}
        school_ref = await session_data_manager.store(
            sid, _schools_fc(), prefix="mssel2")
        pop_ref = await session_data_manager.store(
            sid, _population_fc(), prefix="mssel2p")
        a_school = (await service.inspect_source(sid, school_ref, actor))["anchor"]
        a_pop = (await service.inspect_source(sid, pop_ref, actor))["anchor"]
        plan = _v3_plan(a_school, a_pop)
        with pytest.raises(LinkedExplorationError) as ei:
            await service.preview_plan(sid, plan, actor,
                                       selection_ref="previous")
        assert ei.value.code == "SELECTION_REF_UNRESOLVED"
        assert "previous" in ei.value.correction_hint  # 最小澄清提示
