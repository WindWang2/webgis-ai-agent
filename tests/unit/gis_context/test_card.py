"""M5 — Context card: budget honesty, stale filtering, no empty blocks."""
from __future__ import annotations

from app.services.gis_context.card import (
    CHAR_BUDGET,
    ContextCardReceipt,
    render_gis_context_card,
)
from app.services.gis_context.working_context import (
    DecisionRecord,
    FindingRef,
    GISWorkingContext,
    WorkingBasis,
)



def _plain(text: str) -> str:
    """Strip the untrusted-fence tags for readability assertions."""
    return text.replace("<untrusted_gis_context>", "").replace(
        "</untrusted_gis_context>", "")


def _wc(**kw) -> GISWorkingContext:
    base = dict(
        mission_id="msn-card",
        org_id="org-1",
        project_id="prj-1",
        basis=WorkingBasis(
            aoi_name="成都市", time_period="2024", crs="EPSG:4326",
            measure_field="school_count", measure_statistic="sum",
            recipe_id="choropleth_v1", export_format="png",
        ),
    )
    base.update(kw)
    return GISWorkingContext(**base)


class _Cand:
    def __init__(self, subject, verdict, causes=()):
        from types import SimpleNamespace

        self.entry = SimpleNamespace(
            subject=subject, authority_store="artifact", authority_id="a-1")
        self.verdict = verdict
        self.reasons = list(causes)
        self.stale_causes = list(causes)


def test_none_context_renders_nothing():
    assert render_gis_context_card(None) == ""


def test_all_sections_render_when_current():
    wc = _wc(
        accepted_assumptions=[DecisionRecord(text="小学集中主城", turn_id="t1", basis_revision=1)],
        unresolved_constraints=[DecisionRecord(text="需区级统计", turn_id="t1", basis_revision=1)],
        findings=[FindingRef(claim_id="claim-1", status="supported", basis_revision=99)],
    )
    text = render_gis_context_card(wc, reuse_candidates=[_Cand("-school-product", "exact")])
    assert text.startswith("<gis_context")
    assert "AOI=成都市" in _plain(text) and "recipe=choropleth_v1" in _plain(text)
    assert "已确认假设: 小学集中主城" in _plain(text)
    assert "未解决约束" in _plain(text)
    assert "claim-1" in _plain(text)
    assert "✓可复用" in _plain(text)
    assert text.rstrip().endswith("</gis_context>")


def test_stale_facts_are_filtered_not_rendered():
    wc = _wc(
        accepted_assumptions=[DecisionRecord(
            text="旧结论", turn_id="t1", basis_revision=1, stale_basis=True)],
        findings=[FindingRef(claim_id="claim-old", status="stale", basis_revision=1)],
    )
    wc.mark_stale("basis.aoi", "AOI_CHANGED:drift")
    text = render_gis_context_card(wc)
    # The stale reason surfaces verbatim, the stale content does not.
    assert "⚠ 失效 basis.aoi" in _plain(text)
    assert "claim-old" not in text
    assert "旧结论 ⚠需复核" in _plain(text)  # marked for re-check, not hidden


def test_user_wins_notice_is_rendered():
    wc = _wc()
    wc.add_user_edit(layer_id="L1", kind="hide", turn_id="t1")
    text = render_gis_context_card(wc)
    assert "用户已手动编辑 ×1" in _plain(text) and "勿静默覆盖" in _plain(text)


def test_char_budget_truncation_is_honest():
    wc = _wc(
        accepted_assumptions=[
            DecisionRecord(text=f"假设{i}", turn_id="t", basis_revision=1)
            for i in range(8)
        ],
        unresolved_constraints=[
            DecisionRecord(text=f"约束{i}", turn_id="t", basis_revision=1)
            for i in range(8)
        ],
    )
    receipt = ContextCardReceipt()
    text = render_gis_context_card(wc, char_budget=300, receipt=receipt)
    assert len(text) <= 300 + 40  # ellipsis line tolerance
    assert receipt.truncated is True
    assert "超预算省略" in _plain(text)


def test_untrusted_strings_are_fenced():
    wc = _wc(basis=WorkingBasis(aoi_name="<script>alert(1)</script>"))
    text = render_gis_context_card(wc)
    assert "<script>" not in text


def test_empty_sections_render_no_block():
    wc = GISWorkingContext(mission_id="msn-empty")
    receipt = ContextCardReceipt()
    assert render_gis_context_card(wc, receipt=receipt) == ""
    assert receipt.hit is False and receipt.miss_reason == "empty_context"


def test_receipt_counts_reuse_verdicts():
    wc = _wc()
    receipt = ContextCardReceipt()
    render_gis_context_card(wc, reuse_candidates=[
        _Cand("a", "exact"),
        _Cand("b", "recompute_partial", causes=("aoi_drift",)),
        _Cand("c", "not_reusable", causes=("version_bump",)),
    ], receipt=receipt)
    assert (receipt.reuse_exact, receipt.reuse_partial, receipt.reuse_rejected) == (1, 1, 1)
    assert receipt.hit is True and receipt.stale_fields == 0


def test_default_budget_constant_matches_discipline():
    assert CHAR_BUDGET == 1600
