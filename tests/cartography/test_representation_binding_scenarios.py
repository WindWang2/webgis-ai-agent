"""混合主动制图一致性（ADR-0219 C3/C4）——端到端场景测试。

覆盖 Requirement ↔ MapSpec 双向一致性闭环的九个用户/agent 交互场景：

- C4 语义投影（apply_gis_mutation post-commit，只信服务端已提交 receipt）：
  user 显隐/锁/组件开关投影回 RequirementDocument（绝对值集语义）；
  pan/zoom（presentation_ephemeral）与 superseded 拒笔不污染 requirement；
  undo（RollbackIntent）按提交后 spec 重定基，不留幽灵锁。
- C3 绑定回放（apply_pending_bindings）：requirement 用户显式表达面经
  正式事务（apply_gis_mutation）落地/对账——roads 不复活、palette 走
  grammar face pin（不产生 mutation）、legend 组件禁用再落地。
- CAS/幂等纪律：双 tab 同基线提交后到者 superseded；同 mutation_id 重放
  duplicate（spec/requirement 双侧均不再推进）。

每测试独立 clean_session；全部同步因果链编排，零 sleep。
"""
import shutil
import uuid

import pytest

from app.services.gis_harness.requirement_ir import service as requirement_service
from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
    apply_pending_bindings,
)
from app.services.gis_world_state.mutation import apply_gis_mutation
from app.services.mapspec.intents import (
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    RollbackIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager

_QUERY = "成都小学分布图"


@pytest.fixture
async def clean_session():
    sid = f"rbs-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _layer(layer_id: str) -> dict:
    return {
        "id": layer_id,
        "source": f"{layer_id}-src",
        "type": "circle",
        "paint": {"opacity": 0.9},
    }


def _geojson() -> dict:
    return {"type": "geojson", "features": []}


def _workbench_doc(locked: list) -> dict:
    """workbench v5 最小合法形状（_workbench_doc_error 校验面）。"""
    return {
        "version": 5,
        "groups": [],
        "membership": {},
        "rootMembers": [],
        "lockedLayerIds": list(locked),
        "mode": "explore",
    }


async def _upsert_roads(sid: str, actor: str = "tool:seed"):
    """agent 落 roads 层（每笔 auto-checkpoint），返回完整回执。"""
    res = await apply_gis_mutation(
        sid,
        UpsertLayerIntent(layer=_layer("roads"), source_data=_geojson()),
        origin="agent", actor=actor,
    )
    assert not res.is_error, res.error_msg
    return res


async def _current_revision(sid: str) -> int:
    state = await session_data_manager.get_map_state(sid)
    return int(state.get("_cartographic_mutation_revision") or 0)


async def _spec(sid: str) -> dict:
    state = await session_data_manager.get_map_state(sid)
    return state.get("mapspec") or {}


def _spec_layer(spec: dict, layer_id: str) -> dict:
    return next(
        (ly for ly in spec.get("layers", []) if ly.get("id") == layer_id), {})


async def _requirement_doc(sid: str) -> RequirementDocument:
    """权威断言面：map_state 持久文档 → RequirementDocument.model_validate。"""
    state = await session_data_manager.get_map_state(sid)
    payload = state[requirement_service.REQUIREMENT_STATE_KEY]
    return RequirementDocument.model_validate(payload["document"])


async def _ensure_document(sid: str) -> None:
    out = await requirement_service.ensure_document(
        session_data_manager, sid, _QUERY, turn=1)
    assert out["created"] is True, out


@pytest.mark.cartography
class TestMixedInitiativeBindingScenarios:
    # ── 场景 1：roads 不复活（user hide → agent 同 id 重刷 → 绑定回放） ──

    async def test_roads_hidden_survives_agent_reupsert(self, clean_session):
        """用户隐藏 roads 后，agent 同 id 重跑 upsert 不得复活该层
        （durable 印记继承 + C3 绑定回放 applied 含 roads 隐藏绑定）。"""
        sid = clean_session
        await _ensure_document(sid)
        seed = await _upsert_roads(sid)

        hide = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route",
            expected_revision=seed.mutation_revision, mutation_id="c:h1",
        )
        assert not hide.is_error, hide.error_msg

        # C4 投影：requirement 记录用户隐藏。
        doc = await _requirement_doc(sid)
        assert "roads" in doc.intent.representation.hidden_layer_ids

        # agent 重跑同 id upsert（数据刷新）：用户呈现决策必须保留。
        reup = await _upsert_roads(sid, actor="tool:replan")
        assert not reup.is_error, reup.error_msg
        roads = _spec_layer(await _spec(sid), "roads")
        assert roads.get("layout", {}).get("visibility") == "none"
        intent = roads.get("cartographic_intent") or {}
        assert intent.get("presentation_owner") == "user"
        assert intent.get("expected_visible") is False

        # C3 绑定回放：requirement 隐藏语义经正式事务对账落地。
        report = await apply_pending_bindings(sid)
        assert report["document"] == "ok"
        roads_bindings = [
            b for b in report["applied"]
            if b["payload"].get("layer_id") == "roads"
        ]
        assert roads_bindings, report
        assert roads_bindings[0]["target_kind"] == "PatchLayerPresentationIntent"
        assert roads_bindings[0]["payload"]["visible"] is False
        assert roads_bindings[0]["source_requirement_path"] == (
            "representation.hidden_layer_ids")
        # 绑定后权威 spec 仍为隐藏（agent mutation 不触发反向投影）。
        assert _spec_layer(await _spec(sid), "roads")["layout"]["visibility"] == "none"

    # ── 场景 2：palette pin 保留（grammar face，不产生 mutation） ─────────

    async def test_palette_pin_survives_as_grammar_face(self, clean_session):
        """用户 pin 色带（"蓝色"）→ 归一化 "blue" 进 grammar_pins；
        grammar face 不构造 mutation，applied 列表不得出现 palette 条目。"""
        sid = clean_session
        await _ensure_document(sid)

        out = await requirement_service.apply_user_patches(
            session_data_manager, sid,
            [requirement_service.TurnPatch(
                op="set", path="representation.palette", value="蓝色",
                actor="user", op_id="op-palette", turn=2, reason="pin palette")],
        )
        assert out["applied"] == 1, out

        # 写边界归一化：文档值即规范形。
        doc = await _requirement_doc(sid)
        assert doc.intent.representation.palette == "blue"

        report = await apply_pending_bindings(sid)
        assert report["document"] == "ok"
        assert report["grammar_pins"].get("pinned_palette") == "blue"
        # grammar face 是 pin 不是 mutation：本场景无任何 mutation-face 绑定。
        assert report["applied"] == [], report
        assert all(
            b.get("source_requirement_path") != "representation.palette"
            for bucket in ("applied", "refused", "superseded", "skipped")
            for b in report[bucket]
        )

    # ── 场景 3：legend 组件禁用（user 面 requirement 权威 + 绑定再落地） ──

    async def test_legend_disable_projected_and_rebound(self, clean_session):
        """用户禁用 legend 组件 → requirement components.legend=False；
        agent 再启用引擎允许（spec 面翻转），requirement 仍以 user 面为
        准；apply_pending_bindings 对在场 legend 实例产生禁用绑定并落地。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)

        create = await apply_gis_mutation(
            sid, PatchComponentIntent(
                component_id="legend", component_type="legend",
                enabled=True, upsert=True),
            origin="agent", actor="tool:chrome",
        )
        assert not create.is_error, create.error_msg

        disable = await apply_gis_mutation(
            sid, PatchComponentIntent(
                component_id="legend", component_type="legend", enabled=False),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:lg-off",
        )
        assert not disable.is_error, disable.error_msg

        doc = await _requirement_doc(sid)
        assert doc.intent.components.legend is False

        # agent 同意图 enabled=True：组件开关不在 user-wins 呈现守卫面，
        # 引擎放行（如实断言）——requirement 仍保留用户的 False。
        reenable = await apply_gis_mutation(
            sid, PatchComponentIntent(
                component_id="legend", component_type="legend", enabled=True),
            origin="agent", actor="tool:chrome",
        )
        assert not reenable.is_error, reenable.error_msg
        spec = await _spec(sid)
        legend_entries = [
            c for c in (spec.get("layout") or {}).get("components") or []
            if c.get("type") == "legend"
        ]
        assert legend_entries and legend_entries[0].get("enabled") is True
        doc = await _requirement_doc(sid)
        assert doc.intent.components.legend is False

        # C3：组件在场 → 禁用绑定落地，spec 收敛回用户语义。
        report = await apply_pending_bindings(sid)
        legend_bindings = [
            b for b in report["applied"]
            if b["source_requirement_path"] == "components.legend"
        ]
        assert legend_bindings, report
        assert legend_bindings[0]["target_kind"] == "PatchComponentIntent"
        assert legend_bindings[0]["payload"]["enabled"] is False
        spec = await _spec(sid)
        legend_entries = [
            c for c in (spec.get("layout") or {}).get("components") or []
            if c.get("type") == "legend"
        ]
        assert legend_entries[0].get("enabled") is False
        # agent 绑定不触发反向投影：requirement 面不变。
        doc = await _requirement_doc(sid)
        assert doc.intent.components.legend is False

    # ── 场景 4：workbench 锁（agent 被拒 / user 解锁后放行） ──────────────

    async def test_workbench_lock_blocks_agent_until_user_unlocks(
        self, clean_session,
    ):
        """用户锁 roads → requirement locked_layer_ids 记录；agent 显隐
        突变被 layer_locked 拒绝；用户解锁后同一 agent 意图放行。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)

        lock = await apply_gis_mutation(
            sid, SetWorkbenchStateIntent(
                doc=_workbench_doc(["roads"]), base_workbench_revision=None),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:lock",
        )
        assert not lock.is_error, lock.error_msg

        doc = await _requirement_doc(sid)
        assert doc.intent.representation.locked_layer_ids == ("roads",)

        show_locked = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=True),
            origin="agent", actor="tool:replan",
        )
        assert show_locked.is_error
        assert show_locked.error_code == "layer_locked"
        assert show_locked.locked_layer_ids == ["roads"]

        unlock = await apply_gis_mutation(
            sid, SetWorkbenchStateIntent(
                doc=_workbench_doc([]), base_workbench_revision=None),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:unlock",
        )
        assert not unlock.is_error, unlock.error_msg

        # 解锁投影为绝对值集：requirement 锁面清空。
        doc = await _requirement_doc(sid)
        assert doc.intent.representation.locked_layer_ids == ()

        show = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=True),
            origin="agent", actor="tool:replan",
        )
        assert not show.is_error, show.error_msg
        assert _spec_layer(await _spec(sid), "roads")["layout"]["visibility"] == (
            "visible")

    # ── 场景 5：pan/zoom 不污染 requirement ───────────────────────────────

    async def test_setview_does_not_touch_requirement(self, clean_session):
        """用户 pan/zoom（SetViewIntent，CAS 提交成功）是
        presentation_ephemeral：requirement revision 与表达面均不变。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)
        doc_before = await _requirement_doc(sid)

        view = await apply_gis_mutation(
            sid, SetViewIntent(center=[104.0, 30.6], zoom=11.0),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:view",
        )
        assert not view.is_error, view.error_msg

        doc_after = await _requirement_doc(sid)
        assert doc_after.revision == doc_before.revision
        assert doc_after.intent.representation == (
            doc_before.intent.representation)

    # ── 场景 6：CAS 拒笔（superseded）不投影 ──────────────────────────────

    async def test_superseded_optimistic_write_not_projected(self, clean_session):
        """过期 expected_revision 的 user 显隐提交 → superseded（非提交）；
        requirement hidden 集与 revision 均不得变化。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)
        current = await _current_revision(sid)
        stale_revision = max(current - 1, 0)
        doc_before = await _requirement_doc(sid)

        stale = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route",
            expected_revision=stale_revision, mutation_id="c:stale",
        )
        assert stale.superseded is True
        assert not stale.is_error

        doc_after = await _requirement_doc(sid)
        assert doc_after.revision == doc_before.revision
        assert doc_after.intent.representation.hidden_layer_ids == ()

    # ── 场景 7：undo 重定基（回滚不留幽灵锁） ─────────────────────────────

    async def test_rollback_rebases_locks_without_ghost(self, clean_session):
        """锁定 roads 后用户 rollback 到锁前 checkpoint：undo 重定基按
        提交后 spec 重算锁集/隐藏集（重定基清除），agent 显隐恢复放行。"""
        sid = clean_session
        await _ensure_document(sid)
        seed = await _upsert_roads(sid)
        # 锁前快照：upsert 的 auto-checkpoint；缺席则显式补一笔（锁前）。
        checkpoint_id = seed.checkpoint_id
        if not checkpoint_id:
            from app.services.mapspec.intents import CheckpointIntent

            ckpt = await apply_gis_mutation(
                sid, CheckpointIntent(), origin="agent", actor="tool:seed",
            )
            assert not ckpt.is_error, ckpt.error_msg
            checkpoint_id = ckpt.checkpoint_id
        assert checkpoint_id

        lock = await apply_gis_mutation(
            sid, SetWorkbenchStateIntent(
                doc=_workbench_doc(["roads"]), base_workbench_revision=None),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:lock",
        )
        assert not lock.is_error, lock.error_msg
        doc = await _requirement_doc(sid)
        assert doc.intent.representation.locked_layer_ids == ("roads",)

        rollback = await apply_gis_mutation(
            sid, RollbackIntent(checkpoint_id=checkpoint_id),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:undo",
        )
        assert not rollback.is_error, rollback.error_msg

        # 重定基：requirement 锁面/隐藏面按回滚后权威 spec 清空。
        doc = await _requirement_doc(sid)
        assert doc.intent.representation.locked_layer_ids == ()
        assert doc.intent.representation.hidden_layer_ids == ()

        # 无幽灵锁：同一 agent 显隐意图恢复放行。
        show = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=True),
            origin="agent", actor="tool:replan",
        )
        assert not show.is_error, show.error_msg

    # ── 场景 8：双 tab CAS（后到写 superseded） ────────────────────────────

    async def test_two_tab_same_baseline_second_superseded(self, clean_session):
        """双 tab 同基线：tab A hide 先提交；tab B 以同一过期基线 show →
        superseded；requirement 只反映 tab A 的 hide。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)
        baseline = await _current_revision(sid)

        hide = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="tab-a",
            expected_revision=baseline, mutation_id="c:tab-a",
        )
        assert not hide.is_error, hide.error_msg

        show = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=True),
            origin="user", actor="tab-b",
            expected_revision=baseline, mutation_id="c:tab-b",
        )
        assert show.superseded is True
        assert not show.is_error

        doc = await _requirement_doc(sid)
        assert doc.intent.representation.hidden_layer_ids == ("roads",)
        assert _spec_layer(await _spec(sid), "roads")["layout"]["visibility"] == (
            "none")

    # ── 场景 9：duplicate mutation_id 幂等（spec/requirement 双侧） ────────

    async def test_duplicate_mutation_id_replay_is_idempotent(
        self, clean_session,
    ):
        """同 mutation_id 重放（携带新 expected_revision）：引擎 dedup 命中
        duplicate=True；spec revision 与 requirement 隐藏面/revision 均不变。"""
        sid = clean_session
        await _ensure_document(sid)
        await _upsert_roads(sid)

        hide = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route",
            expected_revision=await _current_revision(sid),
            mutation_id="c:dup",
        )
        assert not hide.is_error and not hide.duplicate
        revision_after_commit = await _current_revision(sid)
        doc_after_commit = await _requirement_doc(sid)
        assert doc_after_commit.intent.representation.hidden_layer_ids == (
            "roads",)

        replay = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="roads", visible=False),
            origin="user", actor="mapspec_route",
            expected_revision=revision_after_commit, mutation_id="c:dup",
        )
        assert replay.duplicate is True
        assert not replay.is_error and not replay.superseded
        assert replay.mutation_revision == revision_after_commit

        assert await _current_revision(sid) == revision_after_commit
        doc = await _requirement_doc(sid)
        assert doc.revision == doc_after_commit.revision
        assert doc.intent.representation.hidden_layer_ids == ("roads",)
