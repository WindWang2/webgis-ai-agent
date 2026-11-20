"""G16 WP02 —— 要素级标注 authoring 纯模型契约测试。

身份/上限/reset 语义/失效/预览的已知答案；schema roundtrip 与向后兼容
（无 authoring 键 = 旧行为不变）。
"""
import math

import pytest

from app.lib.cartography import label_authoring as la
from app.lib.cartography.mapspec_schema import (
    LabelAuthoringConfig,
    LabelAuthoringOverride,
    MapSpecLayerLabel,
    parse_mapspec,
)


# ── 身份解析 ─────────────────────────────────────────────────────────────
class TestIdentity:
    def test_top_level_id_wins(self):
        fid, key = la.resolve_feature_identity({"id": 42, "properties": {"id": "other"}})
        assert (fid, key) == ("42", None)

    def test_property_chain_order(self):
        # 链按 FEATURE_ID_KEYS 键序扫描（与前后端同源契约），不是 dict 序。
        f = {"properties": {"fid": "f1", "OBJECTID": 7}}
        assert la.resolve_feature_identity(f) == ("7", "OBJECTID")
        f2 = {"properties": {"OBJECTID": 7, "fid": "f1"}}
        assert la.resolve_feature_identity(f2) == ("7", "OBJECTID")

    def test_int_and_str_collapse(self):
        # 既有契约：str() 归一 —— 1 ≡ "1"（显式锁定，不引入第二套语义）。
        assert la.resolve_feature_identity({"id": 1}) == la.resolve_feature_identity({"id": "1"})

    def test_bool_never_identity(self):
        assert la.resolve_feature_identity({"id": True}) == (None, None)

    def test_wfid_chain_compatible(self):
        f = {"properties": {"_wfid": "wf1-abc", "id": 3}}
        assert la.resolve_feature_identity(f) == ("3", "id")

    def test_no_identity(self):
        assert la.resolve_feature_identity({"properties": {"name": "x"}}) == (None, None)
        assert la.resolve_feature_identity("junk") == (None, None)

    def test_find_feature_overrides(self):
        feats = [{"id": "a"}, {"properties": {"OBJECTID": 2}}, {"id": "a"}]
        got = la.find_feature_overrides(feats, ["a", "2", "zz"])
        assert got == {"a": (0, None), "2": (1, "OBJECTID")}


# ── 文字/偏移边界 ────────────────────────────────────────────────────────
class TestBounds:
    def test_text_codepoint_cap(self):
        # 非 BMP（emoji 星外字符）按 code point 计 —— 不半截代理对。
        text = "🎓" * 120
        norm, err = la.normalize_text_override(text)
        assert err is None and len(norm) == 120
        _, err = la.normalize_text_override("x" * 121)
        assert err == "text_too_long"

    def test_text_controls_rejected(self):
        assert la.normalize_text_override("bad\x00id")[1] == "invalid_text_control_char"
        assert la.normalize_text_override("  ")[1] == "invalid_text_empty"
        assert la.normalize_text_override(12)[1] == "invalid_text_type"

    def test_offset_bounds(self):
        ok, err = la.normalize_offset_override({"dx": -24.0, "dy": 24.0})
        assert err is None and ok == {"dx": -24.0, "dy": 24.0}
        assert la.normalize_offset_override({"dx": 24.1, "dy": 0})[1] == "offset_out_of_range"
        assert la.normalize_offset_override({"dx": math.nan, "dy": 0})[1] == "invalid_offset_not_finite"
        assert la.normalize_offset_override({"dx": math.inf, "dy": 0})[1] == "invalid_offset_not_finite"
        assert la.normalize_offset_override({"dx": "3", "dy": 0})[1] == "invalid_offset_type"
        assert la.normalize_offset_override(True)[1] == "invalid_offset_type"

    def test_priority_bounds(self):
        assert la.validate_override_bounds({"priority": 1000}) == []
        assert la.validate_override_bounds({"priority": 1001}) == ["invalid_priority"]
        assert la.validate_override_bounds({"priority": True}) == ["invalid_priority"]


