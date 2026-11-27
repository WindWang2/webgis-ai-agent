"""审查驱动测试（WP6）：A-P2-1 传递 no-op、A-P2-2/B-P1 多源导出守卫、
A-P2-3 跨源 rate 端点错配、A-P3-9 同 ref 双绑定。

oracle 全部手算（与 ms_service_journey 同 fixture 族）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.linked_exploration import contract, service


SESSION = "ms-review-session"
ACTOR = {"user_id": "ms-review", "org_id": None, "owner_token": None,
         "trusted_session_id": SESSION}


def _fc_a():
    """三源链 fixture 的 A 源（2 行：a1 / b9）。"""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"_wfid": "a1", "key": "a1",
                                           "kind": "keep"},
         "geometry": None},
        {"type": "Feature", "properties": {"_wfid": "b9", "key": "b9",
                                           "kind": "keep"},
         "geometry": None},
    ]}


def _fc_b():
    """B 源（3 行：a1 / b9 / zzz）。"""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"_wfid": f"b-{k}", "key": k},
         "geometry": None}
        for k in ("a1", "b9", "zzz")]}


def _fc_c():
    """C 源（3 行：a1 / b9 / zzz）。"""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"_wfid": f"c-{k}", "key": k},
         "geometry": None}
        for k in ("a1", "b9", "zzz")]}


async def _seed(sid, actor):
    from app.services.session_data import session_data_manager

    refs = {}
    for name, fc in (("a", _fc_a()), ("b", _fc_b()), ("c", _fc_c())):
        ref = await session_data_manager.store(sid, fc, prefix=f"msrv{name}")
        refs[name] = (ref, (await service.inspect_source(
            sid, ref, actor))["anchor"])
    return refs


def _chain_plan(refs):
    """A→B→C 全域 left_to_right 链（全部无 own 条件 = 全域 no-op 声明）。"""
    return {
        "schema_version": 3,
        "sources": [
            {"source_id": "a", "anchor": refs["a"][1], "role": "primary"},
            {"source_id": "b", "anchor": refs["b"][1], "role": "context"},
            {"source_id": "c", "anchor": refs["c"][1], "role": "context"},
        ],
        "links": [
            {"link_id": "l-ab", "kind": "attribute", "left_source_id": "a",
             "right_source_id": "b",
             "keys": [{"left_field": "key", "right_field": "key"}],
             "cardinality": "one_to_many", "propagate": "left_to_right"},
            {"link_id": "l-bc", "kind": "attribute", "left_source_id": "b",
             "right_source_id": "c",
             "keys": [{"left_field": "key", "right_field": "key"}],
             "cardinality": "one_to_many", "propagate": "left_to_right"},
        ],
        "metrics": [], "charts": [], "conditions": [],
        "base_scope_note": "",
    }


@pytest.mark.asyncio
class TestTransitiveNoOp:
    async def test_inert_chain_imposes_no_restriction(self):
        """A-P2-1：全域无条件的 A→B→C 链是 no-op —— C 保持全量 3 行
        （修复前：B 的全量键集 {a1,b9} 会把 C 的 zzz 静默丢掉 → 2 行）。"""
        sid = "ms-review-chain"
        actor = {**ACTOR, "trusted_session_id": sid}
        refs = await _seed(sid, actor)
        try:
            out = await service.preview_plan(sid, _chain_plan(refs), actor)
            assert out["per_source"]["a"]["match_count"] == 2
            assert out["per_source"]["b"]["match_count"] == 3
            assert out["per_source"]["c"]["match_count"] == 3
            for sid_name in ("b", "c"):
                props = out["per_source"][sid_name]["propagations"]
                assert all(p.get("from_unfiltered") for p in props)
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(sid)


class TestCrossRateEndpoints:
    def test_mismatched_endpoints_rejected(self):
        """A-P2-3：跨源 rate 源对 ≠ link 两端 → 计划校验拒绝（修复前在
        无关源上静默产出 available 的假 0 率）。"""
        raw = {
            "schema_version": 3,
            "sources": [
                {"source_id": "school", "role": "primary",
                 "anchor": {"ref_id": "ref:s", "content_revision": 1}},
                {"source_id": "pop", "role": "context",
                 "anchor": {"ref_id": "ref:p", "content_revision": 1}},
                {"source_id": "shops", "role": "context",
                 "anchor": {"ref_id": "ref:sh", "content_revision": 1}},
            ],
            "links": [{"link_id": "l1", "kind": "attribute",
                       "left_source_id": "school", "right_source_id": "pop",
                       "keys": [{"left_field": "district",
                                 "right_field": "district"}],
                       "cardinality": "many_to_one"}],
            "metrics": [{"metric_id": "m", "op": "rate", "source_id": "shops",
                         "denominator_source_id": "pop", "link_id": "l1"}],
            "charts": [], "conditions": [], "base_scope_note": "",
        }
        with pytest.raises(ValueError, match="不一致"):
            contract.ExplorationPlan.model_validate(raw)

    def test_duplicate_ref_bindings_rejected(self):
        """A-P3-9：同一 ref 双 source_id 绑定 → 拒绝（导出改写图层
        source 键后第二源必然失配）。"""
        anchor = {"ref_id": "ref:x", "content_revision": 1}
        raw = {
            "schema_version": 3,
            "sources": [
                {"source_id": "a", "anchor": anchor, "role": "primary"},
                {"source_id": "b", "anchor": dict(anchor), "role": "context"},
            ],
            "links": [], "metrics": [], "charts": [], "conditions": [],
            "base_scope_note": "",
        }
        with pytest.raises(ValueError, match="同时绑定"):
            contract.ExplorationPlan.model_validate(raw)


@pytest.mark.asyncio
class TestMultiSourceExport:
    async def test_export_rejects_unprojected_and_discloses_not_on_map(self):
        """A-P2-2/B-P1：复合键传播（不可投影）→ EXPORT_SCOPE_NOT_PROJECTABLE
        拒绝导出（修复前：own 无条件时守卫死代码 → 未过滤图面照常出版）。"""
        from app.services.session_data import session_data_manager

        sid = "ms-review-export"
        actor = {**ACTOR, "trusted_session_id": sid}
        refs = await _seed(sid, actor)
        try:
            # 复合键传播链 A→B；全域无条件 → A-P2-1 后无传播限制（键集空
            # 不成立）——改用条件让 B 有真实过滤，但 B→C 复合键传播，C own
            # 无条件 → C 的 map_filter=None 且 projected=False（修复前会以
            # 未过滤图面出版）。导出链需要会话有 MapSpec（先存最小图面）。
            from app.services.mapspec_store import mapspec_store
            base = await mapspec_store.get_mapspec(sid) or {
                "version": "2",
                "view": {"bounds": [116.0, 38.9, 116.8, 39.1]},
                "layers": [], "layout": {"components": []},
            }
            base = dict(base)
            # a 有层（own 条件可过滤通过）；b/c 无层 —— b 被传播过滤且无层
            # 会先触发 EXPORT_NO_TARGET_LAYER？b own 无条件且入边 no-op
            # （A-P2-1 后惰性链无限制）→ b 无过滤 → 不触发；c 不可投影 →
            # 走 EXPORT_SCOPE_NOT_PROJECTABLE
            base["layers"] = [
                {"id": "layer-a", "type": "fill",
                 "source": {"ref_id": refs["a"][0]},
                 "paint": {"fill-color": "#3388aa", "fill-opacity": 0.7}},
                {"id": "layer-b", "type": "fill",
                 "source": {"ref_id": refs["b"][0]},
                 "paint": {"fill-color": "#aa8833", "fill-opacity": 0.7}},
            ]
            await mapspec_store.save_mapspec(sid, base)
            plan = _chain_plan(refs)
            plan["links"][1]["keys"] = [
                {"left_field": "key", "right_field": "key"},
                {"left_field": "_wfid", "right_field": "_wfid"},
            ]
            plan["charts"] = [{"chart_id": "ch-a", "kind": "category",
                               "field": "kind", "source_id": "a"}]
            plan["conditions"] = [{
                "kind": "category", "condition_id": "cond-ch-a",
                "chart_id": "ch-a", "field": "kind", "values": ["keep"]}]
            out = await service.preview_plan(sid, plan, actor)
            # B：own 无条件但 A 有条件 → 键集 {a1,b9} 生效；C：入边复合键
            assert out["per_source"]["c"]["map_filter_projected"] is False

            from app.services.linked_exploration.errors import \
                LinkedExplorationError as _E
            from app.services.linked_exploration.export import export_exploration
            with pytest.raises(_E) as ei:
                await export_exploration(
                    sid, actor, fmt="svg", plan_raw=plan, title="t")
            assert ei.value.code == "EXPORT_SCOPE_NOT_PROJECTABLE", ei.value
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(sid)

    async def test_export_multi_source_layers_and_annotation(self):
        """多源导出正路：两层 MapSpec + 逐源过滤 → 导出成功；manifest
        含逐源口径；未上图源（c：无层引用且无过滤）披露不拒绝。"""
        from app.services.export_paths import exports_root
        from app.services.linked_exploration.export import export_exploration
        from app.services.mapspec_store import mapspec_store
        from app.services.session_data import session_data_manager

        sid = "ms-review-export-ok"
        actor = {**ACTOR, "trusted_session_id": sid}
        refs = await _seed(sid, actor)
        try:
            base = await mapspec_store.get_mapspec(sid) or {
                "version": "2",
                "view": {"bounds": [116.0, 38.9, 116.8, 39.1]},
                "layers": [], "layout": {"components": []},
            }
            base = dict(base)
            base["layers"] = [
                {"id": "layer-a", "type": "fill",
                 "source": {"ref_id": refs["a"][0]},
                 "paint": {"fill-color": "#3388aa", "fill-opacity": 0.7}},
                {"id": "layer-b", "type": "fill",
                 "source": {"ref_id": refs["b"][0]},
                 "paint": {"fill-color": "#aa8833", "fill-opacity": 0.7}},
            ]
            await mapspec_store.save_mapspec(sid, base)

            plan = _chain_plan(refs)   # 全域无条件 → 全部 no-op 传播
            result = await export_exploration(
                sid, actor, fmt="svg", plan_raw=plan, title="多源导出")
            assert result["success"] is True, result
            # 逐源图层过滤：A/B 各自命中（同 ref 改写不串层）
            assert set(result["filtered_layer_ids"]) == {"layer-a", "layer-b"}
            manifest_url = result["manifest_url"]
            fname = manifest_url.rsplit("/", 1)[-1].split("?")[0]
            candidates = list(Path(exports_root()).rglob(fname))
            assert candidates, "manifest not found"
            manifest = json.loads(candidates[0].read_text(encoding="utf-8"))
            assert manifest.get("multi_source") is True
            per_source = manifest.get("per_source") or {}
            assert per_source["a"]["match_count"] == 2
            assert per_source["b"]["match_count"] == 3
            assert per_source["c"]["match_count"] == 3
            # c 无层引用且无过滤 → 未上图披露（非拒绝、非静默）
            assert "c" in (manifest.get("not_on_map_sources") or [])
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(sid)
