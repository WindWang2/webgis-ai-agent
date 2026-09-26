"""F15 视觉观察契约/taxonomy/融合 单元回归（ADR-0214）。

不变式：
1. snapshot 键面 ref-only：``to_snapshot`` 永不含字节/敏感载荷，
   键集合封闭（白名单锁定）；
2. taxonomy 封闭归一：8 类之外的输入诚实丢弃（""/unmapped），不猜；
3. 融合 deterministic-wins：确定性 finding 永不被删除/降级；命中的
   视觉条目被证据化（repair_class 清空 + corroborates 收据 +
   degradation_only 强制），未命中原样通过，不可归一诚实丢弃。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.visual_observation.contracts import (
    _SNAPSHOT_KEYS,
    VisualObservationInput,
    VisualObservationResult,
    VisualScreenshotRef,
)
from app.services.gis_harness.visual_observation.fusion import (
    fuse_visual_with_deterministic,
    visual_taxonomy_of,
)
from app.services.gis_harness.visual_observation.taxonomy import (
    VISUAL_TAXONOMY,
    deterministic_affinity,
    finding_code,
    normalize_to_taxonomy,
    taxonomy_counts_of,
)


# ── 1. snapshot 契约（ref-only 纪律）──────────────────────────────────────

def _screenshot_ref():
    return VisualScreenshotRef(
        ref="vshot-" + "a" * 64, sha256="a" * 64, size=1024,
        width=640, height=480, mapspec_revision=7,
    )


def test_snapshot_keys_are_closed_whitelist():
    obs = VisualObservationInput(
        trigger="finalization", session_id="s1", mapspec_revision=7,
        mapspec_fingerprint="carto-sha256:x", screenshot=_screenshot_ref(),
    )
    snap = obs.to_snapshot()
    assert set(snap.keys()) <= set(_SNAPSHOT_KEYS)
    raw = str(snap)
    assert "vshot-" in raw            # ref 允许
    assert "b64" not in raw.lower()
    assert "data:image" not in raw    # 无内联字节


def test_snapshot_round_trip_and_malformed_input():
    obs = VisualObservationInput(
        trigger="finalization", session_id="s1", mapspec_revision=7,
        deterministic_findings=({"code": "layout_conflict", "severity": "warning",
                                 "target": "legend"},),
        screenshot=_screenshot_ref(),
    )
    parsed = VisualObservationInput.from_snapshot(obs.to_snapshot())
    assert parsed.trigger == "finalization"
    assert parsed.mapspec_revision == 7
    assert parsed.screenshot.sha256 == "a" * 64
    assert parsed.deterministic_findings[0]["code"] == "layout_conflict"
    # 畸形输入：非 dict → None；缺键取默认；坏 ref → 无截图。
    assert VisualObservationInput.from_snapshot(None) is None
    assert VisualObservationInput.from_snapshot([1, 2]) is None
    empty = VisualObservationInput.from_snapshot({"trigger": "finalization"})
    assert empty.screenshot is None
    assert empty.deterministic_findings == ()
    assert VisualScreenshotRef.from_dict({"sha256": "x"}) is None


def test_trigger_whitelist_is_defensive():
    assert VisualObservationInput(trigger="finalization").trigger_known
    assert VisualObservationInput(trigger="visual_repair").trigger_known
    assert not VisualObservationInput(trigger="pan").trigger_known
    assert not VisualObservationInput(trigger="").trigger_known


def test_result_bounded_dict_has_no_bytes_and_honest_reason():
    res = VisualObservationResult.not_evaluated(
        "no_screenshot", provider="rules_only", screenshot_sha256="a" * 64)
    d = res.to_bounded_dict()
    assert d["status"] == "not_evaluated"
    assert d["reason"] == "no_screenshot"
    assert set(d.keys()) <= {"status", "provider", "finding_count",
                             "taxonomy_counts", "duration_ms", "reason",
                             "screenshot_sha256"}
    assert not res.evaluated


# ── 2. taxonomy 封闭归一 ──────────────────────────────────────────────────

def test_taxonomy_closed_and_finding_code_namespace():
    assert len(VISUAL_TAXONOMY) == 8
    assert len(set(VISUAL_TAXONOMY)) == 8
    assert finding_code("contrast") == "visual_contrast"
    assert finding_code("not_a_class") == ""          # 未知类诚实拒绝
    assert finding_code("") == ""


def test_normalize_keyword_priority_and_dimension_defaults():
    assert normalize_to_taxonomy(
        "readability", evidence="注记 overlap 重叠") == "label_collision"
    assert normalize_to_taxonomy(
        "readability", evidence="被上层 occlud 遮挡") == "overlap"
    assert normalize_to_taxonomy(
        "", defect_type="cut off 裁切", evidence="") == "crop"
    # 维度缺省（无关键词命中）
    assert normalize_to_taxonomy("color_discriminability") == "contrast"
    assert normalize_to_taxonomy("composition_balance") == "hierarchy"
    assert normalize_to_taxonomy("readability") == "legibility"
    assert normalize_to_taxonomy("spatial_alignment") == "overlap"
    # healer 类别直通（封闭映射）
    assert normalize_to_taxonomy("", healer_category="layer_order") == "overlap"
    # 完全不可映射 → ""
    assert normalize_to_taxonomy("nonexistent_dimension") == ""


def test_taxonomy_counts():
    counts = taxonomy_counts_of(["contrast", "contrast", "nonsense", ""])
    assert counts["contrast"] == 2
    assert counts["unmapped"] == 2


def test_deterministic_affinity_truth_table():
    assert deterministic_affinity("label_collision", "label_collision")
    assert deterministic_affinity("label_collision", "carto.label.collision_est")
    assert deterministic_affinity("contrast", "carto.color.separability")
    assert deterministic_affinity("legend_mismatch", "GRAMMAR.AUDIT.PAIRING")
    assert deterministic_affinity("contrast", "layer_transparent")
    # 不同类/无关码不亲和
    assert not deterministic_affinity("contrast", "label_collision")
    assert not deterministic_affinity("crop", "layout_conflict")
    assert not deterministic_affinity("", "layout_conflict")


# ── 3. 融合（deterministic wins）──────────────────────────────────────────

def _vf(code="visual_contrast", entity="L1", evidence="low contrast",
        severity="error"):
    return UnifiedFinding(
        domain="visual", code=code, severity=severity, source="test",
        scope="map", affected_entity=entity, evidence=evidence,
        repair_class="reapply_style",
        blocks_completion=False, degradation_only=True,
    )


def test_fusion_corroborates_and_strips_repair_class():
    det = SimpleNamespace(code="carto.color.separability", target="L1")
    outcome = fuse_visual_with_deterministic([_vf()], [det])
    assert outcome.corroborated == 1
    assert outcome.unmapped == 0
    fused = outcome.kept[0]
    assert fused.repair_class == ""                 # 修复归确定性通道
    assert fused.degradation_only is True
    assert "corroborates:contrast" in fused.evidence
    assert fused.code == "visual_contrast"
    assert fused.severity == "error"                # 披露面保真（封顶归管线）


def test_fusion_passthrough_and_unmapped_drop():
    det = SimpleNamespace(code="unrelated_code", target="L1")
    standalone = _vf(code="visual_crop", entity="L2")
    outcome = fuse_visual_with_deterministic([standalone], [det])
    assert outcome.corroborated == 0
    assert len(outcome.kept) == 1
    assert outcome.kept[0].evidence == "low contrast"  # 原样，无收据
    bad = UnifiedFinding(domain="visual", code="visual_whatnot",
                         severity="warning", source="t", affected_entity="x",
                         evidence="完全无关文本")
    outcome2 = fuse_visual_with_deterministic([bad], [])
    assert outcome2.unmapped == 1
    assert outcome2.kept == ()


def test_fusion_deterministic_side_never_removed():
    det = SimpleNamespace(code="carto.color.separability", target="L1")
    outcome = fuse_visual_with_deterministic(
        [_vf(), _vf(entity="L2", code="visual_overlap")], [det])
    assert outcome.corroborated == 1
    assert len(outcome.kept) == 2   # 确定性条目不在输入集合中，永不受影响


def test_visual_taxonomy_of_round_trip():
    assert visual_taxonomy_of(_vf(code="visual_overlap")) == "overlap"
    assert visual_taxonomy_of(
        _vf(code="visual_whoknows", evidence="labels overlap 重叠")
    ) == "label_collision"
    assert visual_taxonomy_of(_vf(code="visual_???", evidence="？？")) == ""