# ── reset 语义（作用域互斥）──────────────────────────────────────────────
class TestResetScopes:
    def _full(self):
        return {
            "featureId": "f1", "idKey": "id", "hidden": True, "pinned": True,
            "offset": {"dx": 1.0, "dy": 2.0}, "text": "自定义", "callout": True,
            "priority": 5, "origin": "user", "sourceRevision": 3,
        }

    def test_visibility_reset_keeps_text_and_position(self):
        after = la.apply_reset_scope(self._full(), "visibility")
        assert "hidden" not in after
        assert after["text"] == "自定义" and after["pinned"] is True
        assert after["offset"] == {"dx": 1.0, "dy": 2.0}

    def test_position_reset_keeps_hidden_and_text(self):
        after = la.apply_reset_scope(self._full(), "position")
        assert after["hidden"] is True and after["text"] == "自定义"
        assert "pinned" not in after and "offset" not in after and "callout" not in after

    def test_text_reset_keeps_hidden_and_position(self):
        after = la.apply_reset_scope(self._full(), "text")
        assert after["hidden"] is True and after["pinned"] is True
        assert "text" not in after

    def test_all_reset_removes_entry(self):
        assert la.apply_reset_scope(self._full(), "all") is None

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValueError):
            la.apply_reset_scope(self._full(), "everything")

    def test_metadata_survives_scoped_reset(self):
        after = la.apply_reset_scope(self._full(), "position")
        assert after["origin"] == "user" and after["sourceRevision"] == 3


# ── 预览（before/after + 拒绝路径）───────────────────────────────────────
class TestPreview:
    def test_set_creates_and_merges(self):
        cur = [{"featureId": "a", "hidden": True, "origin": "user"}]
        ops = [
            {"featureId": "a", "op": "set", "set": {"text": "重点学校"}},
            {"featureId": "b", "op": "set", "set": {"pinned": True, "offset": {"dx": 1, "dy": 0}}},
        ]
        pv = la.preview_authoring_ops(cur, ops, layer_id="L")
        assert pv.change_count == 2
        by = {r.feature_id: r for r in pv.reports}
        assert by["a"].after["text"] == "重点学校" and by["a"].after["hidden"] is True
        assert by["b"].after["pinned"] is True
        nxt = pv.next_overrides
        assert {o["featureId"] for o in nxt} == {"a", "b"}

    def test_patch_cannot_touch_identity_metadata(self):
        ops = [{"featureId": "a", "op": "set",
                "set": {"featureId": "EVIL", "origin": "agent", "text": "t"}}]
        pv = la.preview_authoring_ops([{"featureId": "a", "hidden": True, "origin": "user"}],
                                      ops, layer_id="L")
        after = next(r for r in pv.reports if r.feature_id == "a").after
        assert after["featureId"] == "a" and after["origin"] == "user"

    def test_invalid_patch_rejected_not_partially_applied(self):
        ops = [{"featureId": "a", "op": "set", "set": {"text": "x" * 200}}]
        pv = la.preview_authoring_ops([], ops, layer_id="L")
        assert pv.reports[0].action == "rejected"
        assert "text_too_long" in pv.reports[0].findings
        assert pv.next_overrides is None  # 无任何变更

    def test_known_features_missing_target_rejected(self):
        ops = [{"featureId": "ghost", "op": "set", "set": {"hidden": True}}]
        pv = la.preview_authoring_ops([], ops, layer_id="L", known_features=[{"id": "a"}])
        assert pv.reports[0].action == "rejected"
        assert "feature_not_found" in pv.reports[0].findings
        assert pv.unresolved_targets == 1 and pv.next_overrides is None

    def test_known_features_absent_passes_unverified(self):
        # ref 源数据未在场：不拒绝（消费侧复核才是权威），标记留给调用方。
        ops = [{"featureId": "ghost", "op": "set", "set": {"hidden": True}}]
        pv = la.preview_authoring_ops([], ops, layer_id="L", known_features=None)
        assert pv.reports[0].action == "set"

    def test_override_cap(self):
        cur = [{"featureId": f"f{i}", "hidden": True} for i in range(256)]
        ops = [{"featureId": "new", "op": "set", "set": {"hidden": True}}]
        pv = la.preview_authoring_ops(cur, ops, layer_id="L")
        assert "authoring_limit_exceeded" in pv.reports[0].findings

    def test_ops_cap(self):
        ops = [{"featureId": "a", "op": "set", "set": {"hidden": True}}] * 201
        pv = la.preview_authoring_ops([], ops, layer_id="L")
        assert "op_limit_exceeded" in pv.findings and pv.next_overrides is None

    def test_set_to_empty_removes_entry(self):
        cur = [{"featureId": "a", "hidden": True}]
        ops = [{"featureId": "a", "op": "set", "set": {"hidden": None}}]
        pv = la.preview_authoring_ops(cur, ops, layer_id="L")
        assert pv.reports[0].after is None
        assert pv.next_overrides == []

    def test_reset_missing_override_noop(self):
        pv = la.preview_authoring_ops(
            [], [{"featureId": "a", "op": "reset", "scope": "text"}], layer_id="L")
        assert pv.reports[0].action == "noop" and pv.next_overrides is None


