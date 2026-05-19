"""A2 资源所有权：会话列表 / 取详情 / 删除应只暴露给 owner。

走 AsyncHistoryService 直接测，绕开 chat_engine 让测试聚焦在策略层。
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.db_model import Base
from app.services.history_service_async import AsyncHistoryService


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ownership_test.db'}"
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)
    yield fac
    await eng.dispose()


async def _seed_session(fac, session_id: str, owner: str | None) -> None:
    async with fac() as db:
        await AsyncHistoryService(db).get_or_create_conversation(session_id, user_id=owner)


@pytest.mark.asyncio
async def test_list_sessions_anonymous_returns_empty(session_factory):
    await _seed_session(session_factory, "sess-alice-1", "user-alice")
    async with session_factory() as db:
        result = await AsyncHistoryService(db).list_sessions(user_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_returns_only_own(session_factory):
    await _seed_session(session_factory, "s-alice-1", "user-alice")
    await _seed_session(session_factory, "s-alice-2", "user-alice")
    await _seed_session(session_factory, "s-bob-1", "user-bob")
    await _seed_session(session_factory, "s-anon-1", None)

    async with session_factory() as db:
        sessions = await AsyncHistoryService(db).list_sessions(user_id="user-alice")
    ids = {s.id for s in sessions}
    assert ids == {"s-alice-1", "s-alice-2"}


@pytest.mark.asyncio
async def test_get_session_blocks_cross_user(session_factory):
    await _seed_session(session_factory, "s-bob", "user-bob")
    async with session_factory() as db:
        # alice 试图读 bob 的会话
        conv = await AsyncHistoryService(db).get_session("s-bob", user_id="user-alice")
    assert conv is None


@pytest.mark.asyncio
async def test_get_session_allows_owner(session_factory):
    await _seed_session(session_factory, "s-bob", "user-bob")
    async with session_factory() as db:
        conv = await AsyncHistoryService(db).get_session("s-bob", user_id="user-bob")
    assert conv is not None
    assert conv.id == "s-bob"


@pytest.mark.asyncio
async def test_anon_session_remains_accessible_to_anyone(session_factory):
    """旧匿名记录靠 session_id 作能力令牌，谁知道 id 谁能读 — 保持兼容。"""
    await _seed_session(session_factory, "s-legacy", None)
    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        assert (await svc.get_session("s-legacy", user_id=None)) is not None
        assert (await svc.get_session("s-legacy", user_id="user-alice")) is not None


@pytest.mark.asyncio
async def test_delete_blocks_cross_user(session_factory):
    await _seed_session(session_factory, "s-bob", "user-bob")
    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        ok = await svc.delete_session("s-bob", user_id="user-alice")
    assert ok is False
    # bob 的会话仍在
    async with session_factory() as db:
        conv = await AsyncHistoryService(db).get_session("s-bob", user_id="user-bob")
    assert conv is not None


@pytest.mark.asyncio
async def test_delete_succeeds_for_owner(session_factory):
    await _seed_session(session_factory, "s-bob", "user-bob")
    async with session_factory() as db:
        ok = await AsyncHistoryService(db).delete_session("s-bob", user_id="user-bob")
    assert ok is True
    async with session_factory() as db:
        conv = await AsyncHistoryService(db).get_session("s-bob", user_id="user-bob")
    assert conv is None


@pytest.mark.asyncio
async def test_anonymous_string_is_treated_as_anonymous(session_factory):
    """auth.get_current_user_optional 在无 token 时返回 'anonymous'；service 应当识别。"""
    await _seed_session(session_factory, "s-alice", "user-alice")
    async with session_factory() as db:
        sessions = await AsyncHistoryService(db).list_sessions(user_id="anonymous")
    assert sessions == []
