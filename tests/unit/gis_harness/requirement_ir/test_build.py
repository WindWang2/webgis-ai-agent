"""F02 build 层单测：core→sections 确定性派生、多轮 edit→patch、超替携带。

红线断言：sections 是 core 的纯投影（不新造语义）；edit 差分只接受
词面信号（resolver 兜底默认值不得回退既有确认事实）。
"""
from __future__ import annotations


from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    build_document,
    carry_user_state,
    diff_to_patches,
    apply_patch,
    PatchRecord,
)


def _doc(query: str, turn: int = 1):
    return build_document(query, resolve_map_request_intent(query), turn=turn)


class TestDeriveSections:
    def test_admin_statistic_derivation(self):
        doc = _doc("统计成都各区小学数量")
        assert doc.intent.task.task_type == "administrative_statistic"
        assert doc.intent.aoi.name == "成都"
        assert doc.intent.aoi.level == "city"
        assert doc.intent.aoi.resolver_state == "resolved"
        assert doc.intent.statistics.dimension == "administrative"
        assert doc.intent.statistics.group_by == "district"
        assert doc.intent.measures and doc.intent.measures[0].statistic == "count"

    def test_time_derivation(self):
        doc = _doc("分析成都2019到2024年绿化变化趋势")
        assert doc.intent.time.series is True
        assert doc.intent.time.range_start == "2019"

    def test_geometry_ref_pattern(self):
        doc = _doc("统计成都各区小学数量")
        assert doc.intent.aoi.geometry_ref == "local:admin:city:成都"

    def test_deterministic(self):
        a = _doc("统计成都各区小学数量")
        b = _doc("统计成都各区小学数量")
        assert a == b

    def test_provenance_seeded_rule(self):
        doc = _doc("统计成都各区小学数量")
        assert doc.intent.aoi.provenance.origin == "rule"
        assert doc.intent.statistics.provenance.origin == "rule"


class TestDiffToPatches:
    def test_task_upgrade_patch(self):
        doc = _doc("成都的小学分布情况，做一张图")
        patches = diff_to_patches(
            doc, resolve_map_request_intent("改成各区统计"), turn=2,
            query="改成各区统计")
        assert any(p.path == "task.task_type" for p in patches)

    def test_fallback_defaults_do_not_regress(self):
        """edit 语句的 resolver 兜底（distribution_overview / district）
        不得把已升级的 task 拉回去。"""
        doc = _doc("统计成都各区小学数量")
        # 先把 task 升级（模拟用户已确认 administrative_statistic）
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="t1", turn=2, actor="user", op="set",
            path="task.task_type", value="temporal_trend"))
        patches = diff_to_patches(
            doc, resolve_map_request_intent("隐藏道路图层"), turn=3,
            query="隐藏道路图层")
        assert not any(p.path == "task.task_type" for p in patches)

    def test_format_regression_blocked(self):
        doc = _doc("统计成都各区小学数量")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="f1", turn=2, actor="user", op="set",
            path="output.formats", value=["pdf"]))
        patches = diff_to_patches(
            doc, resolve_map_request_intent("隐藏道路图层"), turn=3,
            query="隐藏道路图层")
        assert not any(p.path == "output.formats" for p in patches)

    def test_hide_layer_cue_with_lock(self):
        doc = _doc("统计成都各区小学数量")
        patches = diff_to_patches(
            doc, resolve_map_request_intent("隐藏道路图层"), turn=3,
            query="隐藏道路图层")
        paths = [p.op for p in patches]
        assert "add_lock" in paths
        hide = next(p for p in patches
                    if p.path == "representation.hidden_layer_ids")
        assert "道路" in hide.value

    def test_duplicate_cues_emit_nothing(self):
        doc = _doc("统计成都各区小学数量")
        patches = diff_to_patches(
            doc, resolve_map_request_intent("隐藏道路图层"), turn=3,
            query="隐藏道路图层")
        for patch in patches:
            doc, _applied = apply_patch(doc, patch)
        repeat = diff_to_patches(
            doc, resolve_map_request_intent("隐藏道路图层"), turn=4,
            query="隐藏道路图层")
        # layer 已隐藏且已锁 → 无 set；lock 已在 → 无 add_lock
        assert repeat == []

    def test_locked_field_skipped(self):
        doc = _doc("统计成都各区小学数量")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="l1", turn=2, actor="user", op="add_lock",
            value={"scope": "group_by", "value": "grid"}))
        patches = diff_to_patches(
            doc, resolve_map_request_intent("按街道统计各小学"), turn=3,
            query="改成按街道统计")
        assert not any(p.path == "statistics.group_by" for p in patches)

    def test_expected_revision_chain_sequential(self):
        doc = _doc("成都的小学分布情况，做一张图")
        patches = diff_to_patches(
            doc, resolve_map_request_intent("改成各区统计"), turn=2,
            query="改成各区统计")
        for index, patch in enumerate(patches):
            assert patch.expected_revision == doc.revision + index


class TestCarryUserState:
    def test_locks_and_fields_survive(self):
        doc = _doc("统计成都各区小学数量")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="set",
            path="representation.palette", value="blue"))
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p2", turn=3, actor="user", op="add_lock",
            value={"scope": "palette", "value": "blue"}))
        locks, fields = carry_user_state(doc)
        assert locks and locks[0].scope == "palette"
        assert "representation.palette" in fields

    def test_rule_origin_not_carried_as_user(self):
        doc = _doc("统计成都各区小学数量")
        locks, fields = carry_user_state(doc)
        assert locks == []
        assert all(not prov.is_user() for prov in fields.values())


class TestRebuildWithPatches:
    def test_rebuild_restores_user_locks(self):
        doc = _doc("成都的小学分布情况，做一张图")
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="add_lock",
            value={"scope": "palette", "value": "blue"}))
        locks, _ = carry_user_state(doc)
        fresh = build_document(
            "统计重庆各区医院数量", resolve_map_request_intent("统计重庆各区医院数量"),
            turn=4, carried_locks=locks)
        assert any(lock.scope == "palette" for lock in fresh.intent.locks)
        # 携带的 lock 落到 representation 面（user-wins 存活）
        assert fresh.intent.representation.palette == "blue"
