"""artifact_revisions DAO 测试（Wave 1 durable artifact store）。

覆盖：record 幂等复用 / revision_no 单调 / pin·unpin / head·list 查询 /
referencing_counts（GC 引用计数）。DB 夹具沿用
test_reproducible_gis_runtime.py 的项目域表重建模式。
"""
import uuid

import pytest
from sqlalchemy import select

import app.models.db_model  # noqa: F401 — 在 fixture 之前把 ORM 模型注册进 Base.metadata
import app.models.project  # noqa: F401
from app.core.database import Base, Engine, SessionLocal

_PROJECT_DOMAIN_TABLES = (
    "artifact_revisions",
    "map_products", "artifact_lineages", "artifacts",
    "workflow_runs", "workflow_revisions", "workflows",
    "project_datasets", "carto_project_facts", "projects",
)


@pytest.fixture
def db():
    from pathlib import Path

    Path("./data").mkdir(parents=True, exist_ok=True)
    metadata_tables = [
        t for t in Base.metadata.sorted_tables if t.name in _PROJECT_DOMAIN_TABLES
    ]
    for tbl in reversed(metadata_tables):
        tbl.drop(bind=Engine, checkfirst=True)
    for tbl in metadata_tables:
        tbl.create(bind=Engine, checkfirst=True)


def _mk_project(project_id):
    from app.models.db_model import User
    from app.models.project import Project, Workflow

    with SessionLocal() as s:
        s.merge(User(id="u_rev", username="rev", email="rev@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_rev"))
        s.add(Workflow(id=f"wf_{uuid.uuid4().hex[:8]}", project_id=project_id,
                       name="wf", version=1))
        s.commit()


def _mk_artifact(project_id, artifact_id):
    from sqlalchemy import select as sa_select

    from app.models.project import Artifact, Workflow

    with SessionLocal() as s:
        wf_id = s.execute(
            sa_select(Workflow.id).where(Workflow.project_id == project_id)
        ).scalars().first()
    with SessionLocal() as s:
        s.add(Artifact(id=artifact_id, project_id=project_id, name="out",
                       artifact_type="vector", metadata_json={"step_id": "s1"}))
        s.commit()
    return wf_id


def test_record_revision_idempotent_reuse_and_monotonic_no(db):
    from app.services.artifact_revisions import (
        head_revision,
        list_revisions,
        record_revision,
    )

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)

    with SessionLocal() as s:
        row1, created1 = record_revision(
            s, artifact_id=art_id, content_sha256="a" * 64,
            content_location="aaaa/" + "a" * 64 + ".json",
            content_type="json", byte_size=10,
        )
        s.commit()
        assert created1 is True and row1.revision_no == 1

        # 同内容重晋升：复用同一行，不 bump（幂等契约）
        row1b, created1b = record_revision(
            s, artifact_id=art_id, content_sha256="a" * 64,
            content_location="aaaa/" + "a" * 64 + ".json",
            content_type="json", byte_size=10,
        )
        assert created1b is False and row1b.id == row1.id
        assert row1b.revision_no == 1

        # 新内容：单调 +1
        row2, created2 = record_revision(
            s, artifact_id=art_id, content_sha256="b" * 64,
            content_location="bbbb/" + "b" * 64 + ".json",
            content_type="binary", byte_size=99,
        )
        s.commit()
        assert created2 is True and row2.revision_no == 2

        rows = list_revisions(s, art_id)
        assert [r.revision_no for r in rows] == [2, 1]  # 新→旧
        head = head_revision(s, art_id)
        assert head.id == row2.id and head.content_sha256 == "b" * 64
        assert head.content_type == "binary"
        # metadata 有界写入（≤16 键）
        over = {f"k{i}": i for i in range(30)}
        row3, created3 = record_revision(
            s, artifact_id=art_id, content_sha256="c" * 64,
            content_location="cccc/" + "c" * 64 + ".json",
            metadata=over,
        )
        s.commit()
        assert created3 is True and len(row3.revision_metadata or {}) <= 16


def test_pin_unpin_sets_head_revision(db):
    from app.services.artifact_revisions import (
        head_revision,
        pin_artifact,
        pinned_content_sha256s,
        record_revision,
    )

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)
    with SessionLocal() as s:
        record_revision(
            s, artifact_id=art_id, content_sha256="d" * 64,
            content_location="dddd/" + "d" * 64 + ".json", byte_size=1,
        )
        record_revision(
            s, artifact_id=art_id, content_sha256="e" * 64,
            content_location="eeee/" + "e" * 64 + ".json", byte_size=2,
        )
        s.commit()

    # 无权（不存在项目）→ None（tenant-checked）
    with SessionLocal() as s:
        assert pin_artifact(
            s, project_id="proj_ghost", artifact_id=art_id, pinned=True,
        ) is None

    with SessionLocal() as s:
        result = pin_artifact(
            s, project_id=project_id, artifact_id=art_id, pinned=True,
            user_id="u_rev",
        )
        assert result is not None and result["pinned"] is True
        assert result["content_sha256"] == "e" * 64, "pin 落在 head 修订上"
    with SessionLocal() as s:
        assert pinned_content_sha256s(s) == ["e" * 64]
        assert head_revision(s, art_id).pinned_at is not None

    # unpin 清空
    with SessionLocal() as s:
        result = pin_artifact(
            s, project_id=project_id, artifact_id=art_id, pinned=False,
            user_id="u_rev",
        )
        assert result["pinned"] is False
    with SessionLocal() as s:
        assert pinned_content_sha256s(s) == []
        assert head_revision(s, art_id).pinned_at is None


