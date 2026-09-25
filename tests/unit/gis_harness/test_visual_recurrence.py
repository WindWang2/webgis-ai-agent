"""F15 跨运行视觉 recurrence 账本回归（ADR-0214 决策五）。

不变式：
1. 同一指纹出现 ≥ MAX_VISUAL_RECURRENCE_RUNS（3）次 → 硬停（newly 披露
   一次，之后持续命中），披露面责任在 pipeline（降 info + 收据）；
2. 账本有界：findings ≤16 FIFO、hard_stopped ≤8；间歇缺席不清零
   （间歇复现是同因强信号）；
3. 修复尝试即进展信号：reset_fingerprints 清计数并移出 hard_stopped；
   状态版本化：旧/畸形状态 → 空账本（诚实缺席）。
"""

from __future__ import annotations

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.visual_observation.recurrence import (
    MAX_VISUAL_RECURRENCE_RUNS,
    STATE_VERSION,
    VisualRecurrenceLedger,
    is_hard_stopped,
    observe_visual_findings,
    reset_fingerprints,
)


def _vf(entity="L1", code="visual_contrast"):
    return UnifiedFinding(
        domain="visual", code=code, severity="warning", source="t",
        scope="map", affected_entity=entity, evidence="low contrast",
        blocks_completion=False, degradation_only=True,
    )


def test_runs_ladder_hits_hard_stop_at_third_run():
    ledger = VisualRecurrenceLedger()
    fp = _vf().recurrence_fingerprint

    r1 = observe_visual_findings(ledger, [_vf()], mapspec_revision=1)
    assert r1.newly_hard_stopped == () and r1.recurrent == ()
    r2 = observe_visual_findings(ledger, [_vf()], mapspec_revision=2)
    assert r2.recurrent == (fp,) and r2.newly_hard_stopped == ()
    r3 = observe_visual_findings(ledger, [_vf()], mapspec_revision=3)
    assert r3.newly_hard_stopped == (fp,)
    assert is_hard_stopped(ledger, fp)
    # 第 4 次仍现：持续命中硬停、不重复 newly 披露。
    r4 = observe_visual_findings(ledger, [_vf()], mapspec_revision=4)
    assert r4.newly_hard_stopped == ()
    assert r4.hard_stopped_findings and is_hard_stopped(ledger, fp)
    assert ledger.findings[fp]["runs"] == 4


def test_different_fingerprints_track_independently():
    ledger = VisualRecurrenceLedger()
    observe_visual_findings(ledger, [_vf(entity="L1")], mapspec_revision=1)
    observe_visual_findings(ledger, [_vf(entity="L1")], mapspec_revision=2)
    report = observe_visual_findings(
        ledger, [_vf(entity="L1"), _vf(entity="L2")], mapspec_revision=3)
    # L1 达 3 次 → 硬停；L2 仅 1 次 → 不波及。
    assert len(report.newly_hard_stopped) == 1
    assert not any(
        is_hard_stopped(ledger, _vf(entity="L2").recurrence_fingerprint)
        for _ in ())


def test_absence_does_not_reset_runs():
    ledger = VisualRecurrenceLedger()
    observe_visual_findings(ledger, [_vf()], mapspec_revision=1)
    observe_visual_findings(ledger, [_vf()], mapspec_revision=2)
    # 第 3 次运行视觉评估缺席（未配置/无发现）→ 计数保留。
    observe_visual_findings(ledger, [], mapspec_revision=3)
    report = observe_visual_findings(ledger, [_vf()], mapspec_revision=4)
    fp = _vf().recurrence_fingerprint
    assert ledger.findings[fp]["runs"] == 3
    assert report.newly_hard_stopped == (fp,)


def test_ledger_is_bounded_fifo():
    ledger = VisualRecurrenceLedger()
    findings = [_vf(entity=f"L{i}") for i in range(24)]
    for i, vf in enumerate(findings):
        observe_visual_findings(ledger, [vf], mapspec_revision=i)
    assert len(ledger.findings) <= 16
    # 状态序列化同样有界。
    state = ledger.to_state()
    assert len(state["findings"]) <= 16
    assert len(state["hard_stopped"]) <= 8


def test_state_round_trip_and_version_guard():
    ledger = VisualRecurrenceLedger()
    observe_visual_findings(ledger, [_vf()], mapspec_revision=1)
    observe_visual_findings(ledger, [_vf()], mapspec_revision=2)
    restored = VisualRecurrenceLedger.from_state(ledger.to_state())
    fp = _vf().recurrence_fingerprint
    assert restored.findings[fp]["runs"] == 2
    # 旧版本/畸形状态 → 空账本。
    assert VisualRecurrenceLedger.from_state({"v": 0}).findings == {}
    assert VisualRecurrenceLedger.from_state(None).findings == {}
    assert VisualRecurrenceLedger.from_state({"v": STATE_VERSION}).findings == {}
    assert MAX_VISUAL_RECURRENCE_RUNS == 3


def test_reset_fingerprints_is_repair_progress_signal():
    ledger = VisualRecurrenceLedger()
    fp = _vf().recurrence_fingerprint
    observe_visual_findings(ledger, [_vf()], mapspec_revision=1)
    observe_visual_findings(ledger, [_vf()], mapspec_revision=2)
    observe_visual_findings(ledger, [_vf()], mapspec_revision=3)
    assert is_hard_stopped(ledger, fp)
    reset_fingerprints(ledger, [fp])
    assert not is_hard_stopped(ledger, fp)
    assert fp not in ledger.findings
