"""Final Map Verification（Goal §九）回归锁。

不变式：
- 三组 V3 缺口检查确定性：图层顺序（结果层不得压在上下文层之下）、
  结果越界（bbox 与观察视口相交性）、陈旧覆盖层（仅死 ref / superseded
  ref 图层告警，用户有效图层零误伤）；
- 裁决聚合确定性：verified / verified_with_degradation / failed /
  unknown；warning 级发现不推翻既有 status 语义；
- pipeline 集成：final_map_status 进入 MapCompletionResult 序列化与
  chapter.map_product 持久化块；
- final_gate 强制门：非 READY 裁决绕过幂等门重验，READY 会话幂等跳过。
"""
from __future__ import annotations


from app.services.gis_harness.completion.contracts import (
    FINAL_MAP_DEGRADED,
    FINAL_MAP_FAILED,
    FINAL_MAP_UNKNOWN,
    FINAL_MAP_VERIFIED,
    F_EXTENT_MISMATCH,
    F_LAYER_ORDER,
    F_STALE_OVERLAY,
    RENDER_ISSUES,
    RENDER_STALE,
    RENDER_VERIFIED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    MapCompletionFinding,
)
from app.services.gis_harness.completion.map_verification import (
    aggregate_final_map_status,
    collect_final_map_findings,
)


def _chapter() -> dict:
    return {
        "map_layers": [
            {"layer_id": "ctx_boundary", "role": "reference", "enabled": True},
            {"layer_id": "result_points", "role": "primary", "enabled": True},
        ],
    }


def _mapspec(order: list) -> dict:
    return {
        "layers": [{"id": lid, "source": f"src_{lid}"} for lid in order],
        "sources": {f"src_{lid}": {"id": f"src_{lid}", "ref": f"ref:{lid}"}
                    for lid in order},
    }


class TestLayerOrder:
    def test_result_below_context_flagged(self):
        findings = collect_final_map_findings(
            _chapter(), _mapspec(["result_points", "ctx_boundary"]))
        codes = [f.code for f in findings]
        assert F_LAYER_ORDER in codes

    def test_result_above_context_clean(self):
        findings = collect_final_map_findings(
            _chapter(), _mapspec(["ctx_boundary", "result_points"]))
        assert all(f.code != F_LAYER_ORDER for f in findings)

    def test_deterministic(self):
        f1 = collect_final_map_findings(
            _chapter(), _mapspec(["result_points", "ctx_boundary"]))
        f2 = collect_final_map_findings(
            _chapter(), _mapspec(["result_points", "ctx_boundary"]))
        assert [x.to_dict() for x in f1] == [x.to_dict() for x in f2]


class TestExtentCheck:
    def test_intersecting_viewport_clean(self):
        obs = {"viewport": {"bbox": [103.9, 30.5, 104.3, 30.8]}}
        findings = collect_final_map_findings(
            _chapter(), _mapspec(["ctx_boundary", "result_points"]),
            result_bbox=[104.0, 30.6, 104.2, 30.7],
            render_observation=obs)
        assert all(f.code != F_EXTENT_MISMATCH for f in findings)

    def test_disjoint_viewport_flagged(self):
        obs = {"viewport": {"bbox": [110.0, 35.0, 112.0, 37.0]}}
        findings = collect_final_map_findings(
            _chapter(), _mapspec(["ctx_boundary", "result_points"]),
            result_bbox=[104.0, 30.6, 104.2, 30.7],
            render_observation=obs)
        assert any(f.code == F_EXTENT_MISMATCH for f in findings)

    def test_no_viewport_fact_no_finding(self):
        """无视口事实（旧客户端）→ 不虚构越界。"""
        findings = collect_final_map_findings(
            _chapter(), _mapspec(["ctx_boundary", "result_points"]),
            result_bbox=[104.0, 30.6, 104.2, 30.7],
            render_observation={"viewport": {"zoom": 10}})
        assert findings == []


class TestStaleOverlay:
    """生产形态：descriptors 以 **ref id** 为键；图层 source 经 MapSpec
    sources 二跳取得 ref 指针（review M1 键位对齐）。"""

    @staticmethod
    def _spec_with(stale_ref: bool) -> dict:
        layers = [{"id": lid, "source": f"src_{lid}"}
                  for lid in ("ctx_boundary", "result_points", "old_result")]
        sources = {
            "src_ctx_boundary": {"id": "src_ctx_boundary", "ref": "ref:ctx"},
            "src_result_points": {"id": "src_result_points", "ref": "ref:pts"},
        }
        if stale_ref:
            sources["src_old_result"] = {
                "id": "src_old_result", "ref": "ref:old_result"}
        return {"layers": layers, "sources": sources}

    def test_evicted_ref_layer_flagged(self):
        """ref 已不在 ref store（descriptors 无键）→ 死层告警。"""
        findings = collect_final_map_findings(
            _chapter(), self._spec_with(stale_ref=True),
            descriptors={"ref:ctx": {"status": "active"},
                         "ref:pts": {"status": "active"}})
        stale = [f for f in findings if f.code == F_STALE_OVERLAY]
        assert len(stale) == 1
        assert stale[0].target == "old_result"

    def test_superseded_ref_layer_flagged(self):
        descriptors = {
            "ref:ctx": {"status": "active"},
            "ref:pts": {"status": "active"},
            "ref:old_result": {"status": "superseded"},
        }
        findings = collect_final_map_findings(
            _chapter(), self._spec_with(stale_ref=True),
            descriptors=descriptors)
        stale = [f for f in findings if f.code == F_STALE_OVERLAY]
        assert len(stale) == 1
        assert stale[0].target == "old_result"

    def test_live_user_layer_never_flagged(self):
        """用户添加的有效图层（ref 存活）零误伤。"""
        descriptors = {
            "ref:ctx": {"status": "active"},
            "ref:pts": {"status": "active"},
            "ref:old_result": {"status": "active"},
        }
        findings = collect_final_map_findings(
            _chapter(), self._spec_with(stale_ref=True),
            descriptors=descriptors)
        assert all(f.code != F_STALE_OVERLAY for f in findings)

    def test_no_ref_semantics_not_flagged(self):
        """无 ref 指针的 basemap/xyz source → 不参与判定。"""
        mapspec = {"layers": [
            {"id": "ctx_boundary", "source": "src_ctx"},
            {"id": "result_points", "source": "src_pts"},
            {"id": "basemap_xyz", "source": "src_xyz"},
        ], "sources": {
            "src_ctx": {"id": "src_ctx", "ref": "ref:ctx"},
            "src_pts": {"id": "src_pts", "ref": "ref:pts"},
            "src_xyz": {"id": "src_xyz"},   # 无 ref
        }}
        findings = collect_final_map_findings(
            _chapter(), mapspec,
            descriptors={"ref:ctx": {"status": "active"},
                         "ref:pts": {"status": "active"}})
        assert all(f.code != F_STALE_OVERLAY for f in findings)


