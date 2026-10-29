"""Feature-edit 核心纯函数测试（M2）：journal/identity/attr/geometry/plan。

数值 oracle 独立可复现（手工推算），不依赖实现内部。
"""
import math


from app.services.feature_edit.attr_ops import profile_fields, validate_set_fields
from app.services.feature_edit.contracts import WFID_PROP
from app.services.feature_edit.geometry_ops import (
    geodesic_distance_m,
    move_by,
    repair_geometry,
    snap_to_ref,
)
from app.services.feature_edit.identity import (
    has_wfids,
    materialize_wfids,
    resolve_feature_ids,
    wfid_for_index,
)
from app.services.feature_edit.journal import (
    append_entry,
    apply_deltas_to_features,
    apply_schema_ops_to_features,
    entry_for_idempotency_key,
    head_entry,
    invert_entry,
    invert_schema_ops,
    new_journal,
)
from app.services.feature_edit.plan import build_plan, derive_changes, plan_safe_classification


# ─── fixtures ──────────────────────────────────────────────────────────────

def school_fc(n: int = 5) -> dict:
    features = []
    for i in range(n):
        features.append(
            {
                "type": "Feature",
                "id": f"sch-{i}",
                "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
                "properties": {"name": f"学校{i}", "type": "小学" if i % 2 == 0 else "中学", "capacity": 500 + i},
            }
        )
    return {"type": "FeatureCollection", "features": features}


FP = "fp-test-0001"


def materialized_fc() -> dict:
    fc, _ = materialize_wfids(school_fc(), FP)
    return fc


# ─── journal ───────────────────────────────────────────────────────────────

class TestJournal:
    def test_append_assigns_seq_and_invert_roundtrip(self):
        j = new_journal()
        delta = {"wfid": "wf1-aaa", "fields": {"type": {"before": "中学", "after": "小学"}}}
        j = append_entry(j, {"kind": "apply", "deltas": [delta], "schema_ops": [],
                             "fingerprint_before": "a", "fingerprint_after": "b",
                             "content_revision_before": 1, "content_revision_after": 2})
        assert j["head_seq"] == 1 and j["entries"][0]["seq"] == 1
        inv = invert_entry(j["entries"][0])
        assert inv["kind"] == "undo" and inv["deltas"][0]["fields"]["type"] == {
            "before": "小学", "after": "中学"
        }
        assert inv["fingerprint_before"] == "b" and inv["fingerprint_after"] == "a"

    def test_idempotency_lookup_latest_first(self):
        j = new_journal()
        for i, key in enumerate(["k1", "k2", "k1"]):
            j = append_entry(j, {"kind": "apply", "idempotency_key": key, "deltas": [],
                                 "schema_ops": [], "fingerprint_before": str(i), "fingerprint_after": str(i + 1),
                                 "content_revision_before": i, "content_revision_after": i + 1})
        hit = entry_for_idempotency_key(j, "k1")
        assert hit["seq"] == 3  # 最新命中

    def test_fifo_bound_and_dropped_disclosure(self):
        j = new_journal()
        for i in range(140):
            j = append_entry(j, {"kind": "apply", "idempotency_key": f"k{i}", "deltas": [],
                                 "schema_ops": [], "fingerprint_before": str(i), "fingerprint_after": str(i + 1),
                                 "content_revision_before": i, "content_revision_after": i + 1})
        assert len(j["entries"]) == 128
        assert j["dropped"] == 12
        assert j["entries"][0]["seq"] == 13  # 丢最旧，seq 连续可审计
        assert head_entry(j)["seq"] == 140

    def test_apply_deltas_cow_only_targets(self):
        fc = materialized_fc()
        wfids = [f["properties"][WFID_PROP] for f in fc["features"]]
        deltas = [{"wfid": wfids[1], "fields": {"type": {"before": "中学", "after": "小学"}}}]
        out, applied = apply_deltas_to_features(fc["features"], deltas)
        assert applied == [wfids[1]]
        assert out[1]["properties"]["type"] == "小学"
        assert fc["features"][1]["properties"]["type"] == "中学"  # 入参未被改
        # 其他要素对象原样（引用相等 = 未复制）
        assert out[0] is fc["features"][0]

    def test_rename_roundtrip(self):
        fc = materialized_fc()
        out, changed = apply_schema_ops_to_features(
            fc["features"], [{"op": "rename_field", "from": "type", "to": "类别"}]
        )
        assert changed and out[0]["properties"]["类别"] == "小学" and "type" not in out[0]["properties"]
        back, _ = apply_schema_ops_to_features(
            out, invert_schema_ops([{"op": "rename_field", "from": "type", "to": "类别"}])
        )
        assert back[0]["properties"]["type"] == "小学"


