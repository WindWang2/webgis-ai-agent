"""RAG service DB-backed regression tests (#484 tenant-scoped total, #485 delete ordering).

Uses a throwaway sqlite+aiosqlite DB patched into
``app.core.database.AsyncSessionLocal`` — the global that
``app.tools._utils.async_db_session`` reads — so the real service code path
runs against a real (file-backed) database without Postgres.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
