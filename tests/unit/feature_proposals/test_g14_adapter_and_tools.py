"""G32 对 G14 的最小适配专项 + Agent 工具真实 dispatch 测试。

- plan_seed：提案 seed 驱动出生身份（与 feature_edit 既有路径不冲突）；
- op 级 wfids：G14 单 plan 多要素异构修改（向后兼容：无 wfids 行为不变）；
- 工具：真实 ToolRegistry.register + dispatch（G32 工具族四件套；
  Agent 无法经工具批准 —— 面不存在）。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest

SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": f"s{i}",
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
         "properties": {"name": f"学校{i}", "type": "primary" if i % 2 else "secondary", "capacity": 500 + i}}
        for i in range(4)
    ],
}


def _mat(fc=None):
    from app.services.feature_edit.identity import materialize_wfids

    out, _ = materialize_wfids(fc or SCHOOLS, "fp-adapt")
    return out


def test_plan_seed_controls_created_identity():
    """plan_seed 决定 create 出生身份：同 seed 同身份、默认路径不受影响。"""
    from app.services.feature_edit.identity import wfid_for_created
    from app.services.feature_edit.plan import build_plan

    ops = [{"op": "create_features", "features": [
        {"geometry": {"type": "Point", "coordinates": [116.5, 39.5]}, "properties": {}},
    ]}]
    plan, findings = build_plan(
        ref_id="ref:x", payload={"geojson": SCHOOLS}, ops=ops,
        requested_ids=None, selection=None,
        dataset_fingerprint="fp-adapt", content_revision=1,
        plan_seed="fpr-seeded-001",
    )
    assert plan.get("committable"), findings
    created = plan["preview"][0]
    assert created["wfid"] == wfid_for_created("fpr-seeded-001", 0, 0)
    # 默认路径（无 seed）→ fep- 前缀。
    plan2, _ = build_plan(
        ref_id="ref:x", payload={"geojson": SCHOOLS}, ops=ops,
        requested_ids=None, selection=None,
        dataset_fingerprint="fp-adapt", content_revision=1,
    )
    assert plan2["plan_id"].startswith("fep-")


def test_op_level_wfids_heterogeneous_targets():
    """一个 plan：s0 改 capacity、s1 改 name（各要素各值 —— 旧 plan 级
    同值语义无法表达）。"""
    from app.services.feature_edit.plan import build_plan

    ops = [
        {"op": "set_fields", "wfids": ["s0"], "fields": {"capacity": 100}},
        {"op": "set_fields", "wfids": ["s1"], "fields": {"capacity": 200}},
    ]
    plan, findings = build_plan(
        ref_id="ref:x", payload={"geojson": SCHOOLS}, ops=ops,
        requested_ids=None, selection=None,
        dataset_fingerprint="fp-adapt", content_revision=1,
    )
    assert plan.get("committable"), findings
    after = {d["wfid"]: d["fields"]["capacity"]["after"] for d in plan["preview"]}
    assert sorted(after.values()) == [100, 200]
    # 只有 s0/s1 被写；s2/s3 不动。
    assert len(after) == 2


def test_op_level_wfids_backward_compatible_no_key():
    """无 wfids 键 → 行为与历史一致（plan 级全体目标同值）。"""
    from app.services.feature_edit.plan import build_plan

    ops = [{"op": "set_fields", "fields": {"capacity": 42}}]
    plan, findings = build_plan(
        ref_id="ref:x", payload={"geojson": SCHOOLS}, ops=ops,
        requested_ids=["s0", "s1", "s2", "s3"], selection=None,
        dataset_fingerprint="fp-adapt", content_revision=1,
    )
    assert plan.get("committable"), findings
    assert len(plan["preview"]) == 4
    assert all(d["fields"]["capacity"]["after"] == 42 for d in plan["preview"])


def test_structural_op_rejects_op_level_wfids():
    from app.services.feature_edit.plan import build_plan

    ops = [{"op": "split_feature", "wfid": "s0", "wfids": ["s0"],
            "line": {"type": "LineString", "coordinates": [[116.005, 38.9], [116.005, 39.1]]}}]
    plan, findings = build_plan(
        ref_id="ref:x", payload={"geojson": SCHOOLS}, ops=ops,
        requested_ids=None, selection=None,
        dataset_fingerprint="fp-adapt", content_revision=1,
    )
    assert not plan.get("committable")
    assert any(f["code"] == "invalid_op" for f in findings)


# ── Agent 工具真实 dispatch ─────────────────────────────────────────────────


@pytest.fixture
def registry():
    from app.tools.feature_proposal_tools import register_feature_proposal_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_feature_proposal_tools(reg)
    return reg


async def test_tool_create_and_dispatch_roundtrip(registry, tmp_path, monkeypatch):
    """真实 ToolRegistry.dispatch：注册 → 有类型参数校验 → 服务层 → 紧凑回执。"""
    from app.services.session_data import session_data_manager

    s = f"sess-tool-{uuid.uuid4().hex[:8]}"
    ref = await session_data_manager.store(s, SCHOOLS, prefix="geojson")

    from app.services.feature_proposals import store as fp_store
    from app.services.durable_blob_store import reset_filesystem_blob_store

    st = fp_store.ProposalStore(base_dir=tmp_path)
    monkeypatch.setattr("app.services.feature_proposals.service.proposal_store", st)
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path / "blobs")
    reset_filesystem_blob_store()
    try:
        result = await registry.dispatch(
            "vector_change_proposal_create",
            {
                "ref_id": ref,
                "ops": [
                    {"op": "set_fields", "wfids": ["s0"], "fields": {"capacity": 321}},
                ],
                "submit": True,
            },
            session_id=s,
        )
        assert result.get("success") is True, result
        payload: Dict[str, Any] = result
        assert payload["proposal_id"].startswith("fpr-")
        assert payload["status"] == "submitted"
        assert "next_action" in payload  # 指向 owner 审阅，不是自动提交

        # diff 工具链。
        diff = await registry.dispatch(
            "vector_change_proposal_diff",
            {"proposal_id": payload["proposal_id"]},
            session_id=s,
        )
        assert diff.get("success") is True
        assert diff["verdict"] == "clean"
        assert diff["candidate_delta_count"] == 1
    finally:
        reset_filesystem_blob_store()


async def test_tool_list_missing_session(registry):
    result = await registry.dispatch(
        "vector_change_proposal_list", {}, session_id=None,
    )
    # registry 拒绝面（校验/缺会话任一）—— 绝不静默成功。
    assert not (isinstance(result, dict) and result.get("success") is True)