# ── 失效状态 ─────────────────────────────────────────────────────────────
class TestStatus:
    def test_orphan(self):
        assert la.override_status(
            {"sourceRevision": 1}, feature_found=False,
            current_revision=1, label_field="name", feature_properties={}) == "orphan"

    def test_stale(self):
        st = la.override_status(
            {"sourceRevision": 1}, feature_found=True,
            current_revision=2, label_field="name", feature_properties={"name": "x"})
        assert st == "stale"

    def test_field_missing_without_text_override(self):
        st = la.override_status(
            {}, feature_found=True, current_revision=None,
            label_field="name", feature_properties={"name": None})
        assert st == "field_missing"

    def test_field_missing_with_text_override_still_active(self):
        st = la.override_status(
            {"text": "显式文字"}, feature_found=True, current_revision=None,
            label_field="name", feature_properties={"name": None})
        assert st == "active"

    def test_active(self):
        assert la.override_status(
            {"sourceRevision": 2}, feature_found=True, current_revision=2,
            label_field="name", feature_properties={"name": "x"}) == "active"


# ── 单位换算 ─────────────────────────────────────────────────────────────
class TestUnits:
    def test_em_px_roundtrip(self):
        assert la.em_to_px(1.5, 16.0) == 24.0
        assert abs(la.px_to_em(24.0, 16.0) - 1.5) < 1e-12
        assert la.px_to_em(10.0, 0.0) == 0.0  # 防除零


# ── schema roundtrip / 兼容 ─────────────────────────────────────────────
class TestSchema:
    def _spec(self, **label_extra):
        return {
            "version": "1.0",
            "layers": [{"id": "schools", "type": "circle", "source": "src",
                        "label": {"field": "name", **label_extra}}],
            "layout": {"labels": {}},
        }

    def test_roundtrip_lossless(self):
        label = MapSpecLayerLabel.model_validate(
            {"field": "name", "authoring": {"overrides": [
                {"featureId": "1", "idKey": "id", "hidden": True,
                 "offset": {"dx": -1.25, "dy": 0.75}, "text": "市第一中学",
                 "origin": "user", "sourceRevision": 4}]}})
        dumped = label.model_dump(exclude_none=True)
        again = MapSpecLayerLabel.model_validate(dumped)
        assert again == label
        assert dumped["authoring"]["overrides"][0]["offset"] == {"dx": -1.25, "dy": 0.75}

    def test_old_spec_without_authoring_unchanged(self):
        r = parse_mapspec(self._spec())
        assert r.valid and not r.unknown_fields and not r.invalid_fields
        layer = r.document["layers"][0]
        assert "authoring" not in layer["label"]

    def test_authoring_spec_parses_valid(self):
        r = parse_mapspec(self._spec(authoring={"overrides": [
            {"featureId": "9", "pinned": True}]}))
        assert r.valid and r.effective_version == "2.1"
        assert r.document["layers"][0]["label"]["authoring"]["overrides"][0]["pinned"] is True

    def test_labels_config_new_keys(self):
        spec = self._spec()
        spec["layout"]["labels"] = {
            "collision": "deterministic", "obstaclePolicy": "geometry_v1",
            "forbiddenZones": [{"id": "z", "ring": [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]}],
        }
        r = parse_mapspec(spec)
        assert r.valid
        assert r.document["layout"]["labels"]["obstaclePolicy"] == "geometry_v1"

    def test_bad_obstacle_policy_disclosed_invalid(self):
        spec = self._spec()
        spec["layout"]["labels"] = {"obstaclePolicy": "geometry_v2"}
        r = parse_mapspec(spec)
        assert not r.valid
        assert any(d.path.endswith("obstaclePolicy") for d in r.invalid_fields)

    def test_override_cap_enforced_by_schema(self):
        with pytest.raises(Exception):
            LabelAuthoringConfig(overrides=[
                LabelAuthoringOverride(featureId=f"f{i}") for i in range(257)])

    def test_deterministic_serialization_order(self):
        cur = [{"featureId": "b", "hidden": True}, {"featureId": "a", "pinned": True}]
        s1 = la.sort_overrides_stable(cur)
        s2 = la.sort_overrides_stable(list(reversed(cur)))
        assert [o["featureId"] for o in s1] == ["a", "b"] == [o["featureId"] for o in s2]
