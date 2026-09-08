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


# ── round-1 review fixes：并发冲突路径（expunge 崩溃 / revision_no 撞号）──


class _RacingSession:
    """把两会话竞态序列化的 session 代理：winner 行在 loser 的 savepoint
    打开时经**独立连接**提交（loser 的 SELECT 已错过它），随后 loser 的
    flush 抛唯一约束 IntegrityError —— round-1 review MAJOR 的 repro。"""

    def __init__(self, real, winner_row):
        self._real = real
        self._winner_row = winner_row
        self.expunge_calls = 0

    def begin_nested(self):
        with SessionLocal() as w:
            w.add(self._winner_row)
            w.commit()  # winner 在 loser INSERT 落定前落地

        class _SP:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _SP()

    def add(self, obj):
        # 模拟"INSERT 进行中"：savepoint 回滚后该行已分离出会话 —— 绝不真
        # 挂到会话上（否则 re-SELECT 的 autoflush 会再造一次真实冲突）。
        self._added = obj

    def flush(self):
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError(
            "INSERT INTO artifact_revisions ...", {},
            Exception("UNIQUE constraint failed: artifact_revisions.artifact_id,"
                      " artifact_revisions.content_sha256"),
        )

    def execute(self, *a, **k):
        return self._real.execute(*a, **k)

    def expunge(self, *a, **k):
        self.expunge_calls += 1
        raise AssertionError(
            "savepoint 回滚后 row 已分离 —— expunge 会抛 InvalidRequestError "
            "中断晋升（round-1 review MAJOR），绝不能被调用")


def test_record_revision_conflict_returns_winner_row(db):
    """并发同内容晋升：winner 在 loser 的 SELECT 之后才提交 —— savepoint 撞
    (artifact_id, content_sha256) 唯一约束后，loser 必须 re-SELECT 复用
    winner 行并正常返回（此前对已分离 row 的 expunge 抛 InvalidRequestError
    中断晋升）。"""
    from app.services.artifact_revisions import (
        list_revisions,
        record_revision,
    )

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)

    sha = "f" * 64
    from app.models.project import ArtifactRevision

    winner_id = str(uuid.uuid4())
    winner = ArtifactRevision(
        id=winner_id, artifact_id=art_id, revision_no=1,
        content_sha256=sha, content_location=f"{sha[:4]}/{sha}.json",
        content_type="json", byte_size=5,
    )
    with SessionLocal() as s:
        racing = _RacingSession(s, winner)
        row, created = record_revision(
            racing, artifact_id=art_id, content_sha256=sha,
            content_location=f"{sha[:4]}/{sha}.json", content_type="json",
            byte_size=5,
        )
        assert racing.expunge_calls == 0
        assert created is False
        assert row.id == winner_id, "loser 必须复用 winner 行（幂等契约）"
        assert row.revision_no == 1

    with SessionLocal() as s:
        rows = list_revisions(s, art_id)
        assert [r.id for r in rows] == [winner_id], "只落一行"


def test_record_revision_retry_distinct_monotonic_revision_no(db, monkeypatch):
    """并发不同内容修订撞 (artifact_id, revision_no)（0030 唯一约束）：
    loser 读到过期 head → 同号撞约束 → 重读 head 重试一次 → revision_no
    单调且不重复。"""
    from types import SimpleNamespace

    import app.services.artifact_revisions as ar

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)

    from app.models.project import ArtifactRevision

    sha_a, sha_b = "a" * 64, "b" * 64
    # 已有 head=1；并发对手：revision_no=2 已落库（loser 的视图尚未看到它）
    with SessionLocal() as s:
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=art_id, revision_no=1,
            content_sha256=sha_a, content_location=f"{sha_a[:4]}/{sha_a}.json",
            content_type="json", byte_size=1,
        ))
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=art_id, revision_no=2,
            content_sha256=sha_b, content_location=f"{sha_b[:4]}/{sha_b}.json",
            content_type="json", byte_size=1,
        ))
        s.commit()

    real_head_revision = ar.head_revision
    head_reads = {"n": 0}

    def stale_head(db, artifact_id):
        head_reads["n"] += 1
        if head_reads["n"] == 1:
            return SimpleNamespace(revision_no=1)  # 过期 head（竞态窗口）
        return real_head_revision(db, artifact_id)

    monkeypatch.setattr(ar, "head_revision", stale_head)

    sha_c = "c" * 64
    with SessionLocal() as s:
        row, created = ar.record_revision(
            s, artifact_id=art_id, content_sha256=sha_c,
            content_location=f"{sha_c[:4]}/{sha_c}.json",
            content_type="json", byte_size=3,
        )
        s.commit()
        assert created is True
        assert row.revision_no == 3, "撞号后重读 head 顺延，绝不重复 revision_no"
        assert head_reads["n"] >= 2, "首试撞约束后必须重试一次"

    with SessionLocal() as s:
        nos = [r.revision_no for r in ar.list_revisions(s, art_id)]
        assert sorted(nos) == sorted(set(nos)), "修订号绝不重复"
        assert sorted(nos) == [1, 2, 3], "并发插入产出单调且唯一的修订号"


def test_record_revision_unique_revision_no_constraint_enforced(db):
    """模型级 (artifact_id, revision_no) 唯一约束（0030）在 DB 层生效。"""
    from sqlalchemy.exc import IntegrityError

    from app.models.project import ArtifactRevision

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)
    with SessionLocal() as s:
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=art_id, revision_no=1,
            content_sha256="1" * 64, content_location="1111/" + "1" * 60 + ".json",
        ))
        s.commit()
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=art_id, revision_no=1,
            content_sha256="2" * 64, content_location="2222/" + "2" * 60 + ".json",
        ))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        rows = s.execute(select(ArtifactRevision)).scalars().all()
        assert len(rows) == 1
        assert {r.revision_no for r in rows} == {1}


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


