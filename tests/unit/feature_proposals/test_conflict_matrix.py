"""G32 冲突矩阵 —— reference-oracle 纯函数测试（P03/P05/P06/P07/P08/P10/P11）。

人工可数的三要素属性/几何冲突矩阵：**期望输出手写**（不用被测 merge/分类
函数生成 expected —— 任务 §五 oracle 纪律）。typed 值语义（null/missing/
False/0/"0"）逐格验证。
"""
from __future__ import annotations

import copy

from app.services.feature_proposals.conflicts import (
    classify_conflicts,
    values_equal_strict,
)

FP = "fp-matrix-0001"


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _feat(fid, props=None, geom=None):
    return {
        "type": "Feature", "id": fid,
        "geometry": geom or {"type": "Point", "coordinates": [116.0, 39.0]},
        "properties": dict(props or {}),
    }


BASE = _fc([
    _feat("f1", {"cat": "a", "n": 1, "flag": False, "txt": "x"}),
    _feat("f2", {"cat": "b", "n": 2, "flag": True, "txt": "y"}),
    _feat("f3", {"cat": "a", "n": 3, "flag": None, "txt": None}),
])


def _mat(base):
    """物化 base（wfid_for_index 同源确定性物化）。"""
    from app.services.feature_edit.identity import materialize_wfids

    fc, _n = materialize_wfids(copy.deepcopy(base), FP)
    return fc


def _classify(base, current, ops, *, seed="seed-m", targets=None):
    if targets is None:
        ids = []
        for op in ops:
            if isinstance(op.get("wfid"), str):
                ids.append(op["wfid"])
            ids.extend(op.get("wfids") or [])
        from app.services.feature_proposals.candidate import resolve_ids_on_payload
        wmap, _ = resolve_ids_on_payload(base, ids, FP)
        targets = list(dict.fromkeys(wmap.values()))
    return classify_conflicts(
        base_payload=_mat(base), base_fingerprint=FP,
        current_payload=current, current_fingerprint="fp-cur",
        ops=ops, ordered_targets=targets, op_seed=seed,
    )


def _kinds(report):
    return sorted(c["kind"] for c in report["unresolved_conflicts"])


# ── values_equal_strict：typed 语义逐格（P06）───────────────────────────────


def test_values_equal_strict_matrix():
    M = object()  # missing marker 语义由函数内部 _MISSING 承担；此处测可见值域
    # null ≠ 0 / False / ""
    assert not values_equal_strict(None, 0)
    assert not values_equal_strict(None, False)
    assert not values_equal_strict(None, "")
    assert values_equal_strict(None, None)
    # False ≠ 0 / "false"
    assert not values_equal_strict(False, 0)
    assert not values_equal_strict(False, "false")
    assert values_equal_strict(False, False)
    # 0 ≠ "0"；数字内部 int/float 数值相等
    assert not values_equal_strict(0, "0")
    assert values_equal_strict(1, 1.0)
    assert not values_equal_strict(0, 1)
    # 空串 ≠ null
    assert not values_equal_strict("", None)
    # dict/list 内容比较
    assert values_equal_strict({"a": [1, 2]}, {"a": [1, 2]})
    assert not values_equal_strict({"a": [1, 2]}, {"a": [2, 1]})
    del M  # 哨兵未参与比较（missing 语义由分类函数内部处理）


# ── P03：同要素两个互不依赖字段 → 无冲突（可组成新 candidate）──────────────


def test_p03_same_feature_independent_fields():
    current = copy.deepcopy(BASE)
    # current 改 f1.cat；proposal 改 f1.n —— 互不依赖（set_fields 不读其他字段）。
    current["features"][0]["properties"]["cat"] = "z"
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"n": 100}}],
    )
    assert report["verdict"] == "clean", report["unresolved_conflicts"]
    assert report["current_changed"] is True


# ── P05：同字段同 typed 值 → satisfied（不重复写）──────────────────────────


def test_p05_same_field_same_value_satisfied():
    current = copy.deepcopy(BASE)
    current["features"][0]["properties"]["n"] = 100
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"n": 100}}],
    )
    assert report["verdict"] == "clean"
    sat = {(s["wfid"], s["field"]) for s in report["satisfied"]}
    assert any(f == "n" for _w, f in sat), report["satisfied"]


# ── P05b：typed 不同值不算 satisfied（0 ≠ "0"）────────────────────────────


def test_p05b_zero_vs_string_zero_is_conflict():
    current = copy.deepcopy(BASE)
    current["features"][0]["properties"]["n"] = "0"
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"n": 0}}],
    )
    assert report["verdict"] == "conflicted"
    assert "field_value_conflict" in _kinds(report)


# ── P06：null / False / missing 三态严格 ────────────────────────────────────


def test_p06_missing_vs_null_conflict():
    current = copy.deepcopy(BASE)
    # current 把 f3.txt（base 为 null）设为**缺失**（删键）—— 与提案写 null 不同。
    del current["features"][2]["properties"]["txt"]
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f3"], "fields": {"txt": None}}],
    )
    # txt 的 current=missing 与 proposed=None 类型不同 → 冲突（诚实呈现，不吞并）。
    assert "field_value_conflict" in _kinds(report)


def test_p06_false_vs_zero_conflict():
    current = copy.deepcopy(BASE)
    current["features"][0]["properties"]["flag"] = 0  # False → 0（类型改变）
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"flag": False}}],
    )
    # current 已是 0，proposal 写 False —— 字段值冲突（False ≠ 0）。
    assert "field_value_conflict" in _kinds(report)


