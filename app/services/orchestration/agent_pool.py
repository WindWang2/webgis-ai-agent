"""
Agent资源池管理 - Coder/TestReviewer/PM 多角色Agent资源分配
"""
from typing import Optional, List, Dict, Any, Set
import threading
from datetime import datetime
from app.services.orchestration.models import (
    AgentInfo,
    AgentRole,
    AgentCapability,
)

# Agent健康心跳间隔（秒）
AGENT_HEARTBEAT_INTERVAL = 60

# Agent失联阈值（秒）
AGENT_OFFLINE_THRESHOLD = 180

# 全局Agent注册中心
_agent_registry_lock = threading.RLock()
_global_agent_pool: Dict[str, AgentInfo] = {}
_agent_last_heartbeat: Dict[str, datetime] = {}

# 用于单元测试的mock registry
_test_mode = False
_mock_agents: Dict[str, AgentInfo] = {}


def register_test_agent(agent: AgentInfo):
    """注册测试用Agent（仅用于单元测试）"""
    global _test_mode, _mock_agents
    _test_mode = True
    _mock_agents[agent.id] = agent


def reset_test_mode():
    """重置测试模式"""
    global _test_mode, _mock_agents
    _test_mode = False
    _mock_agents.clear()


class AgentPool:
    """
    Agent资源池，管理多角色Agent的注册、分配、释放、心跳检测
    """

    def __init__(self):
        self._lock = threading.RLock()

    def register(self, agent: AgentInfo) -> bool:
        """
        注册Agent
        
        Args:
            agent: Agent信息
            
        Returns:
            是否成功
        """
        global _test_mode, _mock_agents
        if _test_mode:
            _mock_agents[agent.id] = agent
            return True
            
        with self._lock:
            if agent.id in _global_agent_pool:
                return False
            _global_agent_pool[agent.id] = agent
            _agent_last_heartbeat[agent.id] = datetime.now()
            return True

    def unregister(self, agent_id: str) -> bool:
        """注销Agent"""
        global _test_mode, _mock_agents
        if _test_mode:
            if agent_id in _mock_agents:
                del _mock_agents[agent_id]
            return True
                
        with self._lock:
            if agent_id in _global_agent_pool:
                del _global_agent_pool[agent_id]
                _agent_last_heartbeat.pop(agent_id, None)
                return True
            return False

    def allocate(
        self,
        role: AgentRole,
        required_capabilities: Optional[Set[str]] = None,
    ) -> Optional[AgentInfo]:
        """
        分配可用Agent
        
        Args:
            role: 需要的角色
            required_capabilities: 需要的能力（可选）
            
        Returns:
            分配的Agent信息，无则返回None
        """
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool

        with self._lock:
            candidates = []
            for agent in pool.values():
                if agent.role != role:
                    continue
                if agent.status != "idle":
                    continue
                    
                # 如果指定了能力要求，检查是否满足
                if required_capabilities:
                    agent_caps = getattr(agent.capabilities, role.value.lower(), set())
                    if not required_capabilities.issubset(set(agent_caps)):
                        continue
                        
                candidates.append(agent)

            if not candidates:
                return None
                
            # 取第一个可用候选者（可优化为负载最低策略）
            selected = candidates[0]
            selected.status = "busy"
            return selected

    def release(self, agent_id: str, task_completed: bool = True) -> bool:
        """
        释放Agent回归空闲池
        
        Args:
            agent_id: Agent ID
            task_completed: 任务是否成功完成
            
        Returns:
            是否成功
        """
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool

        with self._lock:
            agent = pool.get(agent_id)
            if not agent:
                return False
            
            agent.status = "idle"
            agent.current_task_id = None
            return True

    def get_by_id(self, agent_id: str) -> Optional[AgentInfo]:
        """根据ID获取Agent"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        return pool.get(agent_id)

    def get_all_agents(self) -> List[AgentInfo]:
        """获取所有已注册的Agent"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        return list(pool.values())

    def get_agents_by_role(self, role: AgentRole) -> List[AgentInfo]:
        """获取指定角色的所有Agent"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        return [a for a in pool.values() if a.role == role]

    def get_idle_agents_by_role(self, role: AgentRole) -> List[AgentInfo]:
        """获取指定角色的空闲Agent"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        return [a for a in pool.values() if a.role == role and a.status == "idle"]

    def update_status(self, agent_id: str, status: str) -> bool:
        """更新Agent状态"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        
        with self._lock:
            agent = pool.get(agent_id)
            if agent:
                agent.status = status
                return True
            return False

    def heartbeat(self, agent_id: str) -> bool:
        """Agent心跳保活"""
        global _test_mode, _mock_agents
        if _test_mode:
            return True
            
        with self._lock:
            if agent_id in _global_agent_pool:
                _agent_last_heartbeat[agent_id] = datetime.now()
                return True
            return False

    def check_health(self) -> List[str]:
        """检查不活跃Agent，返回其ID列表"""
        global _test_mode
        if _test_mode:
            return []

        now = datetime.now()
        offline_agents = []
        
        with self._lock:
            for agent_id, last_seen in _agent_last_heartbeat.items():
                if (now - last_seen).total_seconds() > AGENT_OFFLINE_THRESHOLD:
                    offline_agents.append(agent_id)
                    
        return offline_agents

    def get_statistics(self) -> Dict[str, Any]:
        """获取Agent池统计"""
        global _test_mode, _mock_agents
        pool = _mock_agents if _test_mode else _global_agent_pool
        
        stats = {
            "total": len(pool),
            "idle": 0,
            "busy": 0,
            "offline": 0,
            "by_role": {},
        }
        
        for agent in pool.values():
            if agent.status == "idle":
                stats["idle"] += 1
            elif agent.status == "busy":
                stats["busy"] += 1
            elif agent.status == "offline":
                stats["offline"] += 1
                
            # Handle role that Pydantic converts to string
            if isinstance(agent.role, str):
                role_key = agent.role
            else:
                role_key = str(agent.role)
            if role_key not in stats["by_role"]:
                stats["by_role"][role_key] = {"total": 0, "idle": 0}
            stats["by_role"][role_key]["total"] += 1
            if agent.status == "idle":
                stats["by_role"][role_key]["idle"] += 1
                
        return stats


# 全局Agent池单例
agent_pool_instance = AgentPool()