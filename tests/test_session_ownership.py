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


async def _seed_session(fac, session_id: str, owner: str | None) -> str | None:
    """Seed a session. Returns the owner_token for new anonymous sessions (SEC-08)."""
    async with fac() as db:
        conv = await AsyncHistoryService(db).get_or_create_conversation(session_id, user_id=owner)
        return conv.owner_token


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
async def test_grandfather_anon_session_without_token_remains_accessible(session_factory):
    """SEC-08 grandfather：owner_token IS NULL 的旧匿名记录仍按能力令牌语义放行。"""
    from app.models.db_model import Conversation
    async with session_factory() as db:
        db.add(Conversation(id="s-legacy", user_id=None, owner_token=None, title="legacy"))
        await db.commit()
    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        # 无 token 也能访问（向后兼容）
        assert (await svc.get_session("s-legacy", user_id=None)) is not None
        assert (await svc.get_session("s-legacy", user_id="user-alice")) is not None


@pytest.mark.asyncio
async def test_new_anon_session_requires_owner_token(session_factory):
    """SEC-08：新建匿名会话签发 owner_token；无 token / 错 token 拒绝（返回 None）。"""
    token = await _seed_session(session_factory, "s-anon-new", None)
    assert token is not None and len(token) >= 32

    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        # 无 token -> 拒绝
        assert (await svc.get_session("s-anon-new", user_id=None)) is None
        # 错 token -> 拒绝
        assert (await svc.get_session("s-anon-new", user_id=None, owner_token="wrong")) is None
        # 正确 token -> 放行
        conv = await svc.get_session("s-anon-new", user_id=None, owner_token=token)
        assert conv is not None and conv.id == "s-anon-new"


@pytest.mark.asyncio
async def test_new_anon_session_delete_requires_owner_token(session_factory):
    """SEC-08：删除匿名会话同样受 owner_token 保护。"""
    token = await _seed_session(session_factory, "s-anon-del", None)
    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        # 无 token 删除失败
        assert (await svc.delete_session("s-anon-del", user_id=None)) is False
    async with session_factory() as db:
        svc = AsyncHistoryService(db)
        # 正确 token 删除成功
        assert (await svc.delete_session("s-anon-del", user_id=None, owner_token=token)) is True


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