def test_referencing_counts_and_artifact_locations(db):
    from app.services.artifact_revisions import (
        referencing_counts,
        record_revision,
    )

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art1 = f"art_{uuid.uuid4().hex[:8]}"
    art2 = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art1)
    _mk_artifact(project_id, art2)
    loc = "cafe/" + "0" * 62 + ".json"
    with SessionLocal() as s:
        record_revision(s, artifact_id=art1, content_sha256="0" * 64,
                        content_location=loc)
        record_revision(s, artifact_id=art2, content_sha256="0" * 64,
                        content_location=loc)  # CAS 去重：两行同 location
        s.commit()
    with SessionLocal() as s:
        counts = referencing_counts(s, [loc, "ghost/x.json"])
        assert counts == {loc: 2}
        assert "ghost/x.json" not in counts


def test_clone_artifact_is_pointer_not_copy(db, tmp_path, monkeypatch):
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_revisions import (
        clone_artifact,
        head_revision,
        list_revisions,
    )

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)
    from app.models.project import Artifact

    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        art.metadata_json = {"content_status": "promoted",
                             "content_location": "cafe/" + "0" * 62 + ".json",
                             "content_payload_sha256": "0" * 64}
        s.commit()

    with SessionLocal() as s:
        clone = clone_artifact(
            s, project_id=project_id, artifact_id=art_id, user_id="u_rev",
        )
        assert clone is not None and clone.id != art_id
        assert clone.name == "out (clone)"
        meta = clone.metadata_json
        assert meta["content_location"] == "cafe/" + "0" * 62 + ".json"
        assert meta["cloned_from"] == art_id and meta["clone_kind"] == "pointer"
    # 新修订行复用同 content_sha256（零字节复制）
    with SessionLocal() as s:
        rev = head_revision(s, clone.id)
        assert rev.content_sha256 == "0" * 64
        assert rev.content_location == "cafe/" + "0" * 62 + ".json"
        assert len(list_revisions(s, clone.id)) == 1

    # 无持久内容的 artifact：诚实拒绝（绝不伪造内容指针）
    bare = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, bare)
    with SessionLocal() as s:
        assert clone_artifact(
            s, project_id=project_id, artifact_id=bare, user_id="u_rev",
        ) is None


# ── 路由层（POST/DELETE /projects/artifacts/{id}/pin、POST .../clone）──────


def _call_route(fn, **kwargs):
    """直接调用路由函数（Depends 默认值不生效，显式传参）—— 轻量接线
    冒烟；认证契约由 tests/test_api_auth_required.py 的 AST 扫描覆盖。"""
    from app.core.database import SessionLocal as _SL

    with _SL() as s:
        return fn(db=s, **kwargs)


def test_pin_unpin_clone_routes_end_to_end(db, tmp_path, monkeypatch):
    from app.api.routes.project import (
        clone_artifact_endpoint,
        pin_artifact_endpoint,
        unpin_artifact_endpoint,
    )
    from app.models.project import Artifact
    from app.schemas.project_schema import ArtifactPinRequest
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)
    loc = "beef/" + "1" * 60 + ".json"
    with SessionLocal() as s:
        from app.services.artifact_revisions import record_revision

        record_revision(s, artifact_id=art_id, content_sha256="1" * 64,
                        content_location=loc, byte_size=7)
        s.commit()

    user = {"user_id": "u_rev"}
    # pin（POST body 缺省 = pinned=True）
    body = _call_route(pin_artifact_endpoint, artifact_id=art_id,
                       data=None, user=user)
    assert body["status"] == "ok" and body["pinned"] is True
    assert body["content_sha256"] == "1" * 64
    # unpin（DELETE）
    body = _call_route(unpin_artifact_endpoint, artifact_id=art_id, user=user)
    assert body["pinned"] is False
    # clone（POST）→ 新行同 content_location（response_model：属性访问）
    body = _call_route(clone_artifact_endpoint, artifact_id=art_id, user=user)
    assert body.status == "ok" and body.source_artifact_id == art_id
    assert body.content_location == loc
    assert body.artifact_id != art_id
    clone_id = body.artifact_id
    with SessionLocal() as s:
        clone_row = s.execute(select(Artifact).where(
            Artifact.id == clone_id)).scalars().first()
        assert clone_row is not None
        assert clone_row.project_id == project_id

    # 租户外身份：项目无权 → 404 HTTPException（IDOR 同款不泄露存在性）
    import pytest as _pytest
    from fastapi import HTTPException

    outsider = {"user_id": "u_stranger"}
    with _pytest.raises(HTTPException) as ei:
        _call_route(pin_artifact_endpoint, artifact_id=art_id,
                    data=ArtifactPinRequest(pinned=True), user=outsider)
    assert ei.value.status_code == 404
    with _pytest.raises(HTTPException) as ei:
        _call_route(clone_artifact_endpoint, artifact_id=art_id, user=outsider)
    assert ei.value.status_code == 404
