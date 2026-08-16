"""RAG service DB-backed regression tests (#484 tenant-scoped total, #485 delete ordering).

Uses a throwaway sqlite+aiosqlite DB patched into
``app.core.database.AsyncSessionLocal`` — the global that
``app.tools._utils.async_db_session`` reads — so the real service code path
runs against a real (file-backed) database without Postgres.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest_asyncio


@pytest_asyncio.fixture
async def rag_db(tmp_path, monkeypatch):
    """sqlite test DB wired into the app's async-session global."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database
    from app.models.db_model import Base
    from app.tools import _utils

    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}",
        connect_args={"check_same_thread": False},
    )
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
            finally:
                await s.close()

    # _utils.async_db_session reads app.core.database.AsyncSessionLocal at
    # call time, so patching the module attribute is enough.
    monkeypatch.setattr(database, "AsyncSessionLocal", test_session)
    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)

    yield

    await test_engine.dispose()


async def _seed_document(db_sessionmaker, *, org_id=None, creator_id=None, title="doc"):
    from app.models.knowledge_base import Chunk, Document

    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    async with db_sessionmaker() as s:
        d = Document(
            id=doc_id,
            title=title,
            file_type="text",
            chunk_count=1,
            status="completed",
            creator_id=creator_id,
            org_id=org_id,
            created_at=datetime.now(timezone.utc),
        )
        s.add(d)
        s.add(Chunk(id=str(uuid.uuid4()), document_id=doc_id, content="c", chunk_index=0))
        await s.commit()
    return doc_id


def _engine_stub(delete_result=True):
    """Minimal KnowledgeEngine stand-in whose delete_document is a mock."""
    stub = type("EngineStub", (), {})()
    stub.delete_document = AsyncMock(return_value=delete_result)
    return stub


# ── #485: delete ordering / vector failure compensation ──────────────────