# ─── identity ──────────────────────────────────────────────────────────────

class TestIdentity:
    def test_materialize_deterministic_and_idempotent(self):
        fc = school_fc()
        m1, n1 = materialize_wfids(fc, FP)
        m2, n2 = materialize_wfids(fc, FP)
        assert n1 == 5 and n2 == 5
        assert [f["properties"][WFID_PROP] for f in m1["features"]] == [
            f["properties"][WFID_PROP] for f in m2["features"]
        ]
        again, n3 = materialize_wfids(m1, FP)
        assert n3 == 0 and again is m1  # 已物化 → 原对象返回
        assert has_wfids(m1) and not has_wfids(fc)
        # 既有 id 键保留
        assert m1["features"][0]["id"] == "sch-0"

    def test_wfid_by_index_shape(self):
        assert wfid_for_index(FP, 3) == f"wf1-{__import__('hashlib').sha256(f'{FP}:3'.encode()).hexdigest()[:16]}"

    def test_resolve_feature_ids_chain(self):
        fc = materialized_fc()
        wfids = [f["properties"][WFID_PROP] for f in fc["features"]]
        resolved = resolve_feature_ids(fc, ["sch-1", "sch-3", wfids[0], "sch-missing"])
        assert resolved["sch-1"] == wfids[1]
        assert resolved["sch-3"] == wfids[3]
        assert resolved[wfids[0]] == wfids[0]  # WFID 本身可寻址
        assert "sch-missing" not in resolved


# ─── attr ops ──────────────────────────────────────────────────────────────

class TestAttrValidation:
    def test_enum_violation_lists_vocabulary(self):
        fc = materialized_fc()
        profiles = profile_fields(fc)
        findings = validate_set_fields({"type": "幼儿园"}, profiles)
        assert findings and findings[0]["code"] == "enum_violation"
        assert "小学" in findings[0]["detail"]["allowed"]

    def test_type_mismatch_rejected(self):
        fc = materialized_fc()
        profiles = profile_fields(fc)
        assert validate_set_fields({"capacity": "很多"}, profiles)[0]["code"] == "type_mismatch"

    def test_unknown_and_reserved_field(self):
        fc = materialized_fc()
        profiles = profile_fields(fc)
        assert validate_set_fields({"new_column": 1}, profiles)[0]["code"] == "unknown_field"
        assert validate_set_fields({WFID_PROP: "x"}, profiles)[0]["code"] == "reserved_field"

    def test_valid_set_passes(self):
        fc = materialized_fc()
        profiles = profile_fields(fc)
        assert validate_set_fields({"type": "小学", "capacity": 800}, profiles) == []


# ─── geometry ops ──────────────────────────────────────────────────────────

