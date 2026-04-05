import pytest
from fastapi.testclient import TestClient
from app.services.orchestration.orchestrator import AgentOrchestrator
from app.services.orchestration.task_parser import TaskParser
from app.services.orchestration.router import TaskRouter
from app.services.orchestration.result_aggregator import ResultAggregator
from app.services.orchestration.error_handler import ErrorHandler
from app.services.orchestration.models import SubTask, TaskType

class TestTaskParser:
    def test_parse_simple_data_query(self):
        parser = TaskParser()
        request = "查询北京市2025年人口密度分布"
        task = parser.parse(request)
        assert task.task_type == TaskType.DATA_QUERY
        assert "region" in task.parameters
        assert "time" in task.parameters
        assert "layer" in task.parameters
        assert len(task.subtasks) == 1
        assert task.subtasks[0].type == TaskType.DATA_QUERY

    def test_parse_complex_spatial_analysis_task(self):
        parser = TaskParser()
        request = "分析上海市黄浦区商场周边500米范围内的人口分布，并生成热力图"
        task = parser.parse(request)
        assert task.task_type == TaskType.COMPLEX_ANALYSIS
        # We expect 2-3 subtasks depending on pattern matching
        assert 2 <= len(task.subtasks) <= 3
        subtask_types = [st.type for st in task.subtasks]
        assert TaskType.DATA_QUERY in subtask_types
        assert TaskType.VISUALIZATION in subtask_types

    def test_parse_general_question(self):
        parser = TaskParser()
        request = "什么是GIS空间分析？"
        task = parser.parse(request)
        assert task.task_type == TaskType.GENERAL_QA
        assert len(task.subtasks) == 1
        assert task.subtasks[0].type == TaskType.GENERAL_QA

class TestTaskRouter:
    def test_route_data_query_task(self):
        router = TaskRouter()
        subtask = SubTask(id=1, type=TaskType.DATA_QUERY, parameters={"layer": "population", "region": "beijing"})
        agent_info = router.route(subtask)
        assert agent_info["agent_type"] == "gis_data_agent"
        assert "endpoint" in agent_info

    def test_route_spatial_analysis_task(self):
        router = TaskRouter()
        subtask = SubTask(id=1, type=TaskType.SPATIAL_ANALYSIS, parameters={"operation": "buffer", "distance": 500})
        agent_info = router.route(subtask)
        assert agent_info["agent_type"] == "spatial_analysis_agent"

    def test_route_visualization_task(self):
        router = TaskRouter()
        subtask = SubTask(id=1, type=TaskType.VISUALIZATION, parameters={"type": "heatmap"})
        agent_info = router.route(subtask)
        assert agent_info["agent_type"] == "visualization_agent"

    def test_route_general_qa_task(self):
        router = TaskRouter()
        subtask = SubTask(id=1, type=TaskType.GENERAL_QA, parameters={"question": "什么是GIS?"})
        agent_info = router.route(subtask)
        assert agent_info["agent_type"] == "general_qa_agent"

    def test_route_unknown_task_fallback(self):
        router = TaskRouter()
        subtask = SubTask(id=1, type=TaskType.UNKNOWN, parameters={})
        agent_info = router.route(subtask)
        assert agent_info["agent_type"] == "general_qa_agent"

class TestResultAggregator:
    def test_aggregate_multiple_results(self):
        aggregator = ResultAggregator()
        subtask_results = [
            {"subtask_id": 1, "status": "success", "data": {"population_data": [1000, 2000, 3000]}},
            {"subtask_id": 2, "status": "success", "data": {"analysis_result": {"high_density_areas": ["area1", "area2"]}}},
            {"subtask_id": 3, "status": "success", "data": {"visualization_url": "https://example.com/heatmap.png"}}
        ]
        aggregated = aggregator.aggregate(subtask_results)
        assert aggregated.status == "success"
        assert "population_data" in aggregated.data
        assert "analysis_result" in aggregated.data
        assert "visualization_url" in aggregated.data

    def test_aggregate_with_partial_errors(self):
        aggregator = ResultAggregator()
        subtask_results = [
            {"subtask_id": 1, "status": "success", "data": {"population_data": [1000, 2000, 3000]}},
            {"subtask_id": 2, "status": "failed", "error": "空间分析服务暂时不可用"},
            {"subtask_id": 3, "status": "success", "data": {"visualization_url": "https://example.com/heatmap.png"}}
        ]
        aggregated = aggregator.aggregate(subtask_results)
        assert aggregated.status == "partial_success"
        assert len(aggregated.warnings) >= 1
        assert "population_data" in aggregated.data
        assert "visualization_url" in aggregated.data

class TestErrorHandler:
    def test_retry_failed_task(self):
        error_handler = ErrorHandler(max_retries=3)
        failed_task = SubTask(id=1, type=TaskType.DATA_QUERY, parameters={}, retry_count=0)
        result = error_handler.handle_failure(failed_task, "connection timeout")
        assert result["action"] == "retry"
        assert result["task"].retry_count == 1

    def test_fallback_to_alternative_agent_after_max_retries(self):
        error_handler = ErrorHandler(max_retries=2)
        failed_task = SubTask(id=1, type=TaskType.DATA_QUERY, parameters={}, retry_count=2)
        result = error_handler.handle_failure(failed_task, "connection timeout")
        assert result["action"] == "fallback"
        assert result["alternative_agent"] == "general_qa_agent"

    def test_return_error_message_after_all_fallbacks_fail(self):
        error_handler = ErrorHandler(max_retries=2)
        failed_task = SubTask(id=1, type=TaskType.DATA_QUERY, parameters={}, retry_count=2, fallback_attempted=True)
        result = error_handler.handle_failure(failed_task, "all services down")
        assert result["action"] == "return_error"
        assert "服务暂时不可用" in result["message"]

class TestAgentOrchestratorEndToEnd:
    def test_simple_task_execution(self):
        orchestrator = AgentOrchestrator()
        request = "查询北京市2025年人口密度"
        result = orchestrator.execute(request)
        assert result["status"] == "success"
        assert "data" in result

    def test_complex_task_execution(self):
        orchestrator = AgentOrchestrator()
        request = "分析深圳市南山区科技园周边300米的餐饮分布，生成热力图并统计平均评分"
        result = orchestrator.execute(request)
        assert result["status"] in ["success", "partial_success"]
        assert "data" in result or "warnings" in result

    def test_concurrent_requests_performance(self):
        import asyncio
        import time
        orchestrator = AgentOrchestrator()
        requests = [
            "查询北京市2025年人口密度",
            "什么是空间缓冲区分析？",
            "生成上海市2025年GDP分布热力图",
            "分析广州市天河区写字楼周边交通流量"
        ]
        
        start_time = time.time()
        async def run_requests():
            tasks = [orchestrator.execute_async(req) for req in requests]
            return await asyncio.gather(*tasks)
        
        results = asyncio.run(run_requests())
        total_time = time.time() - start_time
        
        assert len(results) == 4
        assert total_time < 0.5  # 4 requests should be faster than 500ms, individual <100ms
        assert all(res is not None for res in results)

    def test_error_recovery(self):
        orchestrator = AgentOrchestrator()
        # Simulate a request that would fail on first attempt but recover on retry
        request = "查询需要重试的特殊数据"
        result = orchestrator.execute(request)
        assert result["status"] == "success"  # Should recover after retry