# ── round-1 review PERF MAJOR-1/2：分片 + SQL 聚合 + 有界查询数 ───────────


def test_chunked_helper_bounds_and_reassembly(monkeypatch):
    from app.services import artifact_revisions as ar

    items = list(range(25))
    # 缺省分片：单片（≤ 上限）
    single = list(ar.chunked(items))
    assert single == [items]
    # 显式小片：每片 ≤ size，拼接无损、片内顺序稳定
    pieces = list(ar.chunked(items, size=10))
    assert all(len(p) <= 10 for p in pieces)
    assert [x for p in pieces for x in p] == items
    # 调用时读模块缺省（monkeypatch 可见）→ 分片归并路径可测
    monkeypatch.setattr(ar, "SQL_IN_CHUNK_SIZE", 2)
    assert all(len(p) <= 2 for p in ar.chunked(items))
    # 空/None 输入安全
    assert list(ar.chunked([])) == []
    assert list(ar.chunked([1], size=0)) == [[1]]


def test_referencing_counts_and_sha_counts_chunked_merge(db, monkeypatch):
    """PERF MAJOR-2：IN 列表分片循环 → 结果字典归并与单片查询完全一致。"""
    from app.services import artifact_revisions as ar
    from app.services.artifact_revisions import (
        referencing_counts,
        referencing_sha_counts,
        record_revision,
    )

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art1, art2 = f"art_{uuid.uuid4().hex[:8]}", f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art1)
    _mk_artifact(project_id, art2)
    loc_a = "aaaa/" + "a" * 64 + ".json"
    loc_b = "bbbb/" + "b" * 64 + ".json"
    sha_a, sha_b = "a" * 64, "b" * 64
    with SessionLocal() as s:
        record_revision(s, artifact_id=art1, content_sha256=sha_a,
                        content_location=loc_a)
        record_revision(s, artifact_id=art2, content_sha256=sha_a,
                        content_location=loc_a)  # 同 location 两行
        record_revision(s, artifact_id=art2, content_sha256=sha_b,
                        content_location=loc_b)
        s.commit()

    with SessionLocal() as s:
        unchunked_loc = referencing_counts(s, [loc_a, loc_b, "ghost/x.json"])
        unchunked_sha = referencing_sha_counts(s, [sha_a, sha_b, "f" * 64])
        monkeypatch.setattr(ar, "SQL_IN_CHUNK_SIZE", 1)  # 每片一条 → 强制多查询
        chunked_loc = referencing_counts(s, [loc_a, loc_b, "ghost/x.json"])
        chunked_sha = referencing_sha_counts(s, [sha_a, sha_b, "f" * 64])
    assert chunked_loc == unchunked_loc == {loc_a: 2, loc_b: 1}
    assert chunked_sha == unchunked_sha == {sha_a: 2, sha_b: 1}


def test_artifact_content_locations_sql_json_extraction(db):
    """PERF MAJOR-2：head 指针扫描 = SQL JSON 提取（非整表 metadata 物化
    后 Python 解析）—— 只返回有 content_location 的行，falsy 剔除，语义
    与旧 Python 实现一致。"""
    from app.models.project import Artifact
    from app.services.artifact_revisions import artifact_content_locations

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    loc = "cafe/" + "0" * 62 + ".json"
    with SessionLocal() as s:
        s.add(Artifact(id=f"art_{uuid.uuid4().hex[:8]}", project_id=project_id,
                       name="a", artifact_type="vector",
                       metadata_json={"content_location": loc}))
        s.add(Artifact(id=f"art_{uuid.uuid4().hex[:8]}", project_id=project_id,
                       name="b", artifact_type="vector",
                       metadata_json={"content_status": "promoted"}))  # 无指针
        s.add(Artifact(id=f"art_{uuid.uuid4().hex[:8]}", project_id=project_id,
                       name="c", artifact_type="vector",
                       metadata_json={"content_location": ""}))  # falsy 剔除
        s.add(Artifact(id=f"art_{uuid.uuid4().hex[:8]}", project_id=project_id,
                       name="d", artifact_type="vector", metadata_json=None))
        s.commit()
    with SessionLocal() as s:
        assert artifact_content_locations(s) == [loc]


def test_project_quota_usage_query_count_bounded(db):
    """PERF MAJOR-1：用量口径下推为 SQL 聚合 —— SELECT 条数与修订行数
    无关（有界），60 行修订不再物化为 60 个 Python 元组循环。"""
    from datetime import datetime, timezone

    from sqlalchemy import event

    from app.models.project import ArtifactRevision
    from app.services.artifact_revisions import project_quota_usage

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(project_id, art_id)
    with SessionLocal() as s:
        for i in range(60):
            s.add(ArtifactRevision(
                id=str(uuid.uuid4()), artifact_id=art_id, revision_no=i + 1,
                content_sha256=f"{i:064x}", content_location=f"{i:04x}/x.json",
                content_type="json", byte_size=1,
                created_at=datetime.now(timezone.utc),
            ))
        s.commit()

    statements: list = []

    def _count(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _count)
    try:
        with SessionLocal() as s:
            usage = project_quota_usage(s, project_id)
    finally:
        event.remove(Engine, "before_cursor_execute", _count)
    assert usage["revision_bytes"] == 60
    assert usage["max_per_artifact_revision_bytes"] == 60
    assert len(statements) <= 6, (
        f"project_quota_usage issued {len(statements)} SELECTs — "
        "accounting must stay bounded SQL aggregates, not O(N) row loads")
