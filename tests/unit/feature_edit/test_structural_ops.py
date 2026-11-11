"""G14 结构性编辑核心测试：create/delete/replace/split/merge 的 delta 语义、
身份稳定、血缘与可逆性（V03/V06/V07/V08/V14 的纯函数面）。

数值 oracle 独立推算：正方形面积/分割等价/合并并集均以显式坐标给出。
"""
from app.services.feature_edit.contracts import WFID_PROP
from app.services.feature_edit.identity import materialize_wfids, wfid_for_created
from app.services.feature_edit.journal import (
    apply_deltas_to_features,
    apply_entry_effects,
    invert_deltas,
    invert_entry,
)
from app.services.feature_edit.plan import build_plan, derive_changes, needs_materialization

FP = "fp-g14-0001"

SQUARE = [
    [116.00, 39.00],
    [116.10, 39.00],
    [116.10, 39.10],
    [116.00, 39.10],
    [116.00, 39.00],
]
# 带洞 polygon：外环 SQUARE，洞居中（面积 ≈ 外环 1/4）
HOLE = [
    [116.03, 39.03],
    [116.07, 39.03],
    [116.07, 39.07],
    [116.03, 39.07],
    [116.03, 39.03],
]


def polygon_fc(with_hole: bool = False, n: int = 1) -> dict:
    features = []
    for i in range(n):
        rings = [SQUARE] if not with_hole else [SQUARE, HOLE]
        off = 0.5 * i
        features.append(
            {
                "type": "Feature",
                "id": f"zone-{i}",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[x + off, y] for x, y in ring] for ring in rings
                    ],
                },
                "properties": {"name": f"区域{i}", "kind": "服务区" if i == 0 else "备份区", "rank": i},
            }
        )
    fc = {"type": "FeatureCollection", "features": features}
    fc, _ = materialize_wfids(fc, FP)
    return fc


def wfids_of(fc: dict) -> list:
    return [f["properties"][WFID_PROP] for f in fc["features"]]


def build(ops, fc, **kw):
    return build_plan(
        ref_id="ref:g14",
        payload={"geojson": fc},
        ops=ops,
        requested_ids=kw.pop("requested_ids", None),
        selection=kw.pop("selection", None),
        dataset_fingerprint=kw.pop("dataset_fingerprint", FP),
        content_revision=kw.pop("content_revision", 3),
        **kw,
    )


# ─── create_features ───────────────────────────────────────────────────────

