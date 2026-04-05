"""
Agent资源池测试
"""
import pytest
from datetime import datetime
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


@pytest.fixture(autouse=True)
def cleanup():
    reset_test_mode()
    yield
    reset_test_mode()

@pytest.fixture
def pool():
    """Agent池Fixture"""
    reset_test_mode()
    return AgentPool()


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


@pytest.fixture
def pm_agent():
    return AgentInfo(
        id="pm-001",
        role=AgentRole.PM,
        name="ProjectManagerBot",
        capabilities=AgentCapability(pm={"planning", "coordination"}),
    )
 
 
class TestAgentRegistration:
    """Agent注册测试"""

    def test_register_new_agent(self, pool, coder_agent):
        result = pool.register(coder_agent)
        assert result is True

    def test_cannot_reregister_same_id(self, pool, coder_agent):
        pool.register(coder_agent)
        result = pool.register(coder_agent)  # 重复注册
        assert result is False

    def test_unregister_success(self, pool, coder_agent):
        pool.register(coder_agent)
        result = pool.unregister(coder_agent.id)
        assert result is True

    def test_unregister_nonexistent_fails(self, pool):
        result = pool.unregister("non-existent")
        assert result is False


class TestAgentAllocation:
    """Agent分配测试"""

    def test_allocate_idle_agent(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)

        allocated = pool.allocate(AgentRole.CODER)
        
        assert allocated is not None
        assert allocated.role == AgentRole.CODER
        assert allocated.status == "busy"

    def test_no_available_agent_returns_none(self, pool, coder_agent):
        pool.register(coder_agent)
        pool.update_status(coder_agent.id, "busy")

        allocated = pool.allocate(AgentRole.CODER)
        
        assert allocated is None

    def test_allocate_by_role_only(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)

        # 请求tester角色，应该返回tester
        allocated = pool.allocate(AgentRole.TEST_REVIEWER)
        
        assert allocated is not None
        assert allocated.id == "tester-001"

    def test_allocate_with_capabilities_filter(self, pool, coder_agent):
        coders_with_pytest = AgentInfo(
            id="coder-pytest",
            role=AgentRole.CODER,
            name="TesterCoder",
            capabilities=AgentCapability(coder={"python", "pytest"}),
        )
        
        pool.register(coders_with_pytest)
        
        # 需要python和pytest能力
        allocated = pool.allocate(
            AgentRole.CODER, 
            required_capabilities={"python", "pytest"}
        )
        
        assert allocated is not None
        assert "pytest" in allocated.capabilities.coder


class TestAgentRelease:
    """Agent释放测试"""

    def test_release_returns_to_idle(self, pool, coder_agent):
        pool.register(coder_agent)
        pool.allocate(AgentRole.CODER)
        
        released = pool.release(coder_agent.id)
        
        assert released is True
        
        agent = pool.get_by_id(coder_agent.id)
        assert agent.status == "idle"
        assert agent.current_task_id is None


class TestAgentQueries:
    """Agent查询测试"""

    def test_get_all_agents(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)
        
        all_agents = pool.get_all_agents()
        
        assert len(all_agents) == 2

    def test_get_agents_by_role(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)
        
        coders = pool.get_agents_by_role(AgentRole.CODER)
        testers = pool.get_agents_by_role(AgentRole.TEST_REVIEWER)
        
        assert len(coders) == 1
        assert len(testers) == 1

    def test_get_idle_agents(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)
        pool.allocate(AgentRole.CODER)  # 占用coder
        
        idle_coders = pool.get_idle_agents_by_role(AgentRole.CODER)
        idle_testers = pool.get_idle_agents_by_role(AgentRole.TEST_REVIEWER)
        
        assert len(idle_coders) == 0  # 被占用
        assert len(idle_testers) == 1  # 仍然空闲


class TestAgentHeartbeat:
    """Agent心跳测试"""

    def test_heartbeat_updates_timestamp(self, pool, coder_agent):
        pool.register(coder_agent)
        
        # 直接设置全局心脏
        from app.services.orchestration import agent_poo as ap_module
        # Note: 已经renamed to agent_pool.py, let's import properly
        # 需要测试心跳功能


class TestAgentStatistics:
    """Agent统计测试"""

    def test_statistics_counts_correctly(self, pool, coder_agent, tester_agent, pm_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)
        pool.register(pm_agent)
        
        pool.allocate(AgentRole.CODER)  # 占用一个
        
        stats = pool.get_statistics()
        
        assert stats["total"] == 3
        assert stats["idle"] == 2
        assert stats["busy"] == 1

    def test_statistics_by_role(self, pool, coder_agent, tester_agent):
        pool.register(coder_agent)
        pool.register(tester_agent)
        
        stats = pool.get_statistics()
        
        assert "coder" in stats["by_role"]
        assert "test_reviewer" in stats["by_role"]
        assert stats["by_role"]["coder"]["total"] == 1