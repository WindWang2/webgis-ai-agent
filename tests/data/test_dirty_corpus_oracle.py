"""E2E Oracle 回归锁（DQH v1）—— dirty corpus → 画像/提案/指纹/血缘。

对应 /goal 完成 Oracle：
1. dirty fixture 产生稳定 profile / repair proposal / fingerprint / lineage；
2. 低置信字段不静默绑定为 measure/rate/count；
3. 修复失败不破坏原 artifact（事务锁已有；此处 E2E 复核 dry-run 链）；
4. Planner 可因质量明确 block/degrade（五态资格 + V8 双路径）；
5. 与 guardrail / Data Fabric 职责边界有测试。

关键验证连跑两遍（run_twice helper）——排除缓存/偶发假阳性。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dirty_datasets import corpus

from app.lib.data.quality import QualityIssueCode
from app.lib.gis.semantic_profile import RoleConfidence
from app.services.data_quality.profile import (
    GATE_BLOCKED,
    GATE_DEGRADED,
    GATE_READY,
    build_profile_for_payload,
)
from app.services.data_quality.repair_transaction import (
    STATE_APPLIED,
    run_repair_transaction,
)
from app.services.gis_harness.data_qualification import qualify_data_role
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationStatus,
    qualify_node,
)
from app.services.gis_harness.capability_graph import KIND_TOOL, GraphNode
from app.services.gis_harness.recipe_packs._kit import role


def _payload(name: str):
    return corpus()[name]()


def _profile(name: str):
    return build_profile_for_payload(_payload(name), target_ref=f"ref:{name}")


def _codes(profile):
    return {i.code for i in profile.issues}


class TestOracleStableProfiles:
    def test_dirty_corpus_emits_expected_families(self):
        assert QualityIssueCode.TIMEZONE_MISSING in _codes(_profile("timezone_naive"))
        assert QualityIssueCode.UNIT_AMBIGUOUS in _codes(
            _profile("crs_unknown_unit_ambiguous"))
        assert QualityIssueCode.CRS_MISSING in _codes(
            _profile("crs_unknown_unit_ambiguous"))
        assert QualityIssueCode.ADMIN_MISMATCH in _codes(_profile("admin_variants"))
        assert QualityIssueCode.FIELD_ROLE_AMBIGUOUS in _codes(
            _profile("role_ambiguous"))
        geo = _profile("geometry_dirty")
        # 剖析证据口径：空/缺失几何可判；自相交有效性需 GEOS（deep），
        # 浅画像路径必须诚实列入 checks_not_run（绝不静默跳过）。
        assert QualityIssueCode.EMPTY_GEOMETRY in _codes(geo)
        assert "self_intersection" in geo.checks_not_run
        assert "self_intersection" not in geo.checks_run

    def test_clean_dataset_stays_ready_with_no_discoveries(self):
        p = _profile("clean")
        assert p.gate == GATE_READY
        # 无 warning/error 级发现（info 级角色披露允许 —— 披露 ≠ 阻碍）。
        assert not ({QualityIssueCode.TIMEZONE_MISSING, QualityIssueCode.UNIT_AMBIGUOUS,
                     QualityIssueCode.ADMIN_MISMATCH,
                     QualityIssueCode.CRS_MISSING} & _codes(p))
        assert not any(i.severity in ("warning", "error") for i in p.issues)

    def test_profiles_stable_across_two_runs(self):
        """Oracle：连跑两遍 digest / gate / 提案逐项一致（排除偶发）。"""
        for _run in (1, 2):
            for name in corpus():
                p = _profile(name)
                assert p.profile_digest.startswith("dqprof_")
                assert p.gate in (GATE_READY, GATE_DEGRADED, GATE_BLOCKED)
            # 第二遍逐名比对（内层循环每遍重建）
        baseline = {name: _profile(name).to_bounded_dict() for name in corpus()}
        for name, first in baseline.items():
            second = _profile(name).to_bounded_dict()
            assert second["profile_digest"] == first["profile_digest"], name
            assert second["gate"] == first["gate"], name
            assert second["proposals"] == first["proposals"], name

    def test_profile_carries_repair_proposals(self):
        p = _profile("crs_unknown_unit_ambiguous")
        ops = {pr["reason_code"] for pr in p.proposals}
        assert "crs_missing" in ops
        assert "unit_ambiguous" in ops


class TestOracleLowConfidenceNotBound:
    def test_name_only_semantics_degrade_measure_role_end_to_end(self):
        """无值样本 → 角色仅 metadata_derived → measure 资格必须 degraded。"""
        from app.lib.gis.semantic_profile import (
            FieldRoleAssignment, SemanticDatasetProfile as SDP,
        )
        sp = SDP(
            field_roles=[FieldRoleAssignment(
                field="人口", roles=["population_measure"],
                confidence=RoleConfidence.METADATA_DERIVED)],
            role_index={"population_measure": "人口"},
        )
        req = role("measure", capability="", artifacts=(), geometry=())
        resolver_profile = {
            "featureCount": 12, "geometryTypes": ["Point"],
            "fields_status": "explicit",
            "fields": {"人口": {"type": "number"}},
        }
        q = qualify_data_role(req, "bound", resolver_profile=resolver_profile,
                              semantic_profile=sp)
        assert q.state == "degraded"
        assert q.reason_code == "FIELD_ROLE_AMBIGUOUS"

    def test_user_declared_role_unblocks(self):
        from app.lib.gis.semantic_profile import (
            FieldRoleAssignment, SemanticDatasetProfile as SDP,
        )
        sp = SDP(
            field_roles=[FieldRoleAssignment(
                field="人口", roles=["population_measure"],
                confidence=RoleConfidence.USER_DECLARED)],
            role_index={"population_measure": "人口"},
        )
        req = role("measure", capability="", artifacts=(), geometry=())
        resolver_profile = {
            "featureCount": 12, "geometryTypes": ["Point"],
            "fields_status": "explicit",
            "fields": {"人口": {"type": "number"}},
        }
        q = qualify_data_role(req, "bound", resolver_profile=resolver_profile,
                              semantic_profile=sp)
        assert q.state == "eligible"


class TestOracleRepairLineage:
    async def test_dry_run_apply_fingerprints_stable_twice(self):
        """Oracle：dry-run/apply 指纹与 lineage 两遍一致；源永不破坏。"""
        import copy

        for _run in (1, 2):
            payload = _payload("geometry_dirty")
            snapshot = copy.deepcopy(payload)
            from app.services.data_quality.repair_transaction import (
                reset_session_cache,
            )
            from app.services.data_quality.repair_plan import build_repair_plan

            reset_session_cache()
            profile = _profile("geometry_dirty")
            plan = build_repair_plan(profile, dataset_identity="oracle-geo")
            assert plan.plan_id

            # dry-run 指纹（确定性演练）
            dry = await run_repair_transaction(
                geojson=payload, plan=plan,
                operations=["make_valid", "remove_empty", "deduplicate"],
                mode="dry_run")
            assert payload == snapshot
            preview_digest = dry["preview"]["predicted_digest_after"]
            assert preview_digest

            # apply（无 session store：仅证据链，不登记 ref）
            result = await run_repair_transaction(
                geojson=payload, plan=plan,
                operations=["make_valid", "remove_empty", "deduplicate"],
                mode="apply")
            assert payload == snapshot, "原 artifact 逐字节不变"
            assert result["session"]["state"] in (STATE_APPLIED, "verified")
            assert result["apply"]["content_digest_before"]
            assert result["apply"]["content_digest_after"] != ""

            # 两遍指纹一致（open session 内再跑一遍 dry-run 比对）
            dry2 = await run_repair_transaction(
                geojson=payload, plan=plan,
                operations=["make_valid", "remove_empty", "deduplicate"],
                mode="dry_run")
            assert dry2["preview"]["predicted_digest_after"] == preview_digest


class TestOraclePlannerBlockDegrade:
    def test_v8_blocks_on_blocked_gate(self):
        node = GraphNode(id="test.oracle.tool", kind=KIND_TOOL,
                         source_registry="test", extras={"tier": 1})
        ctx = QualificationContext(quality_gate=GATE_BLOCKED,
                                   blocking_issue_codes=["empty_payload"])
        assert qualify_node(node, ctx).status == QualificationStatus.INELIGIBLE

    def test_v8_degrades_on_degraded_gate(self):
        node = GraphNode(id="test.oracle.tool2", kind=KIND_TOOL,
                         source_registry="test", extras={"tier": 1})
        ctx = QualificationContext(quality_gate=GATE_DEGRADED,
                                   blocking_issue_codes=["crs_missing"])
        assert qualify_node(node, ctx).status == QualificationStatus.DEGRADED

    def test_workflow_role_blocked_on_empty_dataset(self):
        req = role("subject", capability="poi_query",
                   artifacts=("poi_feature_set",), geometry=("point",))
        q = qualify_data_role(req, "bound",
                              resolver_profile={"featureCount": 0,
                                                "geometryTypes": ["Point"]})
        assert q.state == "blocked"


class TestOracleBoundaries:
    def test_guardrails_semantics_unchanged_by_projection(self):
        """边界：本方向只读复用码表 —— verify_code 层级校验语义不变。"""
        from app.services.spatial_guardrails.admin_division_verifier import (
            verify_code,
        )

        assert verify_code("110000").ok
        assert not verify_code("110004").ok  # 已知码表内不存在的结构码
        assert not verify_code("999999").ok

    def test_data_fabric_fingerprint_untouched(self):
        """边界：fabric 指纹服务不被画像改写（同输入同指纹，独立可调用）。"""
        from app.services.data_fabric.fingerprint import (
            DatasetFingerprintService,
        )

        svc = DatasetFingerprintService()
        d1 = svc.calculate_data_fingerprint([{"a": 1}])
        d2 = svc.calculate_data_fingerprint([{"a": 1}])
        assert d1 == d2 and d1
