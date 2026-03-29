"""
Agent资源池完整测试
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
    reset_test_mode,
)


@pytest.fixture
def pool():
    """专用测试套件 - 每个测试都独立"""
    reset_test_mode()
    yield AgentPool()


def test_register_single(pool):
    """注册单一Agent"""
    agent = AgentInfo(
        id="coder-1",
        role=AgentRole.CODER,
        name="DevBot"
    )
    result = pool.register(agent)
    assert result is True
    assert len(pool.get_all_agents()) == 1


def test_allocate_after_register(pool):
    """注册后可分配"""
    agent = AgentInfo(
        id="coder-1",
        role=AgentRole.CODER,
        name="DevBot"
    )
    pool.register(agent)
    
    allocated = pool.allocate(AgentRole.CODER)
    assert allocated is not None
    assert allocated.id == "coder-1"


def test_release_returns_idle(pool):
    """释放后回到空闲"""
    agent = AgentInfo(id="t", role=AgentRole.PM, name="P")
    pool.register(agent)
    pool.allocate(AgentRole.PM)
    
    pool.release("t")
    
    idle_pm = pool.get_idle_agents_by_role(AgentRole.PM)
    assert len(idle_pm) == 1


def test_statistics_empty(pool):
    """空池统计"""
    s = pool.get_statistics()
    assert s["total"] == 0
    assert s["idle"] == 0


def test_cannot_double_register(pool):
    """重复注册失败"""
    agent = AgentInfo(id="dup", role=AgentRole.CODER, name="D")
    pool.register(agent)
    result = pool.register(agent)
    assert result is False