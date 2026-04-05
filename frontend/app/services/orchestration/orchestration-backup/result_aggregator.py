from typing import List, Dict, Any
from .models import OrchestrationResult

class ResultAggregator:
    def __init__(self):
        self.merge_rules = {
            "population_data": "keep",
            "analysis_result": "keep",
            "visualization_url": "keep",
            "statistics": "merge",
            "features": "concat"
        }

    def aggregate(self, subtask_results: List[Dict[str, Any]]) -> OrchestrationResult:
        """聚合多个子任务的结果"""
        aggregated_data = {}
        warnings = []
        errors = []
        success_count = 0
        total_count = len(subtask_results)

        for result in subtask_results:
            if result.get("status") == "success":
                success_count += 1
                self._merge_result(aggregated_data, result.get("data", {}))
            elif result.get("status") == "partial_success":
                success_count += 0.5
                self._merge_result(aggregated_data, result.get("data", {}))
                if "warning" in result:
                    warnings.append(result["warning"])
            else:
                error_msg = result.get("error", f"子任务 {result.get('subtask_id')} 执行失败")
                errors.append(error_msg)
                warnings.append(f"子任务 {result.get('subtask_id')} 执行失败: {error_msg}")

        # 确定最终状态
        if success_count == total_count:
            status = "success"
        elif success_count > 0:
            status = "partial_success"
        else:
            status = "failed"

        return OrchestrationResult(
            status=status,
            data=aggregated_data,
            warnings=warnings,
            errors=errors
        )

    def _merge_result(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """合并单个子任务结果到目标字典"""
        for key, value in source.items():
            if key not in target:
                target[key] = value
                continue
            
            # 根据合并规则处理重复key
            rule = self.merge_rules.get(key, "keep")
            if rule == "merge" and isinstance(target[key], dict) and isinstance(value, dict):
                target[key].update(value)
            elif rule == "concat" and isinstance(target[key], list) and isinstance(value, list):
                target[key].extend(value)
            elif rule == "keep":
                # 保留先到的结果，后续的添加为_{key}
                counter = 1
                new_key = f"{key}_{counter}"
                while new_key in target:
                    counter += 1
                    new_key = f"{key}_{counter}"
                target[new_key] = value
