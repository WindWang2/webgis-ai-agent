"""C13 修复分级策略 / 决策账本 / intent codec 回归。

不变式：
1. 分级边界机器可测：可映射+warning+不触锁 → auto_safe；error 或触锁 →
   needs_approval；不可映射 → blocked（无修复路径，永不产生意图）；
2. 决策账本：≤32 FIFO、同 proposal 替换、rejected 指纹集可提取；
3. intent codec：dict round-trip 等指纹；intent_kind 漂移 / 缺 category →
   None（不猜、不部分还原）；fingerprint 是账本幂等键。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.visual_observation.repair_decisions import (
    DECISION_APPROVED,
    DECISION_REJECTED,
    load_decisions,
    record_decision,
    rejected_fingerprints,
    decision_for_proposal,
)
from app.services.gis_harness.visual_observation.repair_intent import (
    VisualRepairIntent,
    compile_intent,
    intent_from_dict,
    intent_fingerprint,
    intent_to_dict,
)
from app.services.gis_harness.visual_observation.repair_policy import (
    APPROVAL_AUTO_SAFE,
    APPROVAL_BLOCKED,
    APPROVAL_NEEDS_APPROVAL,
    HEALABLE_TAXONOMY,
    approval_class_for,
    touches_locked_layers,
)
from app.services.session_data import session_data_manager


def _finding(code="visual_label_collision", severity="warning",
             entity="L1", evidence="注记 overlap 重叠"):
    return {
        "domain": "visual", "code": code, "severity": severity,
        "source": "visual_observation_provider", "scope": "map",
        "affected_entity": entity, "evidence": evidence,
        "repair_class": "", "degradation_only": True,
        "finding_id": f"visual:{code}:fp",
        "observed_revision": 3,
    }


# ── policy ────────────────────────────────────────────────────────────────

def test_policy_matches_repair_bridge_closure():
    """互锁（docstring 声明的机器锁面）：policy 可自愈闭包 == repair_bridge
    翻译表键集 —— 漂移即红（单一路径：policy 放行的必须可被翻译）。"""
    from app.services.gis_harness.visual_observation.repair_bridge import (
        _TAXO_TO_HEAL_SHAPE,
    )

    assert HEALABLE_TAXONOMY == set(_TAXO_TO_HEAL_SHAPE)


def test_policy_matrix():
    # warning + 可映射 + 不触锁 → auto_safe（三类逐一锁定）
    for taxonomy in ("visual_label_collision", "visual_contrast",
                     "visual_overlap"):
        assert approval_class_for(
            _finding(code=taxonomy)) == APPROVAL_AUTO_SAFE
    assert approval_class_for(_finding()) == APPROVAL_AUTO_SAFE
    # error → needs_approval（无论触锁与否）
    assert approval_class_for(
        _finding(severity="error")) == APPROVAL_NEEDS_APPROVAL
    # 触锁 → needs_approval
    assert approval_class_for(
        _finding(), touches_locked=True) == APPROVAL_NEEDS_APPROVAL
    # 不可映射 taxonomy → blocked
    for unmapped in ("visual_crop", "visual_legibility",
                     "visual_legend_mismatch", "visual_empty_space",
                     "visual_hierarchy"):
        assert approval_class_for(_finding(code=unmapped)) == APPROVAL_BLOCKED
    # 未知 taxonomy → blocked（证据文本中性 —— 关键词回退是既有单一真相，
    # 不在本测试的射击面内）。
    assert approval_class_for(
        _finding(code="visual_weird", evidence="rule:ink_ratio")) == \
        APPROVAL_BLOCKED


def test_policy_severity_override_and_entity_intersection():
    assert approval_class_for(
        _finding(), severity="error") == APPROVAL_NEEDS_APPROVAL
    # 触锁判定：finding 实体与锁定集相交。
    assert touches_locked_layers(_finding(entity="L1"), ["L1", "L2"]) is True
    assert touches_locked_layers(_finding(entity="L1"), ["L9"]) is False
    # 实体未知（空）不臆测触锁。
    assert touches_locked_layers(_finding(entity=""), ["L1"]) is False


# ── decisions ledger ──────────────────────────────────────────────────────

@pytest.fixture
def sid():
    return f"vdec-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _clean(sid):
    await session_data_manager.clear_session(sid)
    yield
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_decision_ledger_roundtrip_and_rejection_memory(sid):
    assert await record_decision(
        sid, decision=DECISION_REJECTED, proposal_id="vrepair-x-1",
        defect_fingerprint="vheal-sha256:aaa",
        recurrence_fingerprints=["fp-label-01"], origin="user", revision=4,
        finding_ids=["visual:visual_label_collision:fp"],
        op_labels=["label_layout"], reason="不要动我的标注")
    assert await record_decision(
        sid, decision=DECISION_APPROVED, proposal_id="vrepair-y-2",
        defect_fingerprint="vheal-sha256:bbb",
        recurrence_fingerprints=["fp-contrast-02"], origin="user", revision=5)
    decisions = await load_decisions(sid)
    assert len(decisions) == 2
    # 拒绝按 finding 的 recurrence 指纹（原因身份）记忆。
    assert rejected_fingerprints(decisions) == frozenset({"fp-label-01"})
    assert decision_for_proposal(decisions, "vrepair-y-2")["decision"] == \
        DECISION_APPROVED
    assert decision_for_proposal(decisions, "ghost") is None


@pytest.mark.asyncio
async def test_approval_supersedes_earlier_rejection(sid):
    """用户改主意：同一原因先拒后批 → 最新裁决获胜（可再次提案）。"""
    await record_decision(
        sid, decision=DECISION_REJECTED, proposal_id="vrepair-r-1",
        recurrence_fingerprints=["fp-x"], revision=1)
    assert rejected_fingerprints(await load_decisions(sid)) == {"fp-x"}
    await record_decision(
        sid, decision=DECISION_APPROVED, proposal_id="vrepair-r-1",
        recurrence_fingerprints=["fp-x"], revision=2)
    assert rejected_fingerprints(await load_decisions(sid)) == frozenset()


@pytest.mark.asyncio
async def test_decision_ledger_bounded_and_replaced(sid):
    for i in range(40):
        await record_decision(
            sid, decision=DECISION_REJECTED, proposal_id=f"vrepair-p-{i}",
            recurrence_fingerprints=[f"fp-{i}"], revision=i)
    # 同 proposal_id 重复记录 → 替换（不重复占位）。
    await record_decision(
        sid, decision=DECISION_APPROVED, proposal_id="vrepair-p-39",
        recurrence_fingerprints=["fp-39"], revision=39)
    decisions = await load_decisions(sid)
    assert len(decisions) <= 32
    # FIFO 淘汰最老的；p-39 已翻转为 approved → 不在 rejected 集。
    rejected = rejected_fingerprints(decisions)
    assert "fp-39" not in rejected
    assert "fp-0" not in rejected            # 最老段已被淘汰
    assert "fp-20" in rejected


@pytest.mark.asyncio
async def test_decision_unknown_word_rejected(sid):
    assert await record_decision(sid, decision="sideways") is False
    assert await load_decisions(sid) == []


# ── intent codec ──────────────────────────────────────────────────────────

class _Def:
    def __init__(self):
        from types import SimpleNamespace

        self.d = SimpleNamespace(
            category="label_collision", severity="warning",
            layer_ids=("L1",), occluder_layer_id=None,
            dimension="information_density", evidence="注记 overlap",
            min_contrast_ratio=None, suggested_operation=None,
        )

    def build(self):
        return self.d


def test_intent_roundtrip_preserves_fingerprint():
    intent = compile_intent(
        proposal_id="vrepair-abc123-4", base_revision=4,
        approval_class=APPROVAL_AUTO_SAFE, origin="system",
        defects=[_Def().build()],
        finding_ids=["visual:visual_label_collision:fp"],
        op_labels=["label_layout"],
        defect_fingerprint="vheal-sha256:ccc",
    )
    raw = intent_to_dict(intent)
    parsed = intent_from_dict(raw)
    assert parsed is not None
    assert intent_fingerprint(parsed) == intent_fingerprint(intent)
    assert parsed == intent


def test_intent_from_dict_shape_drift_is_none():
    good = intent_to_dict(compile_intent(
        proposal_id="vrepair-abc123-4", base_revision=1,
        approval_class=APPROVAL_AUTO_SAFE, origin="user",
        defects=[{"category": "contrast", "severity": "warning",
                  "layer_ids": ["L1"], "occluder_layer_id": None,
                  "dimension": "color_discriminability", "evidence": "e",
                  "min_contrast_ratio": 1.4, "suggested_operation": None}],
    ))
    # intent_kind 漂移 → None
    bad_kind = dict(good, intent_kind="something_else")
    assert intent_from_dict(bad_kind) is None
    # defect 缺 category → None（不部分还原）
    bad_defect = dict(good, defects=[{"severity": "warning"}])
    assert intent_from_dict(bad_defect) is None
    # 非 dict / 缺根键 → None
    assert intent_from_dict(None) is None
    assert intent_from_dict([good]) is None
    assert intent_from_dict({"schema_version": "v1"}) is None
    # base_revision 类型漂移 → None
    assert intent_from_dict(dict(good, base_revision="x")) is None


def test_intent_fingerprint_stable_across_key_order():
    a = intent_from_dict({
        "intent_kind": "apply_visual_heal_patch",
        "proposal_id": "p-1", "base_revision": 2,
        "approval_class": "auto_safe", "origin": "system",
        "defects": [{"category": "overlap", "severity": "warning",
                     "layer_ids": ["A", "B"], "occluder_layer_id": None,
                     "dimension": "readability", "evidence": "z",
                     "min_contrast_ratio": None, "suggested_operation": None}],
    })
    b = intent_from_dict({
        "defects": [{"suggested_operation": None, "evidence": "z",
                     "dimension": "readability", "occluder_layer_id": None,
                     "min_contrast_ratio": None, "layer_ids": ["A", "B"],
                     "severity": "warning", "category": "overlap"}],
        "origin": "system", "approval_class": "auto_safe",
        "base_revision": 2, "proposal_id": "p-1",
        "intent_kind": "apply_visual_heal_patch",
    })
    assert a is not None and b is not None
    assert intent_fingerprint(a) == intent_fingerprint(b)


def test_compile_intent_normalizes_object_defects():
    intent = compile_intent(
        proposal_id="vrepair-obj-1", base_revision=7,
        approval_class=APPROVAL_AUTO_SAFE, origin="system",
        defects=[_Def().build()], defect_fingerprint="fp-obj")
    assert intent.defects[0]["category"] == "label_collision"
    assert set(intent.defects[0].keys()) <= {
        "category", "severity", "layer_ids", "occluder_layer_id",
        "dimension", "evidence", "min_contrast_ratio", "suggested_operation"}


def test_visual_repair_intent_is_frozen_dataclass():
    intent = VisualRepairIntent()
    with pytest.raises(Exception):
        intent.base_revision = 5      # type: ignore[misc]