class TestCreate:
    def test_empty_dataset_first_feature_gets_plan(self):
        """V01 纯函数面：空数据集 create-only plan 不需要既有目标。"""
        empty = {"type": "FeatureCollection", "features": []}
        assert needs_materialization(empty) is False
        plan, findings = build([{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "新学校", "type": "小学"}},
        ]}], empty)
        assert findings == [], findings
        assert plan["committable"] is True
        assert plan["needs_materialization"] is False
        assert plan["safe"] is False  # 结构性操作恒不 safe
        deltas, _, derive_findings, _, _ = derive_changes(
            empty, plan["ops"], [], {}, plan_seed=plan["plan_id"]
        )
        assert derive_findings == []
        created = [d for d in deltas if d.get("presence") == "created"]
        assert len(created) == 1
        wfid = created[0]["wfid"]
        assert wfid.startswith("wf1-")
        # 应用后：真实 FC 里有这个要素，properties 带 _wfid
        feats, _ = apply_deltas_to_features([], created)
        assert feats[0]["properties"][WFID_PROP] == wfid
        assert feats[0]["properties"]["name"] == "新学校"

    def test_created_wfid_deterministic_per_plan_seed(self):
        """同 plan_seed 重求值同身份；不同 plan 新身份（幂等重放的根基）。"""
        fc = polygon_fc()
        ops = [{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}
        ]}]
        d1, _, f1, _, _ = derive_changes(fc, ops, [], {}, plan_seed="fep-aaa")
        d2, _, f2, _, _ = derive_changes(fc, ops, [], {}, plan_seed="fep-aaa")
        d3, _, f3, _, _ = derive_changes(fc, ops, [], {}, plan_seed="fep-bbb")
        assert not f1 and not f2 and not f3
        w1 = {d["wfid"] for d in d1 if d.get("presence") == "created"}
        w2 = {d["wfid"] for d in d2 if d.get("presence") == "created"}
        w3 = {d["wfid"] for d in d3 if d.get("presence") == "created"}
        assert w1 and w1 == w2 and w1 != w3

    def test_create_rejects_reserved_wfid_property(self):
        fc = polygon_fc()
        plan, findings = build([{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {WFID_PROP: "wf1-forged"}},
        ]}], fc)
        assert plan == {} or not plan.get("committable")
        assert any(f["code"] == "reserved_id" for f in findings)

    def test_create_enum_and_type_still_enforced(self):
        fc = polygon_fc()
        plan, findings = build([{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"kind": "未知类型"}},
        ]}], fc)
        assert any(f["code"] == "enum_violation" for f in findings)
        plan, findings = build([{"op": "create_features", "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"rank": "不是数字"}},
        ]}], fc)
        assert any(f["code"] == "type_mismatch" for f in findings)

    def test_create_over_limit_rejected(self):
        from app.services.feature_edit.contracts import MAX_CREATED_PER_PLAN

        fc = polygon_fc()
        feats = [{"geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}] * (MAX_CREATED_PER_PLAN + 1)
        plan, findings = build([{"op": "create_features", "features": feats}], fc)
        assert any(f["code"] == "invalid_op" for f in findings)


# ─── delete_features ───────────────────────────────────────────────────────

class TestDelete:
    def test_delete_creates_removed_snapshots_and_inverts(self):
        """V06 纯函数面：删除→逆=复活同身份同内容。"""
        fc = polygon_fc(n=3)
        targets = wfids_of(fc)[:2]
        deltas, _, findings, _, _ = derive_changes(
            fc, [{"op": "delete_features"}], targets, {}, plan_seed="fep-d1"
        )
        assert not findings
        removed = [d for d in deltas if d.get("presence") == "removed"]
        assert {d["wfid"] for d in removed} == set(targets)
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        assert len(after) == 1
        # inverse：复活完整快照（数量、身份集合、逐要素内容一致）
        inv = invert_deltas(deltas)
        restored, _ = apply_deltas_to_features(after, inv)
        assert len(restored) == 3
        assert set(wfids_of({"features": restored})) == set(wfids_of(fc))
        restored_by_wfid = {f["properties"][WFID_PROP]: f for f in restored}
        for original in fc["features"]:
            w = original["properties"][WFID_PROP]
            assert restored_by_wfid[w]["properties"] == original["properties"]
            assert restored_by_wfid[w]["geometry"] == original["geometry"]

    def test_delete_missing_target_finding(self):
        fc = polygon_fc(n=1)
        deltas, _, findings, _, _ = derive_changes(
            fc, [{"op": "delete_features"}], ["wf1-nonexistent"], {}, plan_seed="fep-d2"
        )
        assert any(f["code"] == "feature_not_found" for f in findings)


# ─── replace_geometry ──────────────────────────────────────────────────────

class TestReplaceGeometry:
    def test_vertex_edit_replaces_geometry_reversibly(self):
        """V03 纯函数面：顶点编辑 = 整几何替换，before/after 可逆。"""
        fc = polygon_fc()
        target = wfids_of(fc)[0]
        moved = {
            "type": "Polygon",
            "coordinates": [[[116.0, 39.2], [116.1, 39.2], [116.1, 39.3], [116.0, 39.3], [116.0, 39.2]]],
        }
        deltas, _, findings, _, _ = derive_changes(
            fc, [{"op": "replace_geometry", "geometry": moved}], [target], {}, plan_seed="fep-r1"
        )
        assert not findings
        assert deltas[0]["geometry_before"]["coordinates"][0][0] == [116.0, 39.0]
        assert deltas[0]["geometry_after"]["coordinates"][0][0] == [116.0, 39.2]
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        assert after[0]["geometry"]["coordinates"][0][0] == [116.0, 39.2]
        inv = invert_deltas(deltas)
        restored, _ = apply_deltas_to_features(after, inv)
        assert restored[0]["geometry"] == fc["features"][0]["geometry"]

    def test_type_mismatch_rejected(self):
        fc = polygon_fc()
        target = wfids_of(fc)[0]
        deltas, _, findings, _, _ = derive_changes(
            fc, [{"op": "replace_geometry", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}],
            [target], {}, plan_seed="fep-r2",
        )
        assert deltas == []
        assert any(f["code"] == "geometry_type_mismatch" for f in findings)

    def test_invalid_geometry_rejected(self):
        fc = polygon_fc()
        target = wfids_of(fc)[0]
        deltas, _, findings, _, _ = derive_changes(
            fc, [{"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [[0, 0]]}}],
            [target], {}, plan_seed="fep-r3",
        )
        assert any(f["code"] in ("invalid_geometry", "geometry_invalid_after") for f in findings)


# ─── split_feature ─────────────────────────────────────────────────────────

class TestSplit:
    def test_split_square_into_two_children_with_lineage(self):
        """V07：竖线切正方形 → 2 children；union 面积 = 原面积（容差内）；
        parent 退役；血缘 parent→children。"""
        fc = polygon_fc()
        parent = wfids_of(fc)[0]
        line = {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}
        deltas, _, findings, lineage = _split(fc, parent, line, "fep-s1")
        assert findings == [], findings
        assert lineage["kind"] == "split" and lineage["parent_wfids"] == [parent]
        children = lineage["child_wfids"]
        assert len(children) == 2
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        assert len(after) == 2 and wfids_of({"features": after}) == children
        # 字段继承：children 复制 parent 全部业务字段
        for feat in after:
            assert feat["properties"]["name"] == "区域0"
        # 面积不变量（split union ≈ original）
        from shapely.geometry import shape
        from shapely.ops import unary_union

        original = shape(fc["features"][0]["geometry"])
        union = unary_union([shape(f["geometry"]) for f in after])
        assert abs(union.area - original.area) < 1e-12
        # 可逆：undo 恢复 parent 原几何
        inv = invert_deltas(deltas)
        restored, _ = apply_deltas_to_features(after, inv)
        assert len(restored) == 1
        assert shape(restored[0]["geometry"]).equals(original)

    def test_split_holed_polygon_assigns_hole_to_containing_part(self):
        """V07 洞语义：洞完整保留在包含它的那一部分。"""
        fc = polygon_fc(with_hole=True)
        parent = wfids_of(fc)[0]
        # 分割线穿过外环左半（不穿洞）：洞完整落在右半部分
        line = {"type": "LineString", "coordinates": [[116.02, 38.9], [116.02, 39.2]]}
        deltas, _, findings, lineage = _split(fc, parent, line, "fep-s2")
        assert findings == [], findings
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        with_hole = [f for f in after if len(f["geometry"]["coordinates"]) == 2]
        without_hole = [f for f in after if len(f["geometry"]["coordinates"]) == 1]
        assert len(with_hole) == 1 and len(without_hole) == 1
        from shapely.geometry import shape

        holed = shape(with_hole[0]["geometry"])
        assert len(holed.interiors) == 1
        # 洞完整保留（interior 环 bbox = HOLE 的范围），且未被吞或变形
        hole_ring = holed.interiors[0]
        assert [round(v, 6) for v in hole_ring.bounds] == [116.03, 39.03, 116.07, 39.07]
        # 洞内的点不含在面内（洞是真的），洞外实面含任意内点
        assert not holed.contains(shape({"type": "Point", "coordinates": [116.05, 39.05]}))
        assert holed.contains(shape({"type": "Point", "coordinates": [116.085, 39.05]}))

    def test_split_point_rejected_typed(self):
        fc_points = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {}},
            ],
        }
        fc_points, _ = materialize_wfids(fc_points, FP)
        target = wfids_of(fc_points)[0]
        deltas, _, findings, lineage = _split(
            fc_points, target, {"type": "LineString", "coordinates": [[115.9, 39.0], [116.1, 39.0]]}, "fep-s3"
        )
        assert any(f["code"] == "invalid_op" for f in findings)

    def test_split_not_dividing_rejected(self):
        fc = polygon_fc()
        parent = wfids_of(fc)[0]
        # 线只擦过角（不贯穿）→ shapely split 不产生 2 部分
        line = {"type": "LineString", "coordinates": [[116.0, 39.05], [116.02, 39.05]]}
        deltas, _, findings, lineage = _split(fc, parent, line, "fep-s4")
        assert any(f["code"] == "split_not_dividing" for f in findings), findings

    def test_split_redo_keeps_child_identity(self):
        """redo 重放 apply entry 的原 deltas —— children 身份不变（V06/V07）。"""
        fc = polygon_fc()
        parent = wfids_of(fc)[0]
        line = {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}
        deltas, _, findings, lineage = _split(fc, parent, line, "fep-s5")
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        inv = invert_deltas(deltas)
        restored, _ = apply_deltas_to_features(after, inv)
        # redo = 再应用原 deltas（undo 后 head invert）——同身份
        again, _ = apply_deltas_to_features(restored, deltas)
        assert wfids_of({"features": again}) == lineage["child_wfids"]


def _split(fc, wfid, line, seed):
    from app.services.feature_edit.geometry_ops import split_feature

    deltas, findings, lineage = split_feature(fc, wfid, line, plan_seed=seed, op_index=0)
    return deltas, None, findings, lineage


# ─── merge_features ────────────────────────────────────────────────────────

class TestMerge:
    def test_merge_adjacent_polygons_first_policy_discloses_conflicts(self):
        """V08：字段继承显式（first），差异面披露；几何 = 并集单部分。"""
        # 两个相邻正方形（x 方向共享边 116.10）
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [
                        [[116.00, 39.0], [116.10, 39.0], [116.10, 39.1], [116.00, 39.1], [116.00, 39.0]]
                    ]},
                    "properties": {"name": "甲", "kind": "服务区", "rank": 1},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [
                        [[116.10, 39.0], [116.20, 39.0], [116.20, 39.1], [116.10, 39.1], [116.10, 39.0]]
                    ]},
                    "properties": {"name": "乙", "kind": "服务区", "rank": 2},
                },
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        w0, w1 = wfids_of(fc)
        from app.services.feature_edit.geometry_ops import merge_features

        deltas, findings, out = merge_features(
            fc, [w0, w1], {"mode": "first"}, plan_seed="fep-m1", op_index=0
        )
        assert findings == [], findings
        lineage = out["lineage"]
        assert lineage["parent_wfids"] == [w0, w1] and len(lineage["child_wfids"]) == 1
        # 冲突披露：name/rank 不同 → conflict_fields
        assert set(out["extra"]["conflict_fields"]) == {"name", "rank"}
        after, _ = apply_deltas_to_features(fc["features"], deltas)
        assert len(after) == 1
        assert after[0]["properties"]["name"] == "甲"  # first 策略
        assert after[0]["properties"]["kind"] == "服务区"  # 一致字段保留
        from shapely.geometry import shape

        merged = shape(after[0]["geometry"])
        assert merged.bounds == (116.0, 39.0, 116.2, 39.1)  # 并集
        # 可逆：undo 恢复两个父要素原样
        inv = invert_deltas(deltas)
        restored, _ = apply_deltas_to_features(after, inv)
        assert len(restored) == 2
        assert wfids_of({"features": restored}) == [w0, w1]
        assert shape(restored[1]["geometry"]).equals(shape(fc["features"][1]["geometry"]))

    def test_merge_require_identical_rejects_conflict(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                    [[116.0, 39.0], [116.1, 39.0], [116.1, 39.1], [116.0, 39.1], [116.0, 39.0]]]},
                 "properties": {"name": "甲"}},
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                    [[116.1, 39.0], [116.2, 39.0], [116.2, 39.1], [116.1, 39.1], [116.1, 39.0]]]},
                 "properties": {"name": "乙"}},
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        w0, w1 = wfids_of(fc)
        from app.services.feature_edit.geometry_ops import merge_features

        deltas, findings, out = merge_features(
            fc, [w0, w1], {"mode": "require_identical"}, plan_seed="fep-m2", op_index=0
        )
        assert deltas == [] and out is None
        assert any(f["code"] == "merge_conflict" and f.get("detail", {}).get("field") == "name" for f in findings)
        # overrides 显式解决后放行
        deltas, findings, out = merge_features(
            fc, [w0, w1], {"mode": "require_identical", "overrides": {"name": "合并区"}},
            plan_seed="fep-m2", op_index=0,
        )
        assert findings == [] and out is not None

    def test_merge_disjoint_rejected(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                    [[116.0, 39.0], [116.1, 39.0], [116.1, 39.1], [116.0, 39.1], [116.0, 39.0]]]},
                 "properties": {"name": "甲"}},
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                    [[117.0, 39.0], [117.1, 39.0], [117.1, 39.1], [117.0, 39.1], [117.0, 39.0]]]},
                 "properties": {"name": "乙"}},
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        w0, w1 = wfids_of(fc)
        from app.services.feature_edit.geometry_ops import merge_features

        deltas, findings, out = merge_features(fc, [w0, w1], {}, plan_seed="fep-m3", op_index=0)
        assert any(f["code"] == "merge_disjoint" for f in findings)

    def test_merge_mixed_types_rejected(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {}},
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                    [[116.0, 39.0], [116.1, 39.0], [116.1, 39.1], [116.0, 39.1], [116.0, 39.0]]]},
                 "properties": {}},
            ],
        }
        fc, _ = materialize_wfids(fc, FP)
        w0, w1 = wfids_of(fc)
        from app.services.feature_edit.geometry_ops import merge_features

        deltas, findings, out = merge_features(fc, [w0, w1], {}, plan_seed="fep-m4", op_index=0)
        assert any(f["code"] == "merge_type_mixed" for f in findings)


