"""R2-P1（qc-loop round 2）回归锁：derive_workflow_instance 的门指纹必须与
refresh 路径的 gate 比对同形。

历史缺陷：写侧把 workflow_contract ``str()`` 强转后传给 gate_fingerprint，
而 gate_fingerprint 只对 dict 取 contract 指纹（str 输入恒得 ""）；读侧
（refresh 的 gate 比对）传原始 dict —— contract 存在时两侧指纹永不匹配，
文档承诺的「不变即跳过」失效，每次工具结果/轮末/render 观察都全量重推。
"""

from app.services.gis_harness.workflow_instance import (
    derive_workflow_instance,
    gate_fingerprint,
    rows_fingerprint,
)


def test_gate_fingerprint_matches_reader_side_computation():
    contract = {"status": "finalized", "data_blockers": [], "method_blockers": []}
    chapter = {"plan_id": "p1", "workflow_contract": contract}
    state = derive_workflow_instance(
        chapter, instance_id="sess:p1", mapspec_revision=3, render_seq=2)
    expected = gate_fingerprint(
        rows_fingerprint(chapter), 3, 2, contract)
    assert state.gate_fingerprint == expected


def test_contract_change_still_changes_gate_fingerprint():
    # _contract_core 只取 roles/obligations/blockers（忽略 status 等非核心键）
    old = derive_workflow_instance(
        {"plan_id": "p1", "workflow_contract": {"data_blockers": ["A"]}},
        instance_id="sess:p1", mapspec_revision=3, render_seq=2)
    new = derive_workflow_instance(
        {"plan_id": "p1", "workflow_contract": {"data_blockers": []}},
        instance_id="sess:p1", mapspec_revision=3, render_seq=2)
    assert old.gate_fingerprint != new.gate_fingerprint


def test_absent_contract_keeps_empty_contract_fingerprint():
    bare = derive_workflow_instance(
        {"plan_id": "p1"}, instance_id="sess:p1",
        mapspec_revision=3, render_seq=2)
    expected = gate_fingerprint(
        rows_fingerprint({"plan_id": "p1"}), 3, 2, None)
    assert bare.gate_fingerprint == expected
