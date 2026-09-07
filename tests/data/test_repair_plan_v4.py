"""Wave 4 —— RepairPlan / 修复执行证据 / 质量状态回写 / mapspec 指纹接线。

覆盖（audit 08-lineage-reproducibility-gaps.md §4/§5.1/§6.2 建议 2-3、7）：
- build_repair_plan：码 → 操作经 W3 单一映射（与 propose_repairs 对账）、
  plan_id 确定性（同输入同 id；identity 变 → id 变）、无映射码诚实不提案；
- 修复执行：修复输出 = **新 ref**（源载荷字节不变）、ledger 登记
  producer_tool + inputs 边、repair_evidence 落在血缘边上（有界、仅摘要、
  无要素载荷）、issue_codes 入证据；
- spatial audit issues 携带 repairable / remediation_op（有背书才 True，
  否则诚实 False/None）；
- quality_status 回写：有数据集行 → 更新；无 → 保持 unchecked；租户校验
  （跨项目 → 拒绝且行不变）；
- 迁移 0028 upgrade/downgrade 往返（scratch SQLite，仿 0027 迁移测试）；
- mapspec_fingerprint：会话地图上下文在场 → 引擎侧解析出指纹；缺失 → None。
"""
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.project import Artifact, ArtifactLineage, Project, ProjectDataset
from app.services.gis_harness.data_qualification import REMEDIATION_OPS

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_con, _):
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_project(db) -> Project:
    proj = Project(
        id=f"proj_{uuid.uuid4().hex[:16]}",
        name="p",
        owner_id=None,
        status="active",
    )
    db.add(proj)
    db.commit()
    return proj


def _make_dataset(db, project_id: str, quality_status: str = "unchecked") -> ProjectDataset:
    row = ProjectDataset(
        id=f"ds_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        name="ds",
        source_type="inline",
        source_ref="ref:src",
        quality_status=quality_status,
    )
    db.add(row)
    db.commit()
    return row


def _quality_report(issues_spec):
    """构造 app.lib.data.quality.QualityReport（鸭子类型亦可，但用真类型）。"""
    from app.lib.data.quality import QualityIssue, QualityIssueCode, QualityReport

    issues = [
        QualityIssue(
            code=QualityIssueCode(code),
            severity=severity,
            repairable=repairable,
            field=field,
        )
        for code, severity, repairable, field in issues_spec
    ]
    report = QualityReport(target_ref="ref:test", issues=issues)
    from app.lib.data.quality import compose_status

    report.status = compose_status(issues)
    return report