# ─── 顺序语义与 plan 集成 ──────────────────────────────────────────────────

class TestSequentialAndPlan:
    def test_create_properties_define_fields_of_new_feature(self):
        """顺序语义边界：新要素的字段在 create op 内给定 —— set_fields 的
        目标集来自 plan 目标解析（既有要素），不隐式扩展到本 plan 新生要素
        （编辑范围显式，不猜对象）。"""
        fc = {"type": "FeatureCollection", "features": []}
        ops = [
            {"op": "create_features", "features": [
                {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {"name": "新点", "kind": "服务区"}},
            ]},
            {"op": "set_fields", "fields": {"name": "改名点"}},
        ]
        deltas, schema_ops, findings, summaries, lineage = derive_changes(
            fc, ops, [], {}, plan_seed="fep-q1"
        )
        # 空数据集上 set_fields 的字段不存在 → unknown_field（诚实拒绝：
        # set_fields 只改既有列，新列由 create op 的 properties 定义）
        assert any(f["code"] == "unknown_field" and f.get("detail", {}).get("field") == "name" for f in findings)
        feats, _ = apply_deltas_to_features(
            [], [d for d in deltas if d.get("presence") == "created"]
        )
        assert len(feats) == 1
        assert feats[0]["properties"]["name"] == "新点"

    def test_delete_then_create_same_plan_replaces(self):
        fc = polygon_fc(n=2)
        w = wfids_of(fc)
        ops = [
            {"op": "delete_features"},
            {"op": "create_features", "features": [
                {"geometry": {"type": "Point", "coordinates": [116.0, 39.0]}}
            ]},
        ]
        deltas, _, findings, _, _ = derive_changes(fc, ops, [w[0]], {}, plan_seed="fep-q2")
        assert findings == []
        feats, _ = apply_deltas_to_features(fc["features"], deltas)
        assert len(feats) == 2  # 1 保留 + 1 新增

    def test_structural_ops_never_safe(self):
        from app.services.feature_edit.plan import plan_safe_classification

        for op in ("create_features", "delete_features", "replace_geometry", "split_feature", "merge_features"):
            assert not plan_safe_classification([{"op": op}], [], 1), op

    def test_entry_too_large_guard(self):
        """V14：单 entry 字节预算 —— 大几何 create 被诚实拒绝。"""
        big_ring = [[round(116.0 + 0.0001 * i, 7), round(39.0 + (0.0001 if i % 2 else 0), 7)] for i in range(1024)]
        big_ring.append(big_ring[0])
        fc = polygon_fc()
        plan, findings = build([{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [big_ring]}}
        ]}], fc)
        # 1024 顶点 ≈ 25KB —— 单条仍可（256KB 预算）；构造超限：50 × 大几何
        feats = [{"geometry": {"type": "Polygon", "coordinates": [big_ring]}} for _ in range(50)]
        plan, findings = build([{"op": "create_features", "features": feats}], fc)
        assert any(f["code"] == "entry_too_large" for f in findings), findings
        assert not plan.get("committable")

    def test_plan_preview_carries_presence_and_lineage(self):
        fc = polygon_fc()
        parent = wfids_of(fc)[0]
        plan, findings = build(
            [{"op": "split_feature", "wfid": parent,
              "line": {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}}],
            fc,
        )
        assert findings == [], findings
        assert plan["lineage"] and plan["lineage"][0]["parent_wfids"] == [parent]
        presences = {p.get("presence") for p in plan["preview"]}
        assert "removed" in presences and "created" in presences


# ─── journal entry effects（结构 entry 的正/逆应用） ───────────────────────

class TestEntryEffects:
    def test_apply_entry_roundtrip_with_lineage_entry(self):
        fc = polygon_fc()
        parent = wfids_of(fc)[0]
        line = {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}
        deltas, _, findings, lineage = _split(fc, parent, line, "fep-e1")
        assert findings == []
        entry = {
            "kind": "apply",
            "deltas": deltas,
            "schema_ops": [],
            "lineage": [dict(lineage, seq=1)],
            "fingerprint_before": "a",
            "fingerprint_after": "b",
            "content_revision_before": 1,
            "content_revision_after": 2,
        }
        applied, applied_wfids = apply_entry_effects(fc, entry)
        assert len(applied["features"]) == 2
        assert {f["properties"][WFID_PROP] for f in applied["features"]} == set(lineage["child_wfids"])
        inverse = invert_entry(entry)
        undone, _ = apply_entry_effects(applied, inverse)
        assert len(undone["features"]) == 1
        assert undone["features"][0]["properties"][WFID_PROP] == parent