# ── P07：双方改同一几何 → geometry_both_modified（不平均/不吸附）───────────


def test_p07_geometry_both_modified():
    current = copy.deepcopy(BASE)
    current["features"][0]["geometry"] = {"type": "Point", "coordinates": [117.0, 40.0]}
    report = _classify(
        BASE, current,
        ops=[{"op": "move_by", "wfids": ["f1"], "dx_m": 10, "dy_m": 10}],
    )
    assert report["verdict"] == "conflicted"
    kinds = _kinds(report)
    assert "geometry_both_modified" in kinds
    geom = next(c for c in report["unresolved_conflicts"] if c["kind"] == "geometry_both_modified")
    # 两候选 digest 并存（原样展示，不合成第三值）。
    assert geom["current_digest"] and geom["proposed_digest"]
    assert geom["current_digest"] != geom["proposed_digest"]


# ── P08：current 改属性、proposal 只移动几何 → 独立合并 ───────────────────


def test_p08_attribute_vs_geometry_independent():
    current = copy.deepcopy(BASE)
    current["features"][1]["properties"]["n"] = 99
    report = _classify(
        BASE, current,
        ops=[{"op": "move_by", "wfids": ["f2"], "dx_m": 5, "dy_m": 5}],
    )
    assert report["verdict"] == "clean", report["unresolved_conflicts"]


# ── P11：proposal 删、current 改 → delete_update_conflict ──────────────────


def test_p11_delete_vs_update():
    current = copy.deepcopy(BASE)
    current["features"][0]["properties"]["n"] = 42
    report = _classify(
        BASE, current,
        ops=[{"op": "delete_features", "wfids": ["f1"]}],
    )
    assert "delete_update_conflict" in _kinds(report)


# ── P10：proposal 改、current 删 → target_deleted ──────────────────────────


def test_p10_update_on_deleted_target():
    current = copy.deepcopy(BASE)
    del current["features"][0]
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"n": 100}}],
    )
    assert "target_deleted" in _kinds(report)


# ── P14：重复自然键 → id_ambiguous（不 first-wins 错配/按位置迁移）────────


def test_p14_duplicate_natural_key_blocks():
    current = copy.deepcopy(BASE)
    dup = copy.deepcopy(current["features"][0])
    dup["properties"] = {"cat": "q"}
    current["features"].append(dup)  # 顶层 id "f1" 出现两次（未物化形态）
    report = _classify(
        BASE, current,
        ops=[{"op": "set_fields", "wfids": ["f1"], "fields": {"n": 100}}],
    )
    assert "id_ambiguous" in _kinds(report)


# ── P15：多要素异构 ops 求值（G14 适配面）──────────────────────────────────


def test_p15_multi_feature_heterogeneous_deltas():
    from app.services.feature_proposals.candidate import (
        evaluate_candidate,
        resolve_candidate_targets,
    )
    ops = [
        {"op": "set_fields", "wfids": ["f1"], "fields": {"n": 10}},
        {"op": "set_fields", "wfids": ["f2"], "fields": {"n": 20}},
        {"op": "set_fields", "wfids": ["f3"], "fields": {"n": 30}},
    ]
    ordered, missing, mat = resolve_candidate_targets(BASE, ops, dataset_fingerprint=FP)
    assert not missing
    deltas, findings, _ = evaluate_candidate(mat, ops, ordered, op_seed="seed-m")
    assert not findings, findings
    by_wfid = {}
    wmap, _ = __import__(
        "app.services.feature_proposals.candidate", fromlist=["resolve_ids_on_payload"],
    ).resolve_ids_on_payload(BASE, ["f1", "f2", "f3"], FP)
    inv = {v: k for k, v in wmap.items()}
    for d in deltas:
        by_wfid[inv[d["wfid"]]] = d["fields"]["n"]["after"]
    assert by_wfid == {"f1": 10, "f2": 20, "f3": 30}  # 手写 oracle：各要素各值


# ── P12：split 候选的出生身份稳定性（seed 决定）────────────────────────────


def test_p12_split_child_identity_stable_across_reloads():
    from app.services.feature_proposals.candidate import (
        evaluate_candidate,
        resolve_candidate_targets,
    )

    poly = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
    }
    base = _fc([_feat("f1", {"cat": "a"}, poly)])
    ops = [{
        "op": "split_feature", "wfid": "f1",
        "line": {"type": "LineString", "coordinates": [[1, -1], [1, 3]]},
    }]
    ordered, missing, mat = resolve_candidate_targets(base, ops, dataset_fingerprint=FP)
    assert not missing
    d1, findings1, lineage1 = evaluate_candidate(mat, ops, ordered, op_seed="fpr-fixed-seed")
    d2, findings2, lineage2 = evaluate_candidate(mat, ops, ordered, op_seed="fpr-fixed-seed")
    assert not findings1 and not findings2
    ids1 = sorted(d["wfid"] for d in d1)
    ids2 = sorted(d["wfid"] for d in d2)
    assert ids1 == ids2  # 同一候选重载 → 同一批 child _wfid
    assert lineage1 and lineage2 and lineage1 == lineage2
    # 不同 seed → 不同身份（服务端受控，模型无法指定）。
    d3, _, _ = evaluate_candidate(mat, ops, ordered, op_seed="fpr-other-seed")
    ids3 = sorted(d["wfid"] for d in d3)
    assert ids1 != ids3