class TestGeometryOps:
    def test_move_by_numeric_oracle(self):
        # 数值 oracle：39°N 东移 1000m ——
        # ① 测地自洽：对结果做 geod.inv 应还原 ~1000m/90°（证明真按米制
        #    测地平移，而非度近似硬编码）；
        # ② 粗界：经度增量与均匀近似 1000/(111320·cos39°) 差 <1%
        #    （测地线 vs 平行圈的固有差异 ~0.1%）。
        from app.services.feature_edit.geometry_ops import _geod

        fc = materialized_fc()
        wfids = {f["properties"][WFID_PROP] for f in fc["features"]}
        deltas, findings = move_by(fc, wfids, dx_m=1000.0, dy_m=0.0)
        assert not findings and len(deltas) == 5
        before = deltas[0]["geometry_before"]["coordinates"]
        after = deltas[0]["geometry_after"]["coordinates"]
        az_back, _, dist_back = _geod().inv(before[0], before[1], after[0], after[1])
        assert abs(dist_back - 1000.0) < 0.5
        assert abs(az_back - 90.0) < 0.05
        dlon = after[0] - before[0]
        expected = 1000.0 / (111320.0 * math.cos(math.radians(39.0)))
        assert abs(dlon - expected) / expected < 0.01
        assert abs(after[1] - before[1]) < 1e-4

    def test_move_by_requires_units(self):
        fc = materialized_fc()
        wfids = {f["properties"][WFID_PROP] for f in fc["features"]}
        _, findings = move_by(fc, wfids, dx_m="100", dy_m=None)
        assert findings[0]["code"] == "unit_required"

    def test_move_by_rejects_huge_displacement(self):
        fc = materialized_fc()
        wfids = {f["properties"][WFID_PROP] for f in fc["features"]}
        _, findings = move_by(fc, wfids, dx_m=1_000_000.0, dy_m=0.0)
        assert findings[0]["code"] == "invalid_op"

    def test_repair_close_ring(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "p1",
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1]]]},  # 未闭环
                    "properties": {"a": 1},
                }
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        wfid = fc["features"][0]["properties"][WFID_PROP]
        deltas, findings = repair_geometry(fc, {wfid}, ["close_ring"])
        assert not findings
        ring = deltas[0]["geometry_after"]["coordinates"][0]
        assert ring[0] == ring[-1] and len(ring) == 4

    def test_repair_dedupe_vertices(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "l1",
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 0], [1, 1], [1, 1], [2, 2]]},
                    "properties": {"a": 1},
                }
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        wfid = fc["features"][0]["properties"][WFID_PROP]
        deltas, findings = repair_geometry(fc, {wfid}, ["dedupe_vertices"])
        assert not findings
        assert deltas[0]["geometry_after"]["coordinates"] == [[0, 0], [1, 1], [2, 2]]

    def test_snap_to_ref_hit_and_miss(self):
        fc = materialized_fc()  # 5 个点，x=116.00..116.04, y=39.0
        wfids = [f["properties"][WFID_PROP] for f in fc["features"]]
        roads = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[116.001, 39.0], [116.001, 39.1]]},
                    "properties": {},
                }
            ],
        }
        # 116.001 与 116.00 距离 ≈ 85m（39°纬度 0.001°经度）→ 容差 100m 内
        deltas, findings, summary = snap_to_ref(fc, {wfids[0]}, roads, 100.0)
        assert not findings and len(deltas) == 1
        moved_x = deltas[0]["geometry_after"]["coordinates"][0]
        assert abs(moved_x - 116.001) < 1e-9
        assert summary["snapped"] == 1
        # 远处要素 + 小容差 → fail-closed
        deltas2, findings2, _ = snap_to_ref(fc, {wfids[0]}, roads, 10.0)
        assert findings2 and findings2[0]["code"] == "no_snap_candidate"

    def test_geodesic_distance_known_pair(self):
        # 北京→上海 ≈ 1067km（WGS84 大圆）
        d = geodesic_distance_m(116.4074, 39.9042, 121.4737, 31.2304)
        assert abs(d - 1_067_000) < 5_000


# ─── plan ──────────────────────────────────────────────────────────────────

