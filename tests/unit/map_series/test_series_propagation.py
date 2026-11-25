"""传播测试 —— preview 只读 / 逐图 receipts / 单图失败不中断 / superseded / user-wins。

真实引擎端到端：seed → preview → apply → 断言成员会话 MapSpec 真实变更。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.map_series import service as ms_service
from app.services.map_series.propagation import (
    apply_propagation,
    build_propagation_preview,
    parse_intents,
)
from app.services.map_series.store import create_series
from tests.unit.map_series.conftest import seed_member_session
from tests.unit.map_series.helpers import series_payload

PALETTE_INTENTS: Dict[str, Any] = {
    "palette": {"target_layer_id": "districts", "color": "#166534"},
}


async def _make_series(db, host_spec_id: str, *, name: str, members: list) -> Any:
    payload = series_payload(
        name=name,
        design_spec_id=host_spec_id,
        members=members,
        comparability={"shared_classification": True},
        dimension={"axis": "region", "labels": []},
    )
    return create_series(db, payload, creator_id="series-owner")


def _member(member_id: str, sid: str, **kw) -> Dict[str, Any]:
    base = {"member_id": member_id, "title": member_id, "session_id": sid}
    base.update(kw)
    return base


@pytest.fixture()
def host_spec(db):
    from app.services.design_specs.versioning import create_spec as create_design_spec

    spec = create_design_spec(
        db,
        {"spec_version": "design_spec.v1", "name": "传播宿主规范"},
        creator_id="series-owner",
    )
    return spec.id


@pytest.mark.asyncio
class TestPropagationPreview:
    async def test_preview_lists_changes_and_is_readonly(self, db, host_spec):
        await seed_member_session("sess-prop-a")
        series = await _make_series(
            db, host_spec, name="预览系列",
            members=[_member("m1", "sess-prop-a")],
        )
        before = await ms_service.load_member_mapspec("sess-prop-a")
        preview = await build_propagation_preview(db, series.id, intents=PALETTE_INTENTS)
        assert preview["unresolvable_member_ids"] == []
        entry = preview["members"][0]
        assert entry["eligible"] is True
        assert entry["changes"][0]["intent"] == "palette"
        assert entry["changes"][0]["component"] == "districts"
        after = await ms_service.load_member_mapspec("sess-prop-a")
        assert before == after  # 只读：preview 零写入
        assert (before or {})["layers"][0]["paint"]["fill-color"] == "#1d4ed8"

    async def test_preview_discloses_locked_and_unresolvable(self, db, host_spec):
        await seed_member_session("sess-prop-b", color="#1d4ed8")
        series = await _make_series(
            db, host_spec, name="预览披露系列",
            members=[
                _member("m1", "sess-prop-b", locked_fields=["palettes"]),
                _member("m2", "sess-prop-missing"),
            ],
        )
        preview = await build_propagation_preview(db, series.id, intents=PALETTE_INTENTS)
        by_id = {m["member_id"]: m for m in preview["members"]}
        assert by_id["m1"]["locked_fields_hit"] == ["palettes"]
        assert by_id["m1"]["eligible"] is False
        assert by_id["m2"]["unresolvable"] is True
        assert "m2" in preview["unresolvable_member_ids"]

    async def test_preview_invalid_intents_rejected(self, db, host_spec):
        await seed_member_session("sess-prop-c")
        await _make_series(
            db, host_spec, name="非法意图系列",
            members=[_member("m1", "sess-prop-c")],
        )
        from app.services.map_series.errors import MapSeriesValidationError

        with pytest.raises(MapSeriesValidationError):
            parse_intents({"palette": {"target_layer_id": "districts",
                                       "color": "not-a-color"}})
        with pytest.raises(MapSeriesValidationError):
            parse_intents({"classification": {"target_layer_id": "districts",
                                              "method": "made_up"}})
        with pytest.raises(MapSeriesValidationError):
            parse_intents({"unknown_intent": {"x": 1}})


@pytest.mark.asyncio
class TestPropagationApply:
    async def test_apply_commits_per_member_and_mutates_real_spec(self, db, host_spec):
        await seed_member_session("sess-apply-a")
        await seed_member_session("sess-apply-b", color="#7c3aed")
        series = await _make_series(
            db, host_spec, name="应用系列",
            members=[_member("m1", "sess-apply-a"), _member("m2", "sess-apply-b")],
        )
        result = await apply_propagation(
            db, series.id, intents=PALETTE_INTENTS, actor="tester"
        )
        assert result["summary"]["committed"] == 2, result
        for receipt in result["receipts"]:
            assert receipt["status"] == "committed"
            assert isinstance(receipt["revision"], int)
        spec_a = await ms_service.load_member_mapspec("sess-apply-a")
        layer_a = next(ly for ly in spec_a["layers"] if ly["id"] == "districts")
        assert layer_a["paint"]["fill-color"] == "#166534"  # 真实落图
        spec_b = await ms_service.load_member_mapspec("sess-apply-b")
        layer_b = next(ly for ly in spec_b["layers"] if ly["id"] == "districts")
        assert layer_b["paint"]["fill-color"] == "#166534"
        # 逐图 receipt 的 revision 是各自会话的 CAS 世代（独立计数器）
        for receipt in result["receipts"]:
            sid = "sess-apply-a" if receipt["member_id"] == "m1" else "sess-apply-b"
            assert receipt["revision"] == await ms_service.member_revision(sid)

    async def test_apply_classification_resets_breaks_and_sets_method(self, db, host_spec):
        await seed_member_session("sess-apply-c", method="quantiles")
        series = await _make_series(
            db, host_spec, name="分类传播系列",
            members=[_member("m1", "sess-apply-c")],
        )
        intents = {
            "classification": {"target_layer_id": "districts", "method": "equal_interval", "k": 5},
        }
        result = await apply_propagation(db, series.id, intents=intents, actor="tester")
        assert result["summary"]["committed"] == 1, result
        spec = await ms_service.load_member_mapspec("sess-apply-c")
        legend = spec["layers"][0]["legend_spec"]
        assert legend["method"] == "equal_interval"
        assert legend["k"] == 5
        assert "breaks" not in legend  # 旧方法的数据断点重置，不跨图复制

    async def test_locked_member_skipped_others_commit(self, db, host_spec):
        await seed_member_session("sess-apply-d")
        await seed_member_session("sess-apply-e")
        series = await _make_series(
            db, host_spec, name="锁定跳过系列",
            members=[
                _member("m1", "sess-apply-d", locked_fields=["palettes"]),
                _member("m2", "sess-apply-e"),
            ],
        )
        result = await apply_propagation(db, series.id, intents=PALETTE_INTENTS, actor="tester")
        assert result["summary"]["skipped_locked"] == 1
        assert result["summary"]["committed"] == 1  # 单成员锁定不中断后续
        by_id = {r["member_id"]: r for r in result["receipts"]}
        assert by_id["m1"]["status"] == "skipped_locked"
        spec_d = await ms_service.load_member_mapspec("sess-apply-d")
        assert spec_d["layers"][0]["paint"]["fill-color"] == "#1d4ed8"  # 锁定成员未被改写

    async def test_single_member_failure_does_not_stop_batch(self, db, host_spec):
        await seed_member_session("sess-apply-f")
        # m2 无目标层存在且会话缺失 → failed；m1 照常 committed
        series = await _make_series(
            db, host_spec, name="失败不中断系列",
            members=[_member("m1", "sess-apply-f"), _member("m2", "sess-gone")],
        )
        result = await apply_propagation(db, series.id, intents=PALETTE_INTENTS, actor="tester")
        assert result["summary"]["committed"] == 1
        assert result["summary"]["failed"] == 1
        by_id = {r["member_id"]: r for r in result["receipts"]}
        assert by_id["m2"]["status"] == "failed"
        assert by_id["m2"]["error"] == "session MapSpec unavailable"
        assert "batch is not atomic" in result["note"]

    async def test_no_applicable_intent_reported_honestly(self, db, host_spec):
        await seed_member_session("sess-apply-g")
        series = await _make_series(
            db, host_spec, name="无可应用意图系列",
            members=[_member("m1", "sess-apply-g")],
        )
        intents = {"palette": {"target_layer_id": "no-such-layer", "color": "#166534"}}
        result = await apply_propagation(db, series.id, intents=intents, actor="tester")
        receipt = result["receipts"][0]
        assert receipt["status"] == "failed"
        assert receipt["error"] == "no_applicable_intent"

    async def test_superseded_receipt_on_stale_expected_revision(self, db, host_spec):
        await seed_member_session("sess-apply-h")
        series = await _make_series(
            db, host_spec, name="过期CAS系列",
            members=[_member("m1", "sess-apply-h")],
        )
        current = await ms_service.member_revision("sess-apply-h")
        stale = int(current or 0) + 7  # 过期的 CAS 期望值
        result = await apply_propagation(
            db, series.id, intents=PALETTE_INTENTS, actor="tester",
            expected_revisions={"m1": stale},
        )
        receipt = result["receipts"][0]
        assert receipt["status"] == "superseded", receipt
        # superseded receipt 的 revision 如实携带引擎回传的当前世代
        assert receipt["revision"] == await ms_service.member_revision("sess-apply-h")
        spec = await ms_service.load_member_mapspec("sess-apply-h")
        assert spec["layers"][0]["paint"]["fill-color"] == "#1d4ed8"  # 未被改写

    async def test_engine_component_lock_reports_skipped_locked(self, db, host_spec):
        # 引擎层 user-wins：workbench 锁 title 组件后组件意图 → skipped_locked
        await seed_member_session("sess-apply-i")
        from app.services.gis_world_state.mutation import apply_gis_mutation
        from app.services.mapspec.intents import SetWorkbenchStateIntent

        res = await apply_gis_mutation(
            "sess-apply-i",
            SetWorkbenchStateIntent(doc={
                "version": 5,
                "groups": [],
                "membership": {},
                "lockedLayerIds": [],
                "lockedComponentIds": ["t"],
                "mode": "explore",
            }),
            origin="agent", actor="tester",
        )
        assert not res.is_error, res.error_msg
        series = await _make_series(
            db, host_spec, name="引擎锁系列",
            members=[_member("m1", "sess-apply-i")],
        )
        intents = {
            "components": [{"component_type": "title", "op": "ensure", "options": {}}],
        }
        result = await apply_propagation(db, series.id, intents=intents, actor="tester")
        by_id = {r["member_id"]: r for r in result["receipts"]}
        assert by_id["m1"]["status"] == "skipped_locked", by_id
