from typing import Dict, Any
from .models import SubTask, AgentInfo, TaskType

class TaskRouter:
    def __init__(self):
        # Agent路由配置
        self.agent_routes = {
            TaskType.DATA_QUERY: AgentInfo(
                agent_type="gis_data_agent",
                endpoint="http://localhost:8001/api/v1/data/query",
                timeout=30
            ),
            TaskType.SPATIAL_ANALYSIS: AgentInfo(
                agent_type="spatial_analysis_agent",
                endpoint="http://localhost:8002/api/v1/analysis/execute",
                timeout=60
            ),
            TaskType.VISUALIZATION: AgentInfo(
                agent_type="visualization_agent",
                endpoint="http://localhost:8003/api/v1/visualize/generate",
                timeout=30
            ),
            TaskType.GENERAL_QA: AgentInfo(
                agent_type="general_qa_agent",
                endpoint="http://localhost:8004/api/v1/qa/answer",
                timeout=20
            )
        }
        
        # 备用Agent配置
        self.fallback_agents = {
            TaskType.DATA_QUERY: "general_qa_agent",
            TaskType.SPATIAL_ANALYSIS: "general_qa_agent",
            TaskType.VISUALIZATION: "general_qa_agent"
        }

    def route(self, subtask: SubTask) -> Dict[str, Any]:
        """根据子任务类型路由到对应Agent"""
        task_type = subtask.type
        
        if task_type in self.agent_routes:
            agent_info = self.agent_routes[task_type]
            return {
                "agent_type": agent_info.agent_type,
                "endpoint": agent_info.endpoint,
                "timeout": agent_info.timeout
            }
        
        # 未知类型默认路由到通用问答Agent
        default_agent = self.agent_routes[TaskType.GENERAL_QA]
        return {
            "agent_type": default_agent.agent_type,
            "endpoint": default_agent.endpoint,
            "timeout": default_agent.timeout
        }

    def get_fallback_agent(self, task_type: TaskType) -> Dict[str, Any]:
        """获取失败后的备用Agent"""
        fallback_type = self.fallback_agents.get(task_type, TaskType.GENERAL_QA)
        fallback_agent = self.agent_routes[fallback_type]
        return {
            "agent_type": fallback_agent.agent_type,
            "endpoint": fallback_agent.endpoint,
            "timeout": fallback_agent.timeout
        }
