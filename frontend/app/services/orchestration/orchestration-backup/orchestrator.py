import asyncio
import time
from typing import Dict, Any, List
import httpx
from .models import ParsedTask, SubTask, OrchestrationResult
from .task_parser import TaskParser
from .router import TaskRouter
from .result_aggregator import ResultAggregator
from .error_handler import ErrorHandler

class AgentOrchestrator:
    def __init__(self):
        self.parser = TaskParser()
        self.router = TaskRouter()
        self.aggregator = ResultAggregator()
        self.error_handler = ErrorHandler(max_retries=3)
        self.http_client = httpx.AsyncClient(timeout=60)
        self.context_store: Dict[str, Dict[str, Any]] = {}  # 多轮对话上下文存储

    async def execute_async(self, request: str, session_id: str = None) -> Dict[str, Any]:
        """异步执行任务"""
        start_time = time.time()
        context = self.context_store.get(session_id, {}) if session_id else {}
        
        # 1. 解析任务
        parsed_task = self.parser.parse(request, context)
        
        # 2. 执行子任务
        subtask_results = await self._execute_subtasks(parsed_task.subtasks)
        
        # 3. 聚合结果
        result = self.aggregator.aggregate(subtask_results)
        result.execution_time = time.time() - start_time
        
        # 4. 保存上下文（用于多轮对话）
        if session_id:
            context["last_request"] = request
            context["last_result"] = result.model_dump()
            self.context_store[session_id] = context
        
        return result.model_dump()

    def execute(self, request: str, session_id: str = None) -> Dict[str, Any]:
        """同步执行任务（兼容同步调用）"""
        return asyncio.run(self.execute_async(request, session_id))

    async def _execute_subtasks(self, subtasks: List[SubTask]) -> List[Dict[str, Any]]:
        """执行所有子任务，支持依赖调度"""
        results = []
        completed_tasks: Dict[int, Dict[str, Any]] = {}
        
        # 按依赖顺序执行子任务
        while len(results) < len(subtasks):
            # 找到所有依赖已完成的待执行子任务
            ready_tasks = [
                st for st in subtasks
                if st.status == "pending" and all(dep in completed_tasks for dep in st.dependencies)
            ]
            
            if not ready_tasks:
                # 没有可执行的任务，说明有依赖缺失，标记剩余任务为失败
                for st in subtasks:
                    if st.status == "pending":
                        st.status = "failed"
                        results.append({
                            "subtask_id": st.id,
                            "status": "failed",
                            "error": "依赖任务执行失败"
                        })
                break
            
            # 并行执行所有就绪任务
            tasks = [self._execute_single_subtask(st, completed_tasks) for st in ready_tasks]
            batch_results = await asyncio.gather(*tasks)
            
            # 处理执行结果
            for res in batch_results:
                subtask_id = res["subtask_id"]
                if res["status"] == "success":
                    completed_tasks[subtask_id] = res["data"]
                results.append(res)
        
        return results

    async def _execute_single_subtask(self, subtask: SubTask, completed_tasks: Dict[int, Any]) -> Dict[str, Any]:
        """执行单个子任务，包含重试和降级逻辑"""
        try:
            # 注入依赖任务的结果到参数中
            for dep_id in subtask.dependencies:
                if dep_id in completed_tasks:
                    subtask.parameters[f"dep_{dep_id}_result"] = completed_tasks[dep_id]
            
            # 路由到对应Agent
            agent_info = self.router.route(subtask)
            
            # 调用Agent接口
            response = await self._call_agent(agent_info, subtask)
            
            if response.get("status") == "success":
                return {
                    "subtask_id": subtask.id,
                    "status": "success",
                    "data": response.get("data", {})
                }
            else:
                # 处理失败
                return await self._handle_subtask_failure(subtask, response.get("error", "未知错误"))
                
        except Exception as e:
            return await self._handle_subtask_failure(subtask, str(e))

    async def _call_agent(self, agent_info: Dict[str, Any], subtask: SubTask) -> Dict[str, Any]:
        """调用Agent服务接口"""
        try:
            response = await self.http_client.post(
                agent_info["endpoint"],
                json={
                    "task_type": subtask.type.value,
                    "parameters": subtask.parameters,
                    "subtask_id": subtask.id
                },
                timeout=agent_info["timeout"]
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    async def _handle_subtask_failure(self, subtask: SubTask, error_msg: str) -> Dict[str, Any]:
        """处理子任务失败，包含重试和降级逻辑"""
        handle_result = self.error_handler.handle_failure(subtask, error_msg)
        
        if handle_result["action"] == "retry":
            # 等待重试延迟
            await asyncio.sleep(handle_result["delay"])
            # 重新执行任务
            return await self._execute_single_subtask(handle_result["task"], {})
        
        elif handle_result["action"] == "fallback":
            # 调用备用Agent
            fallback_agent = self.router.get_fallback_agent(subtask.type)
            try:
                response = await self._call_agent(fallback_agent, subtask)
                if response.get("status") == "success":
                    return {
                        "subtask_id": subtask.id,
                        "status": "success",
                        "data": response.get("data", {}),
                        "warning": "使用备用Agent返回结果"
                    }
            except Exception as e:
                pass
        
        # 返回错误
        return {
            "subtask_id": subtask.id,
            "status": "failed",
            "error": handle_result.get("message", error_msg)
        }

    def clear_context(self, session_id: str) -> None:
        """清除指定会话的上下文"""
        if session_id in self.context_store:
            del self.context_store[session_id]