def _fc_invalid():
    """含无效几何（自相交 bowtie）+ 空几何的 FC。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    # bowtie：自相交
                    "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
                },
                "properties": {"name": "bowtie"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": []},
                "properties": {"name": "empty"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                "properties": {"name": "ok"},
            },
        ],
    }


# ── 1. build_repair_plan ──────────────────────────────────────────────────


class TestBuildRepairPlan:
    def test_codes_map_through_single_w3_mapping(self):
        from app.services.data_ingest.repair_planning import (
            propose_repairs_for_issue_codes,
        )
        from app.services.data_quality.repair_plan import build_repair_plan

        codes = ["crs_missing", "invalid_geometry", "empty_geometry", "encoding_issues"]
        report = _quality_report(
            [
                ("crs_missing", "warning", True, ""),
                ("invalid_geometry", "error", True, ""),
                ("empty_geometry", "warning", True, ""),
                ("encoding_issues", "warning", True, ""),
            ]
        )
        plan = build_repair_plan(report, dataset_identity="fp-abc")
        plan_ops = {step.operation for step in plan.operations}
        expected_ops = {
            p.operation for p in propose_repairs_for_issue_codes(codes)
        }
        assert plan_ops == expected_ops
        assert plan_ops <= set(REMEDIATION_OPS), "operation 必须属于 REMEDIATION_OPS 词表"
        # 每一步的 reason_codes 都能对回 W3 映射的操作
        for step in plan.operations:
            w3_ops = {
                p.operation
                for p in propose_repairs_for_issue_codes(list(step.reason_codes))
            }
            assert w3_ops == {step.operation}

    def test_plan_id_deterministic_and_identity_sensitive(self):
        from app.services.data_quality.repair_plan import build_repair_plan

        report = _quality_report([("invalid_geometry", "error", True, "")])
        p1 = build_repair_plan(report, dataset_identity="fp-1")
        p2 = build_repair_plan(report, dataset_identity="fp-1")
        p3 = build_repair_plan(report, dataset_identity="fp-2")
        assert p1.plan_id == p2.plan_id, "同报告同身份 ⇒ 同 plan_id"
        assert p1.plan_id != p3.plan_id, "身份变 ⇒ plan_id 变"
        assert p1.source_report_digest == p2.source_report_digest
        # created_at 是元数据，绝不参与 plan_id（p1/p2 生成时间必然不同）

    def test_unmapped_codes_honestly_absent(self):
        from app.services.data_quality.repair_plan import build_repair_plan

        report = _quality_report([("empty_payload", "error", False, "")])
        plan = build_repair_plan(report, dataset_identity="fp-x")
        assert plan.operations == [], "阻断且无修复操作的码不硬凑提案"

    def test_same_op_same_params_merge_reason_codes(self):
        from app.services.data_quality.repair_plan import build_repair_plan

        report = _quality_report(
            [
                ("invalid_geometry", "error", True, ""),
                ("self_intersection", "error", True, ""),
            ]
        )
        plan = build_repair_plan(report, dataset_identity="fp-merge")
        repair_geometry_steps = [s for s in plan.operations if s.operation == "repair_geometry"]
        assert len(repair_geometry_steps) == 1
        merged = set(repair_geometry_steps[0].reason_codes)
        assert {"invalid_geometry", "self_intersection"} <= merged


# ── 2. 修复执行：新 ref + 血缘边上的 repair_evidence ─────────────────────


class TestRepairExecution:
    async def test_pipeline_evidence_is_bounded_and_per_op(self):
        from app.services.spatial_repair_pipeline import SpatialRepairPipeline

        repaired, logs, ops_evidence = SpatialRepairPipeline.repair_dataset_detailed(
            _fc_invalid(), ops=["make_valid", "remove_empty"]
        )
        by_op = {e["op"]: e for e in ops_evidence}
        assert by_op["make_valid"]["features_affected"] == 1
        assert by_op["remove_empty"]["features_affected"] >= 1
        assert all(set(e) == {"op", "features_affected", "failed_count"} for e in ops_evidence)
        assert len(ops_evidence) <= 16
        # 兼容包装：2-tuple 契约不变
        repaired2, logs2 = SpatialRepairPipeline.repair_dataset(
            _fc_invalid(), ops=["remove_empty"]
        )
        assert isinstance(repaired2, dict) and isinstance(logs2, list)

    async def test_repaired_output_is_new_ref_source_untouched(self):
        from app.services.artifact_registry import get_artifact
        from app.services.data_quality.repair_execution import execute_repair
        from app.services.session_data import session_data_manager

        sid = f"repair-v4-{uuid.uuid4().hex[:8]}"
        source = _fc_invalid()
        source_copy = json.loads(json.dumps(source))
        source_ref = await session_data_manager.store(sid, source, prefix="src")

        result = await execute_repair(
            geojson=source,
            operations=["make_valid", "remove_empty"],
            session_id=sid,
            source_ref=source_ref,
            issue_codes=["invalid_geometry", "empty_geometry"],
        )
        # (a) 新 ref；源载荷字节不变（canonical JSON 相等 = 字节级内容一致）
        assert result["repaired_ref"] and result["repaired_ref"] != source_ref
        stored_source = await session_data_manager.get(sid, source_ref)
        assert json.dumps(stored_source, sort_keys=True) == json.dumps(
            source_copy, sort_keys=True
        ), "源载荷绝不因修复被覆写"
        assert result["ref_registration_error"] is None
        # ledger：producer_tool + honest inputs 边
        rec = await get_artifact(sid, result["repaired_ref"])
        assert rec is not None
        assert rec.producer_tool == "repair_spatial_dataset"
        assert rec.inputs == [source_ref]
        assert rec.metadata["replaces"] == source_ref
        # evidence 有界、含 issue_codes
        ev = result["repair_evidence"]
        assert set(ev["issue_codes_addressed"]) == {"invalid_geometry", "empty_geometry"}
        assert ev["content_digest_before"] and ev["content_digest_after"]
        assert ev["feature_count_after"] == 2  # 空几何被剔除
        assert len(json.dumps(ev)) < 4096, "证据必须保持有界摘要"

    async def test_repair_evidence_persisted_on_lineage_edge(self, db_session):
        from app.services.data_quality.repair_execution import (
            build_repair_evidence,
            persist_repair_lineage,
        )

        db = db_session
        proj = _make_project(db)
        ds = _make_dataset(db, proj.id)

        evidence = build_repair_evidence(
            ops_evidence=[{"op": "make_valid", "features_affected": 1, "failed_count": 0}],
            issue_codes=["invalid_geometry"],
            feature_count_before=3,
            feature_count_after=2,
            content_digest_before="a" * 64,
            content_digest_after="b" * 64,
            output_ref="ref:repaired-1",
        )
        artifact_id = persist_repair_lineage(
            db,
            proj.id,
            repair_evidence=evidence,
            content_fingerprint="b" * 64,
            storage_ref="ref:repaired-1",
            source_dataset_id=ds.id,
            source_dataset_fingerprint=ds.version_fingerprint,
        )
        row = db.execute(
            select(ArtifactLineage).where(ArtifactLineage.artifact_id == artifact_id)
        ).scalars().first()
        assert row is not None
        assert row.repair_evidence["plan_id"] == ""
        assert row.repair_evidence["ops_applied"] == ["make_valid"]
        assert row.repair_evidence["issue_codes_addressed"] == ["invalid_geometry"]
        assert row.repair_evidence["feature_count_before"] == 3
        assert row.repair_evidence["feature_count_after"] == 2
        assert row.repair_evidence["content_digest_before"] == "a" * 64
        assert row.source_dataset_id == ds.id, "INV-LIN4：根边携带输入数据集"
        # 仅摘要：证据里没有任何要素载荷键
        blob = json.dumps(row.repair_evidence)
        assert "coordinates" not in blob and "features\":" not in blob.replace(" ", "")
        assert "FeatureCollection" not in blob

    async def test_evidence_absent_when_no_repair(self, db_session):
        from app.services.lineage_service import LineageService

        db = db_session
        proj = _make_project(db)
        art = Artifact(
            id=f"art_{uuid.uuid4().hex[:8]}",
            project_id=proj.id,
            name="plain",
            artifact_type="vector",
        )
        db.add(art)
        db.commit()
        LineageService.record_lineage(db=db, artifact_id=art.id, producing_tool="t")
        row = db.execute(
            select(ArtifactLineage).where(ArtifactLineage.artifact_id == art.id)
        ).scalars().first()
        assert row.repair_evidence is None, "默认 None → 列绝不被碰"


# ── 3. spatial audit issues 携带修复联动 ─────────────────────────────────


class TestSpatialAuditRepairLinkage:
    def test_issues_carry_repairable_and_remediation_op(self):
        from app.services.spatial_quality_service import SpatialQualityEngine

        fc = {
            "type": "FeatureCollection",
            "features": [
                {  # 自相交 + EXTREME 场景用简单 bowtie 即可
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
                    },
                    "properties": {"name": "bowtie"},
                },
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"name": "null-geom"},
                },
                {  # 悬空端点：无可确定性修复 → 诚实 None
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[10.0, 10.0], [10.001, 10.001], [20.0, 20.0]],
                    },
                    "properties": {"name": "line"},
                },
            ],
        }
        report = SpatialQualityEngine.audit_dataset(fc, crs="EPSG:4326")
        by_code = {}
        for issue in report.issues:
            by_code.setdefault(issue.code, issue)
        assert by_code["SELF_INTERSECTION"].repairable is True
        assert by_code["SELF_INTERSECTION"].remediation_op in REMEDIATION_OPS
        assert by_code["EMPTY_GEOMETRY"].repairable is True
        assert by_code["EMPTY_GEOMETRY"].remediation_op in REMEDIATION_OPS
        # 无确定性修复的维度：诚实 False / None（不硬凑）
        assert by_code["DANGLING_ENDPOINT"].repairable is False
        assert by_code["DANGLING_ENDPOINT"].remediation_op is None

    def test_existing_codes_unchanged_additive_only(self):
        from app.services.spatial_quality_service import SpatialQualityEngine

        report = SpatialQualityEngine.audit_dataset(_fc_invalid(), crs="EPSG:4326")
        codes = {i.code for i in report.issues}
        assert {"SELF_INTERSECTION", "EMPTY_GEOMETRY"} <= codes
        # 新字段有默认值 —— 旧构造路径（不传新字段）不破坏
        from app.services.spatial_quality_service import QualityIssue

        legacy = QualityIssue(dimension="geometry", code="X", level="info", message="m")
        assert legacy.repairable is False and legacy.remediation_op is None


# ── 4. quality_status 回写 ────────────────────────────────────────────────


class TestQualityStatusWriteBack:
    def test_dataset_row_updated_after_quality(self, db_session):
        from app.services.project_service import ProjectService

        db = db_session
        proj = _make_project(db)
        ds = _make_dataset(db, proj.id)
        assert ds.quality_status == "unchecked"

        report = _quality_report([("crs_missing", "warning", True, "")])
        written = ProjectService.record_dataset_quality(
            db, proj.id, ds.id, report
        )
        assert written == "repairable"
        db.expire_all()
        row = db.execute(
            select(ProjectDataset).where(ProjectDataset.id == ds.id)
        ).scalar_one()
        assert row.quality_status == "repairable", "compose_status 结论落库"

    def test_valid_report_marks_valid(self, db_session):
        from app.services.project_service import ProjectService

        db = db_session
        proj = _make_project(db)
        ds = _make_dataset(db, proj.id)
        report = _quality_report([])
        assert ProjectService.record_dataset_quality(db, proj.id, ds.id, report) == "valid"

    def test_missing_dataset_row_skips_honestly(self, db_session):
        from app.services.project_service import ProjectService

        db = db_session
        proj = _make_project(db)
        report = _quality_report([("crs_missing", "warning", True, "")])
        assert ProjectService.record_dataset_quality(
            db, proj.id, "ds_does_not_exist", report
        ) is None

    def test_tenant_check_rejects_cross_project(self, db_session):
        from app.services.project_service import ProjectService

        db = db_session
        proj_a = _make_project(db)
        proj_b = _make_project(db)
        ds = _make_dataset(db, proj_a.id)
        report = _quality_report([("crs_missing", "warning", True, "")])
        # 跨项目（未授权）→ None 且行不变
        assert ProjectService.record_dataset_quality(
            db, proj_b.id, ds.id, report
        ) is None
        db.expire_all()
        row = db.execute(
            select(ProjectDataset).where(ProjectDataset.id == ds.id)
        ).scalar_one()
        assert row.quality_status == "unchecked"

    def test_owned_project_requires_caller(self, db_session):
        from app.services.project_service import ProjectService

        db = db_session
        from app.models.db_model import User

        db.add(User(id="user-1", username="u1", email="u1@example.com",
                    password_hash="x", role="viewer", is_active=True))
        proj = Project(
            id=f"proj_{uuid.uuid4().hex[:16]}",
            name="owned",
            owner_id="user-1",
            status="active",
        )
        db.add(proj)
        ds = _make_dataset(db, proj.id)
        db.commit()
        report = _quality_report([])
        # 匿名调用 owned 项目 → 拒绝（404 语义在路由层；helper 返回 None）
        assert ProjectService.record_dataset_quality(
            db, proj.id, ds.id, report
        ) is None
        assert (
            ProjectService.record_dataset_quality(
                db, proj.id, ds.id, report, user_id="user-1"
            )
            == "valid"
        )


# ── 5. 迁移 0028 往返（scratch SQLite，仿 0027 迁移测试）─────────────────


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DATABASE_URL": f"sqlite:///{db_path}",
            "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
            "USE_REDIS": "false",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )


def _columns(db_path: Path, table: str) -> set:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ddl(db_path: Path, table: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row[0] if row else ""


class TestMigration0028:
    def test_upgrade_downgrade_roundtrip(self, tmp_path):
        db_path = tmp_path / "repair-v4-0028.db"
        up = _alembic(db_path, "upgrade", "head")
        assert up.returncode == 0, f"upgrade head 失败:\n{up.stdout}\n{up.stderr}"

        # 列在场
        assert "repair_evidence" in _columns(db_path, "artifact_lineages")
        # CHECK 词表放宽：repairable/blocked 可写
        ddl = _ddl(db_path, "project_datasets")
        assert "repairable" in ddl and "blocked" in ddl
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO projects (id, name, status) VALUES ('p1', 'p', 'active')"
            )
            conn.execute(
                "INSERT INTO project_datasets (id, project_id, name, source_type,"
                " quality_status) VALUES ('d0', 'p1', 'd', 'inline', 'unchecked')"
            )
            conn.execute(
                "INSERT INTO project_datasets (id, project_id, name, source_type,"
                " quality_status) VALUES ('d1', 'p1', 'd', 'inline', 'repairable')"
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO project_datasets (id, project_id, name, source_type,"
                    " quality_status) VALUES ('d9', 'p1', 'd', 'inline', 'bogus')"
                )

        # downgrade → 列消失 + 旧 CHECK 恢复。诚实体：降级要求先清退新词表
        # 状态（迁移 docstring 有声明）—— 'repairable' 行必须在降级前清退。
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM project_datasets WHERE id='d1'")
            conn.commit()
        down = _alembic(db_path, "downgrade", "0027_upload_content_sha256")
        assert down.returncode == 0, f"downgrade 失败:\n{down.stdout}\n{down.stderr}"
        assert "repair_evidence" not in _columns(db_path, "artifact_lineages")
        old_ddl = _ddl(db_path, "project_datasets")
        assert "repairable" not in old_ddl
        with sqlite3.connect(db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO project_datasets (id, project_id, name, source_type,"
                    " quality_status) VALUES ('d3', 'p1', 'd', 'inline', 'repairable')"
                )

        # 再升级 → 列回来、既有数据仍在
        again = _alembic(db_path, "upgrade", "head")
        assert again.returncode == 0, f"re-upgrade 失败:\n{again.stdout}\n{again.stderr}"
        assert "repair_evidence" in _columns(db_path, "artifact_lineages")
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM project_datasets").fetchone()[0]
        assert count == 1, "往返不丢数据行"

    def test_upgrade_idempotent_on_existing_column(self, tmp_path):
        db_path = tmp_path / "repair-v4-0028-idem.db"
        assert _alembic(db_path, "upgrade", "head").returncode == 0
        # 已是 head（列 + 新 CHECK 都在）→ 重复 upgrade 不报错（守卫幂等）
        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, f"幂等 upgrade 失败:\n{result.stdout}\n{result.stderr}"
        assert "repair_evidence" in _columns(db_path, "artifact_lineages")


# ── 6. mapspec_fingerprint 接线 ──────────────────────────────────────────


class TestSessionMapspecFingerprint:
    async def test_resolves_fingerprint_when_map_context_present(self):
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.workflow_engine import WorkflowEngine

        sid = f"mapspec-fp-{uuid.uuid4().hex[:8]}"
        mapspec = {
            "layers": [
                {
                    "id": "lyr-1",
                    "kind": "vector",
                    "source_ref": "ref:abc",
                    "style": {"fill_color": "#08519c"},
                }
            ],
            "projection": "EPSG:4326",
        }
        await mapspec_store_instance.save_mapspec(sid, mapspec)
        fp = await WorkflowEngine._session_mapspec_fingerprint(sid)
        assert fp == cartographic_fingerprint(mapspec)
        assert fp.startswith("carto-sha256:")

    async def test_none_without_session_or_mapspec(self):
        from app.services.workflow_engine import WorkflowEngine

        assert await WorkflowEngine._session_mapspec_fingerprint(None) is None
        assert (
            await WorkflowEngine._session_mapspec_fingerprint(
                f"no-mapspec-{uuid.uuid4().hex[:8]}"
            )
            is None
        )

    def test_record_lineage_persists_mapspec_and_evidence_together(self, db_session):
        from app.services.lineage_service import LineageService

        db = db_session
        proj = _make_project(db)
        art = Artifact(
            id=f"art_{uuid.uuid4().hex[:8]}",
            project_id=proj.id,
            name="mapspecced",
            artifact_type="vector",
        )
        db.add(art)
        db.commit()
        LineageService.record_lineage(
            db=db,
            artifact_id=art.id,
            producing_tool="t",
            mapspec_fingerprint="carto-sha256:" + "0" * 64,
            repair_evidence={"plan_id": "rplan_x", "ops_applied": ["make_valid"]},
        )
        row = db.execute(
            select(ArtifactLineage).where(ArtifactLineage.artifact_id == art.id)
        ).scalars().first()
        assert row.mapspec_fingerprint == "carto-sha256:" + "0" * 64
        assert row.repair_evidence["plan_id"] == "rplan_x"