class TestAggregation:
    def test_verified_clean(self):
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_COMPLETE,
            render_status=RENDER_VERIFIED, v3_findings=[])
        assert status == FINAL_MAP_VERIFIED

    def test_degraded_with_warning_findings(self):
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_COMPLETE,
            render_status=RENDER_VERIFIED,
            v3_findings=[MapCompletionFinding(
                code=F_LAYER_ORDER, severity="warning", target="x")])
        assert status == FINAL_MAP_DEGRADED

    def test_degraded_with_stale_observation(self):
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_COMPLETE,
            render_status=RENDER_STALE, v3_findings=[])
        assert status == FINAL_MAP_DEGRADED

    def test_render_issues_maps_to_degraded_not_failed(self):
        """P9 语义对齐（review M2）：render issues 可自愈 → degraded。"""
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_COMPLETE,
            render_status=RENDER_ISSUES, v3_findings=[])
        assert status == FINAL_MAP_DEGRADED

    def test_failed_on_base_failed(self):
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_FAILED,
            render_status=RENDER_VERIFIED, v3_findings=[])
        assert status == FINAL_MAP_FAILED

    def test_unknown_without_planned_layers(self):
        status = aggregate_final_map_status(
            has_planned_layers=False, base_status=STATUS_COMPLETE,
            render_status=RENDER_VERIFIED, v3_findings=[])
        assert status == FINAL_MAP_UNKNOWN

    def test_needs_repair_maps_to_degraded(self):
        status = aggregate_final_map_status(
            has_planned_layers=True, base_status=STATUS_NEEDS_REPAIR,
            render_status=RENDER_VERIFIED, v3_findings=[])
        assert status == FINAL_MAP_DEGRADED


class TestFinalGate:
    def test_final_map_status_serialized(self):
        result = MapCompletionResultRef()
        result.final_map_status = FINAL_MAP_DEGRADED
        assert result.to_dict()["final_map_status"] == FINAL_MAP_DEGRADED

    def test_default_is_unknown(self):
        result = MapCompletionResultRef()
        assert result.final_map_status == FINAL_MAP_UNKNOWN


def MapCompletionResultRef():
    from app.services.gis_harness.completion.contracts import MapCompletionResult
    return MapCompletionResult()


class TestDedupGate:
    """final_gate 强制终验门（review R7：两分支单测）。"""

    @staticmethod
    def _gate(stored, *, force=False, final_gate=False, revision=7):
        from app.services.gis_harness.completion.pipeline import _dedup_gate_blocks
        return _dedup_gate_blocks(
            stored, {"map_layers": []}, revision, 3,
            force=force, final_gate=final_gate)

    @staticmethod
    def _stored(verdict="READY", status="complete"):
        # rows_fingerprint 与空章节（无行）对齐 —— 门比较同宽截断
        return {"product_verdict": verdict, "status": status,
                "checked_revision": 7, "render_observation_seq": 3,
                "rows_fingerprint": ""}

    def test_ready_session_idempotent_skip(self):
        assert self._gate(self._stored()) is True

    def test_needs_repair_session_forced_by_final_gate(self):
        stored = self._stored(verdict="NEEDS_REPAIR", status="needs_repair")
        assert self._gate(stored, final_gate=True) is False
        # 无 final_gate 时幂等跳过（既有语义）
        assert self._gate(stored, final_gate=False) is True

    def test_missing_verdict_counts_as_not_ready(self):
        """verdict 缺失（旧块）→ 非 READY → final_gate 强制重验。"""
        stored = self._stored(verdict="", status="needs_repair")
        del stored["product_verdict"]
        stored["product_verdict"] = ""
        assert self._gate(stored, final_gate=True) is False

    def test_force_bypasses_gate_unconditionally(self):
        assert self._gate(self._stored(), force=True) is False

    def test_revision_change_breaks_gate(self):
        assert self._gate(self._stored(), revision=9) is False

    def test_non_terminal_status_never_skips(self):
        assert self._gate(self._stored(status="pending")) is False
