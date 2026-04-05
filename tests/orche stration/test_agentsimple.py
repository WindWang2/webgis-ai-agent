"""Agent资源池测试"""
import pytest
from app.services.orchestration.models import (
    AgentInfo,
    AgentRole,
    AgentCapability,
)
from app.services.orchestration.agent_pool import (
    AgentPool,
    register_test_agent,
    reset_test_mode,
)


@pytest.fixture
def pool():
    reset_test_mode()
    p = AgentPool()
    yield p
    reset_test_mode()


@pytest.fixture
def coder_agent():
    return AgentInfo(
        id="coder-001",
        role=AgentRole.CODER,
        name="PythonDevBot",
        capabilities=AgentCapability(coder={"python", "fastapi"}),
    )


@pytest.fixture
def tester_agent():
    return AgentInfo(
        id="tester-001",
        role=AgentRole.TEST_REVIEWER,
        name="QABot",
        capabilities=AgentCapability(test_reviewer={"pytest", "selenium"}),
    )


class TestAgentRegistration:
    def test_register_new_agent(self, pool, coder_agent):
        result = pool.register(coder_agent)
        assert result is True

    def test_cannot_reregister_same_id(self, pool, coder_agent):
        reset_test_mode()
        pool.register(coder_agent)
        result = pool.register(coder_agent)
        assert result is False


class TestAgentAllocation:
    def test_allocate_idle_agent(self, pool, coder_agent, tester_agent):
        reset_test_mode()
        pool.register(coder_agent)
        pool.register(tester_agent)
        allocated = pool.allocate(AgentRole.CODER)
        assert allocated is not None
        assert allocated.role == AgentRole.CODER

    def test_no_available_returns_none(self, pool, coder_agent):
        reset_test_mode()
        pool.register(coder_agent)
        pool.update_status(coder_agent.id, "busy")
        allocated = pool.allocate(AgentRole.CODER)
        assert allocated is None


class TestAgentRelease:
    def test_release_returns_to_idle(self, pool, coder_agent):
        reset_test_mode()
        pool.register(coder_agent)
        pool.allocate(AgentRole.CODER)
        released = pool.release(coder_agent.id)
        assert released is True
        agent = pool.get_by_id(coder_agent.id)
        assert agent.status == "idle"


class TestAgentQueries:
    def test_get_all_agents(self, pool, coder_agent, tester_agent):
        reset_test_mode()
        pool.register(coder_agent)
        pool.register(tester_agent)
        all_agents = pool.get_all_agents()
        assert len(all_agents) == 2

    def test_get_statistics_correct_counts(self, pool, coder_agent, tester_agent):
        reset_test_mode()
        pool.register(coder_agent)
        pool.register(tester_agent)
        stats = pool.get_statistics()
        assert stats["total"] == 2
        assert stats["idle"] == 2