class TestPlan:
    def _ctx(self):
        fc = materialized_fc()
        wfids = [f["properties"][WFID_PROP] for f in fc["features"]]
        return fc, wfids

    def test_build_plan_selection_targets(self):
        fc, wfids = self._ctx()
        plan, findings = build_plan(
            ref_id="ref:x",
            payload={"geojson": fc},
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
            requested_ids=["sch-1"],
            selection=None,
            dataset_fingerprint=FP,
            content_revision=3,
        )
        assert not findings
        assert plan["committable"] and plan["safe"]  # 纯属性 + 无 findings
        assert plan["target_wfids"] == [wfids[1]]
        assert plan["expected_content_revision"] == 3
        assert plan["preview"][0]["fields"]["type"]["before"] == "中学"
        assert plan["preview"][0]["fields"]["type"]["after"] == "小学"

    def test_build_plan_enum_violation_blocks(self):
        fc, _ = self._ctx()
        plan, findings = build_plan(
            ref_id="ref:x", payload=fc,
            ops=[{"op": "set_fields", "fields": {"type": "幼儿园"}}],
            requested_ids=["sch-1"], selection=None,
            dataset_fingerprint=FP, content_revision=1,
        )
        assert findings[0]["code"] == "enum_violation"
        assert not plan["committable"] and not plan["safe"]

    def test_geometry_plan_never_safe(self):
        fc, _ = self._ctx()
        plan, findings = build_plan(
            ref_id="ref:x", payload=fc,
            ops=[{"op": "move_by", "dx_m": 5.0, "dy_m": 0.0}],
            requested_ids=["sch-0"], selection=None,
            dataset_fingerprint=FP, content_revision=1,
        )
        assert not findings and plan["committable"]
        assert not plan["safe"]  # 坐标变更恒需显式确认

    def test_selection_mode_reads_selected_ids(self):
        fc, wfids = self._ctx()
        plan, findings = build_plan(
            ref_id="ref:x", payload=fc,
            ops=[{"op": "set_fields", "fields": {"capacity": 900}}],
            requested_ids=None,
            selection={"dataset_ref": "ref:x", "feature_id": "sch-0", "selected_ids": ["sch-2", "sch-4"]},
            dataset_fingerprint=FP, content_revision=1,
        )
        assert not findings
        assert plan["target_wfids"] == sorted([wfids[0], wfids[2], wfids[4]])

    def test_derive_changes_same_path_for_commit(self):
        fc, _ = self._ctx()
        deltas, schema_ops, findings, _ = derive_changes(
            fc, [{"op": "set_fields", "fields": {"type": "小学"}}],
            [f["properties"][WFID_PROP] for f in fc["features"]], {},
        )
        assert not findings and schema_ops == []
        assert all(d["fields"]["type"]["before"] in ("小学", "中学") for d in deltas)
        assert all(d["fields"]["type"]["after"] == "小学" for d in deltas)

    def test_safe_classification_bounds(self):
        assert not plan_safe_classification([{"op": "set_fields", "fields": {}}], [], 51)
        assert plan_safe_classification([{"op": "set_fields", "fields": {}}], [], 50)
        assert not plan_safe_classification([{"op": "repair_geometry", "fixes": ["make_valid"]}], [], 1)

    def test_no_target_rejected(self):
        fc, _ = self._ctx()
        plan, findings = build_plan(
            ref_id="ref:x", payload=fc, ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
            requested_ids=None, selection=None,
            dataset_fingerprint=FP, content_revision=1,
        )
        assert findings[0]["code"] == "invalid_target" and plan == {}

    def test_materializing_preview_uses_future_wfids(self):
        # 未物化数据集：预览对「物化后」形态求值（与 commit 一致）
        raw = school_fc()
        plan, findings = build_plan(
            ref_id="ref:x", payload=raw,
            ops=[{"op": "set_fields", "fields": {"type": "小学"}}],
            requested_ids=["sch-1"], selection=None,
            dataset_fingerprint=FP, content_revision=1,
        )
        assert not findings and plan["needs_materialization"] is True
        expected_wfid = wfid_for_index(FP, 1)
        assert plan["target_wfids"] == [expected_wfid]
