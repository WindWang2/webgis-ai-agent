"""H10：GISActionPlan IR 契约测试（ADR-0217）。

refs-only / bounded / closed vocab / 内容寻址 —— plan_ir 纪律在执行语义
层的等价锁定；负例驱动（payload 走私、词表外 kind、不可寻址动作）。
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.lib.gis.action_ir import (
    ACTION_IR_VERSION,
    ACTION_IR_VERSION as V,
    GISAction,
    GISActionPlan,
    IODescriptor,
    compute_plan_id,
    describe_plan,
    digest_of,
)


def _action(**kw) -> GISAction:
    base = dict(action_id="act-0001", kind="analyze", tool="hotspot_analysis")
    base.update(kw)
    return GISAction(**base)


def _plan(actions=None, **kw) -> GISActionPlan:
    base = dict(plan_id="gap-test", actions=actions or [_action()])
    base.update(kw)
    return GISActionPlan(**base)


class TestConstruction:
    def test_minimal_action_with_tool(self):
        a = _action()
        assert a.kind == "analyze"
        assert a.side_effect == "pure"
        assert a.failure == "fail_closed"
        assert a.idempotency == "idempotent"

    def test_capability_addressable_without_tool(self):
        a = _action(tool="", capability="spatial_statistics")
        assert a.capability == "spatial_statistics"

    def test_unaddressable_action_rejected(self):
        with pytest.raises(ValidationError, match="可寻址"):
            _action(tool="", capability="")

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            _action(kind="teleport")

    def test_unknown_side_effect_rejected(self):
        with pytest.raises(ValidationError):
            _action(side_effect="deletes_everything")

    def test_plan_fingerprint_deterministic(self):
        p1 = _plan()
        p2 = _plan()
        assert p1.plan_fingerprint() == p2.plan_fingerprint()
        assert p1.plan_fingerprint().startswith("gap-sha256:")

    def test_fingerprint_changes_with_params(self):
        p1 = _plan(actions=[_action(params={"k": 5})])
        p2 = _plan(actions=[_action(params={"k": 7})])
        assert p1.plan_fingerprint() != p2.plan_fingerprint()

    def test_compute_plan_id_ignores_plan_id_field(self):
        body = {"plan_version": V, "revision": 1, "actions": []}
        id1 = compute_plan_id(body)
        body["plan_id"] = "ignored"
        assert compute_plan_id(body) == id1
        assert id1.startswith("gap-")

    def test_action_fingerprint_stable(self):
        assert _action().action_fingerprint() == _action().action_fingerprint()


class TestRefsOnlyBounds:
    def test_params_key_budget_enforced(self):
        params = {f"p{i}": i for i in range(20)}
        with pytest.raises(ValidationError, match="键数"):
            _action(params=params)

    def test_params_bytes_budget_blocks_smuggling(self):
        big = {"blob": "x" * 4096}
        with pytest.raises(ValidationError, match="字节"):
            _action(params=big)

    def test_inline_geojson_rejected_in_params(self):
        fc = {"type": "FeatureCollection", "features": [
            {"properties": {"v": i}} for i in range(100)]}
        with pytest.raises(ValidationError):
            _action(params={"data": fc})

    def test_action_list_budget(self):
        with pytest.raises(ValidationError):
            _plan(actions=[_action(action_id=f"act-{i:04d}")
                           for i in range(65)])

    def test_io_descriptor_bounded(self):
        with pytest.raises(ValidationError):
            IODescriptor(name="x" * 200)

    def test_compensation_args_budget(self):
        from app.lib.gis.action_ir import Compensation
        with pytest.raises(ValidationError, match="键数"):
            Compensation(kind="remove_layer",
                         args={f"k{i}": i for i in range(12)})


class TestSerialization:
    def test_roundtrip_via_json(self):
        plan = _plan(actions=[
            _action(inputs=[IODescriptor(name="dataset", ref="ref:abc")]),
        ])
        payload = json.loads(plan.model_dump_json())
        revived = GISActionPlan(**payload)
        assert revived.plan_fingerprint() == plan.plan_fingerprint()

    def test_describe_plan_bounded(self):
        plan = _plan(actions=[_action(action_id=f"act-{i:04d}")
                              for i in range(40)])
        text = describe_plan(plan, max_lines=6)
        assert "…(+36 actions)" in text
        assert len(text.splitlines()) <= 6

    def test_digest_of_is_canonical(self):
        assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})


class TestPlanEnvelope:
    def test_revision_and_supersedes(self):
        p2 = _plan(plan_id="gap-2", revision=2, supersedes="gap-1")
        assert p2.revision == 2 and p2.supersedes == "gap-1"

    def test_origin_closed_vocab(self):
        with pytest.raises(ValidationError):
            _plan(origin="hallucination")

    def test_version_constant(self):
        assert ACTION_IR_VERSION == "1.0.0"
        assert _plan().plan_version == ACTION_IR_VERSION
