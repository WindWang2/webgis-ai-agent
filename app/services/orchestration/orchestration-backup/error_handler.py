from typing import Dict, Any, Optional
from .models import SubTask, TaskType

class ErrorHandler:
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # 可重试的错误类型
        self.retryable_errors = [
            "connection timeout",
            "service unavailable",
            "rate limit exceeded",
            "internal server error",
            "500", "502", "503", "504"
        ]
        # 错误提示模板
        self.error_templates = {
            "data_query_failed": "数据查询服务暂时不可用，请稍后重试或尝试简化查询条件",
            "analysis_failed": "空间分析服务暂时不可用，已为您返回可获取的基础数据",
            "visualization_failed": "可视化生成失败，已为您返回分析结果数据",
            "general_failed": "服务暂时不可用，请稍后重试或联系管理员"
        }

    def handle_failure(self, subtask: SubTask, error_msg: str) -> Dict[str, Any]:
        """处理子任务执行失败"""
        # 检查是否可以重试
        if self._is_retryable(error_msg) and subtask.retry_count < self.max_retries:
            subtask.retry_count += 1
            return {
                "action": "retry",
                "task": subtask,
                "delay": self.retry_delay * (2 ** (subtask.retry_count - 1))  # 指数退避
            }
        
        # 检查是否可以使用备用Agent
        if not subtask.fallback_attempted and subtask.type != TaskType.GENERAL_QA:
            subtask.fallback_attempted = True
            return {
                "action": "fallback",
                "task": subtask,
                "alternative_agent": "general_qa_agent"
            }
        
        # 所有重试和备用都失败，返回错误信息
        error_message = self._get_friendly_error_message(subtask.type, error_msg)
        return {
            "action": "return_error",
            "message": error_message,
            "task": subtask
        }

    def _is_retryable(self, error_msg: str) -> bool:
        """判断错误是否可以重试"""
        error_lower = error_msg.lower()
        return any(retry_error in error_lower for retry_error in self.retryable_errors)

    def _get_friendly_error_message(self, task_type: TaskType, original_error: str) -> str:
        """生成友好的错误提示"""
        if task_type == TaskType.DATA_QUERY:
            return self.error_templates["data_query_failed"]
        elif task_type == TaskType.SPATIAL_ANALYSIS:
            return self.error_templates["analysis_failed"]
        elif task_type == TaskType.VISUALIZATION:
            return self.error_templates["visualization_failed"]
        else:
            return self.error_templates["general_failed"]
