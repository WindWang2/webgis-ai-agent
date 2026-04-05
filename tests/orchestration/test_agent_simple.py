"""简单Agent资源池测试"""
import pytest
from app.services.orchestration.models import (
    AgentInfo,
    AgentRole,
    AgentCapability,
)
from app.services.orchestration.agent_pool import (
    AgentPool,
    reset_test_mode,
)


@pytest.fixture
def pool():
    reset_test_mode()
    yield AgentPool()
    reset_test_mode()


def test_register(pool):
    reset_test_mode()
    agent = AgentInfo(id="a1", role=AgentRole.CODER, name="Bot")
    result = pool.register(agent)
    assert result is True


def test_allocate(pool):
    reset_test_mode()
    agent = AgentInfo(id="a1", role=AgentRole.CODER, name="Bot")
    pool.register(agent)
    alloc = pool.allocate(AgentRole.CODER)
    assert alloc is not None


def test_statistics(pool):
    reset_test_mode()
    pool.register(AgentInfo(id="a1", role=AgentRole.CODER, name="Bot"))
    pool.register(AgentInfo(id="a2", role=AgentRole.TEST_REVIEWER, name="QA"))
    stats = pool.get_statistics()
    assert stats["total"] == 2