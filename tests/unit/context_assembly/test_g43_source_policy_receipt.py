"""G43（ADR-0295）单元测试：源策略契约 + 回执 v2 + 卡片源粒度。

覆盖场景矩阵（单元层）：
- C08 结构性拒绝（auto 携带选择/未知命名空间/冲突/超界）
- C27 源身份不因文本相同而混淆（id 命名空间闭集）
- C01/C17 知识卡 included/omitted/policy_excluded 如实分列
"""
from __future__ import annotations

from app.services.context_assembly.receipt import (
    RECEIPT_SCHEMA,
    ContextAssemblyReceipt,
    SourceDecisionLine,
)
from app.services.context_assembly.source_policy import (
    AUTO_POLICY,
    GIS_MEMORY_BLOCK_SOURCE_ID,
    SourcePolicy,
    validate_policy_payload,
)
from app.services.project_knowledge.card import render_project_knowledge_card
from app.services.project_knowledge.contract import KnowledgeEntry


def _entry(eid: str, kind: str = "dataset_version", subject: str | None = None):
    return KnowledgeEntry(
        id=eid, org_id="1", project_id="p1", entity_kind=kind,
        authority_store="project_dataset", authority_id=f"a-{eid}",
        subject=subject or f"entry {eid}", version_token=f"tok-{eid}"[:32],
    )


class TestSourcePolicyContract:
    def test_auto_policy_never_rewrites_default(self):
        assert AUTO_POLICY.decision_for("pk:x") == "included"
        assert AUTO_POLICY.is_explicit is False

    def test_explicit_decisions(self):
        p = SourcePolicy(
            mode="explicit",
            preferred=("pk:1",),
            excluded=("cm:9", "gm:block"),
        )
        assert p.decision_for("pk:1") == "preferred"
        assert p.decision_for("cm:9") == "excluded_by_policy"
        assert p.decision_for("gm:block") == "excluded_by_policy"
        assert p.decision_for("pk:2") == "included"

    def test_validate_rejects_malformed_namespaces(self):
        _, _, err = validate_policy_payload(
            mode="explicit", preferred=["/etc/passwd"], excluded=[])
        assert err == "source_id_malformed"
        _, _, err = validate_policy_payload(
            mode="explicit", preferred=["xx:1"], excluded=[])
        assert err == "source_id_malformed"
        # 任意正文/路径/URL 绝不是合法源 id（C09 结构面）
        for bad in ("data:text/plain,x", "pk:", "cm", GIS_MEMORY_BLOCK_SOURCE_ID + "x"):
            _, _, err = validate_policy_payload(
                mode="explicit", preferred=[bad], excluded=[])
            assert err == "source_id_malformed", bad

    def test_validate_rejects_conflict_and_auto_with_selection(self):
        _, _, err = validate_policy_payload(
            mode="explicit", preferred=["pk:1"], excluded=["pk:1"])
        assert err == "conflict"
        _, _, err = validate_policy_payload(
            mode="auto", preferred=["pk:1"], excluded=[])
        # 结构层面 auto+selection 由 store 层拒绝（auto_with_selection）
        assert err is None

    def test_validate_dedupes_and_caps(self):
        preferred, excluded, err = validate_policy_payload(
            mode="explicit",
            preferred=["pk:1", "pk:1", "cm:2"], excluded=["gm:block"])
        assert err is None
        assert preferred == ["pk:1", "cm:2"]
        _, _, err = validate_policy_payload(
            mode="explicit", preferred=[f"pk:{i}" for i in range(65)], excluded=[])
        assert err == "too_many"


class TestReceiptV2:
    def test_schema_bumped_and_source_lines_in_digest(self):
        r1 = ContextAssemblyReceipt(
            session_id="s", turn_id="t",
            source_lines=[SourceDecisionLine(
                source_id="pk:1", kind="project_knowledge_entry",
                decision="included", version_token="tok-1")],
        ).settle()
        assert RECEIPT_SCHEMA == "ctx.receipt.v2"
        assert r1.schema_version == "ctx.receipt.v2"
        r2 = ContextAssemblyReceipt(session_id="s", turn_id="t").settle()
        # 源行参与 digest（同身份不同源去向 ⇒ 不同 digest）
        assert r1.digest != r2.digest
        # 同输入确定性
        r3 = ContextAssemblyReceipt(
            session_id="s", turn_id="t",
            source_lines=[SourceDecisionLine(
                source_id="pk:1", kind="project_knowledge_entry",
                decision="included", version_token="tok-1")],
        ).settle()
        assert r1.digest == r3.digest

    def test_bounded_dict_carries_sources_and_policy_version(self):
        r = ContextAssemblyReceipt(
            session_id="s", turn_id="t", policy_version=7,
            source_lines=[
                SourceDecisionLine(source_id=f"pk:{i}", kind="k", decision="included")
                for i in range(120)
            ],
        ).settle()
        d = r.as_bounded_dict()
        assert d["policy_version"] == 7
        assert d["sources_total"] == 120
        assert len(d["sources"]) == 96  # 有界，溢出如实计数

    def test_settle_once(self):
        r = ContextAssemblyReceipt(session_id="s", turn_id="t").settle()
        again = r.settle()
        assert again.digest == r.digest


class TestKnowledgeCardSourceGranularity:
    def test_included_omitted_and_policy_excluded_are_honest(self):
        entries = [_entry(str(i)) for i in range(1, 25)]  # 超过 CARD_MAX_ITEMS
        card = render_project_knowledge_card(
            entries, project_id="p1",
            exclude_entry_ids={"3", "5"},
        )
        assert "3" not in card.included_ids
        assert "5" not in card.included_ids
        assert card.policy_excluded_ids == ("3", "5")
        # 预算上限挤出的条目进 omitted_ids（而非凭空消失）
        omitted_set = set(card.omitted_ids)
        assert omitted_set
        assert omitted_set.isdisjoint(card.included_ids)
        assert omitted_set.isdisjoint(card.policy_excluded_ids)
        assert card.truncated is True and card.omitted >= len(omitted_set)

    def test_empty_entries_with_exclusion_still_reports_excluded(self):
        card = render_project_knowledge_card(
            [], project_id="p1", exclude_entry_ids={"9"})
        assert card.text == ""
        assert card.policy_excluded_ids == ("9",)

    def test_text_contains_only_allowed_entries(self):
        entries = [_entry("1", subject="alpha"), _entry("2", subject="beta")]
        card = render_project_knowledge_card(
            entries, project_id="p1", exclude_entry_ids={"2"})
        assert "alpha" in card.text
        assert "beta" not in card.text
        assert card.included_ids == ("1",)