async def test_delete_document_vector_failure_keeps_rows_and_retry_succeeds(rag_db, tmp_path):
    """#485: 向量清理失败时 DB 行必须仍在（可重试），重试后两端都删净。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.knowledge_base import Chunk, Document
    from app.services import rag_service
    from app.services.rag import engine as engine_mod

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    doc_id = await _seed_document(session, creator_id="alice")

    calls = {"n": 0}

    async def flaky_engine_delete(doc_id_arg, tenant=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("vector store metadata I/O failed")
        return True

    stub = type("E", (), {})()
    stub.delete_document = flaky_engine_delete
    with patch.object(engine_mod, "get_knowledge_engine", return_value=stub):
        # First attempt: vector cleanup fails -> delete must NOT report
        # success and DB rows must survive for a retry.
        ok = await rag_service.delete_document(doc_id, user_id="alice")
        assert ok is False, "vector failure must not report success"

        async with session() as s:
            doc_row = (await s.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
            chunk_rows = (await s.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars().all()
        assert doc_row is not None, "DB document row must survive vector-cleanup failure"
        assert len(chunk_rows) == 1, "DB chunk rows must survive vector-cleanup failure"

        # Retry after the transient vector failure succeeds and clears both stores.
        ok2 = await rag_service.delete_document(doc_id, user_id="alice")
        assert ok2 is True
        async with session() as s:
            doc_row2 = (await s.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
            chunk_rows2 = (await s.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars().all()
        assert doc_row2 is None
        assert chunk_rows2 == []
    assert calls["n"] == 2, "retry must reach vector cleanup again"


async def test_delete_document_removes_vectors_before_db_rows(rag_db, tmp_path):
    """#485: 排序契约 —— engine.delete_document 运行时 DB 行必须还在。

    若先删 DB 行，向量清理一旦失败 owner_check 将永远查不到行，重试不可能。
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.knowledge_base import Document
    from app.services import rag_service
    from app.services.rag import engine as engine_mod

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    doc_id = await _seed_document(session, creator_id="alice")

    seen_row_at_vector_time = {}

    async def probe_engine_delete(doc_id_arg, tenant=None):
        async with session() as s:
            row = (await s.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
        seen_row_at_vector_time["row_present"] = row is not None
        return True

    stub = type("E", (), {})()
    stub.delete_document = probe_engine_delete
    with patch.object(engine_mod, "get_knowledge_engine", return_value=stub):
        ok = await rag_service.delete_document(doc_id, user_id="alice")

    assert ok is True
    assert seen_row_at_vector_time.get("row_present") is True, (
        "vector cleanup ran after the DB rows were already deleted — a vector "
        "failure there would be unretryable (#485)"
    )


async def test_delete_document_success_clears_both_stores(rag_db, tmp_path):
    """Happy path: both the vector engine and the DB rows are cleaned."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.knowledge_base import Document
    from app.services import rag_service
    from app.services.rag import engine as engine_mod

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    doc_id = await _seed_document(session, creator_id="alice")

    stub = _engine_stub(delete_result=True)
    with patch.object(engine_mod, "get_knowledge_engine", return_value=stub):
        ok = await rag_service.delete_document(doc_id, user_id="alice")

    assert ok is True
    assert stub.delete_document.await_count == 1
    async with session() as s:
        assert (await s.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none() is None


async def test_delete_document_denied_for_non_owner(rag_db, tmp_path):
    """Owner check still holds: a different user's delete must not touch the engine."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services import rag_service
    from app.services.rag import engine as engine_mod

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    doc_id = await _seed_document(session, creator_id="alice")

    stub = _engine_stub(delete_result=True)
    with patch.object(engine_mod, "get_knowledge_engine", return_value=stub):
        ok = await rag_service.delete_document(doc_id, user_id="mallory")

    assert ok is False
    stub.delete_document.assert_not_awaited()


# ── #484: tenant-scoped total ────────────────────────────────────────────


async def test_list_documents_total_is_org_scoped(rag_db, tmp_path):
    """#484: org A (2 docs) and org B (3 docs) must each see only their own total."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services import rag_service

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    for _ in range(2):
        await _seed_document(session, org_id=1)
    for _ in range(3):
        await _seed_document(session, org_id=2)

    result_a = await rag_service.list_documents(org_id=1)
    result_b = await rag_service.list_documents(org_id=2)

    assert result_a["total"] == 2, "org A must see exactly its own document count"
    assert result_b["total"] == 3, "org B must see exactly its own document count"
    assert {i["title"] for i in result_a["items"]} <= {"doc"}


async def test_list_documents_total_is_user_scoped(rag_db, tmp_path):
    """#484: with no org context, total must be scoped to the creator."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services import rag_service

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    for _ in range(2):
        await _seed_document(session, creator_id="alice")
    await _seed_document(session, creator_id="bob")

    assert (await rag_service.list_documents(user_id="alice"))["total"] == 2
    assert (await rag_service.list_documents(user_id="bob"))["total"] == 1


async def test_list_documents_pagination_math(rag_db, tmp_path):
    """#484: total must reflect the filtered extent, not the page size or the table."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services import rag_service

    session = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag_service.db'}"),
        expire_on_commit=False,
    )
    for _ in range(3):
        await _seed_document(session, org_id=7)
    for _ in range(4):
        await _seed_document(session, org_id=8)  # other tenant noise

    page1 = await rag_service.list_documents(limit=2, offset=0, org_id=7)
    page2 = await rag_service.list_documents(limit=2, offset=2, org_id=7)

    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page2["total"] == 3
    assert len(page2["items"]) == 1


# ── #545: add_document 的 add 侧补偿（#485 的镜像）────────────────────────


async def test_add_document_db_failure_compensates_orphan_vectors(rag_db, monkeypatch):
    """#545 单元：index_document 成功后 DB 写失败 → 必须调用
    engine.delete_document（幂等软删）补偿已提交的向量，并返回 error。"""
    from app.services import rag_service
    from app.services.rag import engine as engine_mod

    indexed = {
        "document_id": "doc_deadbeef",
        "chunks": [{"content": "orphan content", "chunk_index": 0,
                    "start_char": 0, "end_char": 5}],
        "status": "indexed",
    }
    deleted: list[str] = []

    class FakeEngine:
        async def index_document(self, **kwargs):
            return dict(indexed)

        async def delete_document(self, doc_id, tenant=None):
            deleted.append(doc_id)
            return True

    # DB 写入必然失败（#545 的复现方式：async_db_session 抛异常）
    import app.tools._utils as utils_mod

    @asynccontextmanager
    async def boom():
        raise RuntimeError("simulated DB outage")
        yield  # pragma: no cover — async gen 形态（保证 __aenter__ 才抛）

    monkeypatch.setattr(utils_mod, "async_db_session", boom)

    with patch.object(engine_mod, "get_knowledge_engine", return_value=FakeEngine()):
        result = await rag_service.add_document(title="t", content="orphan content")

    assert "error" in result, "DB 失败必须上报 error，不得假装成功"
    assert deleted == ["doc_deadbeef"], (
        f"补偿未触发：engine.delete_document 应被调用，实际 {deleted}"
    )


async def test_add_document_db_failure_leaves_no_searchable_orphan(rag_db, tmp_path, monkeypatch):
    """#545 集成（真实 FaissVectorStore + 补丁 embed）：DB 失败后向量被软删，
    语义搜索不再返回孤儿内容；重试成功入库，索引/元数据无双重拷贝。"""
    from contextlib import asynccontextmanager

    import numpy as np

    from app.services import rag_service
    from app.services.rag import engine as engine_mod
    from app.services.rag.engine import KnowledgeEngine
    from app.services.rag.faiss_store import FaissVectorStore
    from app.tools import _utils

    store = FaissVectorStore(index_dir=str(tmp_path / "vectors"))

    def fake_embed(self, texts):
        rng = np.random.default_rng(seed=7)
        # 与 _get_index 默认 dim=384 保持一致（空索引/查询向量同维）
        return np.array(rng.random((len(texts), 384)), dtype=np.float32)

    monkeypatch.setattr(FaissVectorStore, "embed_texts", fake_embed)
    real_engine = KnowledgeEngine(vector_store=store)

    # rag_db fixture 已把 _utils.async_db_session 换成真实 sqlite；包一层：
    # 第一次调用抛异常，后续放行。
    original_session = _utils.async_db_session
    fails = {"n": 0}

    @asynccontextmanager
    async def flaky_session():
        fails["n"] += 1
        if fails["n"] == 1:
            raise RuntimeError("simulated DB outage")
        async with original_session() as s:
            yield s

    monkeypatch.setattr(_utils, "async_db_session", flaky_session)

    with patch.object(engine_mod, "get_knowledge_engine", return_value=real_engine):
        # 第一次 add：向量已提交，DB 失败 → 补偿软删
        result = await rag_service.add_document(title="t1", content="orphan content")
        assert "error" in result

        # 孤儿不得再可检索
        found = await rag_service.semantic_search(query="orphan content", top_k=5)
        assert found == [], f"孤儿向量仍可检索: {found}"

        # 第二次 add：DB 恢复 → 成功；向量库 chunk 数与 DB 上报一致（无双重拷贝）
        ok = await rag_service.add_document(title="t2", content="real content")
        assert "error" not in ok

        meta = store.load_metadata()
        assert len(meta["chunks"]) == ok["chunk_count"], (
            f"重试后向量库 chunk 数（{len(meta['chunks'])}）与 DB 上报（{ok['chunk_count']}）"
            "不一致 —— 孤儿残留或双重拷贝"
        )
        assert all(c["document_id"] == ok["document_id"] for c in meta["chunks"]), (
            f"向量库出现非本次文档的 chunk（孤儿未清干净）: "
            f"{[c['content'] for c in meta['chunks']]}"
        )
