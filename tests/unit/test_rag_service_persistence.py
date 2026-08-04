"""
rag_service 元数据持久化契约测试 (REVIEW-P0-1)。

ADR-0038 把 add_document 的实现委托给 KnowledgeEngine，但 KnowledgeEngine 只写
FAISS —— 提取过程把原来的 Document/Chunk SQL 落库整段丢掉了，而 list_documents
仍然读 SQL。结果：上传成功但文档永远不出现在列表里；同时路由读
result["chunk_count"]，引擎却返回 "chunks_count"，直接 KeyError 500。
delete_document 也丢掉了 S41 所有权校验，变成任何人都能删任意 doc_id。

本文件锁定这三条契约。此前没有任何测试覆盖 add -> list 这条往返路径，
所以整个回归在 CI 全绿的情况下发船。
"""
import uuid
from contextlib import asynccontextmanager

import pytest

# ── Fake 向量库：避免加载 SentenceTransformer / FAISS ──────────────────

class FakeVectorStore:
    """满足 VectorStoreProtocol 的最小实现，只记录调用。"""

    def __init__(self):
        self.added: list[dict] = []
        self.deleted: list[str] = []

    def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]

    def add_vectors(self, vectors, chunks_metadata):
        self.added.extend(chunks_metadata)

    def search(self, query_vector, top_k=5, user_id=None, org_id=None, is_admin=False):
        return []

    def mark_deleted(self, doc_id):
        self.deleted.append(doc_id)

    def compact(self):
        return {}

    def get_stats(self):
        return {"total_vectors": len(self.added), "deleted_count": len(self.deleted)}


@pytest.fixture
def rag_env(tmp_path, monkeypatch):
    """临时 SQLite + 注入 FakeVectorStore 的 KnowledgeEngine。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models.knowledge_base  # noqa: F401 — 注册 Document/Chunk 表
    from app.services.rag.engine import KnowledgeEngine, set_active_knowledge_engine
    from app.tools import _utils

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'rag.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

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

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)

    store = FakeVectorStore()
    set_active_knowledge_engine(KnowledgeEngine(vector_store=store))

    yield test_engine, test_session, store

    set_active_knowledge_engine(None)


@pytest.fixture
async def initialized_db(rag_env):
    from app.models.db_model import Base

    test_engine, test_session, store = rag_env
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield test_session, store
    await test_engine.dispose()


# ── add -> list 往返 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_document_then_list_returns_it(initialized_db):
    """核心回归：上传的文档必须出现在 list_documents 里。

    提取后 add_document 只写 FAISS，本断言会拿到 total == 0。
    """
    from app.services import rag_service

    _, store = initialized_db

    result = await rag_service.add_document(
        title="北京医院数据",
        content="第一段内容。\n\n第二段内容。\n\n第三段内容。",
        file_type="text",
        user_id="alice",
    )

    assert "error" not in result, result
    listing = await rag_service.list_documents(user_id="alice")

    assert listing["total"] == 1
    assert listing["items"][0]["id"] == result["document_id"]
    assert listing["items"][0]["title"] == "北京医院数据"
    # 向量侧也确实写了，两侧不能只成功一边
    assert len(store.added) == result["chunk_count"]


@pytest.mark.asyncio
async def test_add_document_returns_chunk_count_key(initialized_db):
    """路由读的是 result["chunk_count"]；引擎返回的是 "chunks_count"。

    适配层必须归一化，否则 POST /knowledge/documents 每次都 KeyError → 500。
    """
    from app.services import rag_service

    result = await rag_service.add_document(
        title="key 契约", content="一些内容", user_id="alice"
    )

    assert "chunk_count" in result
    assert isinstance(result["chunk_count"], int)
    assert result["chunk_count"] > 0
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_persisted_chunk_rows_match_chunk_count(initialized_db):
    """Chunk 行数必须与上报的 chunk_count 一致，且 chunk_index 连续。"""
    from sqlalchemy import select

    from app.models.knowledge_base import Chunk
    from app.services import rag_service

    test_session, _ = initialized_db

    result = await rag_service.add_document(
        title="分块落库",
        content="段落一。\n\n段落二。\n\n段落三。\n\n段落四。",
        file_type="markdown",
        user_id="alice",
    )

    async with test_session() as db:
        rows = (
            await db.execute(
                select(Chunk).where(Chunk.document_id == result["document_id"])
            )
        ).scalars().all()

    assert len(rows) == result["chunk_count"]
    assert sorted(r.chunk_index for r in rows) == list(range(len(rows)))
    assert all(r.content for r in rows)


# ── 租户隔离 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_documents_isolates_tenants(initialized_db):
    """Alice 的文档不能出现在 Bob 的列表里。"""
    from app.services import rag_service

    await rag_service.add_document(title="alice 的文档", content="秘密", user_id="alice")

    bob_listing = await rag_service.list_documents(user_id="bob")
    assert all(item["title"] != "alice 的文档" for item in bob_listing["items"])


@pytest.mark.asyncio
async def test_delete_document_rejects_other_tenant(initialized_db):
    """S41：Bob 不能删 Alice 的文档，且不得触达向量库。"""
    from app.services import rag_service

    _, store = initialized_db

    created = await rag_service.add_document(
        title="alice 的文档", content="内容", user_id="alice"
    )
    doc_id = created["document_id"]

    ok = await rag_service.delete_document(doc_id, user_id="bob")

    assert ok is False
    assert doc_id not in store.deleted, "越权删除不应触达向量库"
    # 文档对所有者依然可见
    listing = await rag_service.list_documents(user_id="alice")
    assert listing["total"] == 1


@pytest.mark.asyncio
async def test_delete_document_allows_owner(initialized_db):
    """正向：所有者能删自己的文档，SQL 行与向量都被清理。"""
    from app.services import rag_service

    _, store = initialized_db

    created = await rag_service.add_document(
        title="alice 的文档", content="内容", user_id="alice"
    )
    doc_id = created["document_id"]

    ok = await rag_service.delete_document(doc_id, user_id="alice")

    assert ok is True
    assert doc_id in store.deleted
    listing = await rag_service.list_documents(user_id="alice")
    assert listing["total"] == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_document_returns_false(initialized_db):
    """不存在的 doc_id 必须返回 False（路由据此回 "删除失败或无权访问"），
    而不是无脑 True。"""
    from app.services import rag_service

    ok = await rag_service.delete_document(f"doc_{uuid.uuid4().hex[:12]}", user_id="alice")
    assert ok is False
