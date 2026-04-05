import re
from typing import Dict, Any
from .models import ParsedTask, SubTask, TaskType

class TaskParser:
    def __init__(self):
        # 关键词匹配规则
        self.patterns = {
            TaskType.DATA_QUERY: [r"查询|获取|统计|查找|列出", r"数据|图层|信息|记录"],
            TaskType.SPATIAL_ANALYSIS: [r"分析|计算|缓冲区|叠加|相交|邻近|距离|热力|密度"],
            TaskType.VISUALIZATION: [r"生成|绘制|展示|导出|地图|图表|热力图|分布图|报表", r"生成.*图|绘制.*图|展示.*图"],
            TaskType.GENERAL_QA: [r"什么是|怎么用|为什么|如何|解释|说明"]
        }
        
        # GIS领域实体识别正则
        self.entity_patterns = {
            "region": r"(北京市|上海市|广州市|深圳市|杭州市|南京市|成都市|重庆市|武汉市|西安市)(市|区|县)?|([\u4e00-\u9fa5]{2,10}(省|市|区|县|街道|镇))",
            "time": r"(\d{4}年|20\d{2}|今年|去年|明年|\d{1,2}月|\d{1,2}日)",
            "layer": r"(人口|GDP|建筑|道路|河流|POI|餐饮|商场|写字楼|学校|医院|交通|土地利用|植被)",
            "operation": r"(缓冲区|叠加分析|相交分析|邻近分析|网络分析|路径分析|密度分析)"
        }

    def parse(self, request: str, context: Dict[str, Any] = None) -> ParsedTask:
        """解析用户自然语言请求为结构化任务"""
        context = context or {}
        
        # 1. 识别任务类型
        task_type = self._classify_task_type(request)
        
        # 2. 提取实体参数
        parameters = self._extract_entities(request)
        
        # 3. 拆分子任务
        subtasks = self._split_subtasks(task_type, parameters, request)
        
        return ParsedTask(
            original_request=request,
            task_type=task_type,
            subtasks=subtasks,
            parameters=parameters,
            context=context
        )

    def _classify_task_type(self, request: str) -> TaskType:
        """分类请求的任务类型"""
        # 优先判断是否是问答类请求
        if re.search(r"什么是|怎么|为什么|如何|解释|说明", request):
            return TaskType.GENERAL_QA
        
        scores = {}
        for task_type, patterns in self.patterns.items():
            if task_type == TaskType.GENERAL_QA:
                continue  # 已经优先判断过了
            score = sum(1 for pattern in patterns if re.search(pattern, request))
            scores[task_type] = score
        
        max_score = max(scores.values()) if scores else 0
        if max_score == 0:
            return TaskType.GENERAL_QA
        
        # 检查是否是复杂任务（需要明确的多个操作动词）
        # 只有当存在明确的多个操作（如"查询...并分析...并生成"）时才是复杂任务
        has_multiple_operations = re.search(r"并|且|同时|然后", request) and len(scores) >= 2
        
        if has_multiple_operations:
            return TaskType.COMPLEX_ANALYSIS
        
        # 返回最高得分的任务类型
        return max(scores, key=scores.get)

    def _extract_entities(self, request: str) -> Dict[str, Any]:
        """提取请求中的实体参数"""
        entities = {}
        for entity_type, pattern in self.entity_patterns.items():
            matches = re.findall(pattern, request)
            if matches:
                # 处理元组匹配结果
                if isinstance(matches[0], tuple):
                    entities[entity_type] = [m[0] for m in matches if m[0]]
                else:
                    entities[entity_type] = matches
        return entities

    def _split_subtasks(self, task_type: TaskType, parameters: Dict[str, Any], request: str) -> list[SubTask]:
        """将任务拆分为子任务"""
        subtasks = []
        subtask_id = 1
        
        if task_type == TaskType.COMPLEX_ANALYSIS:
            # 复杂任务按顺序拆分为数据查询->空间分析->可视化
            if "layer" in parameters or "region" in parameters:
                subtasks.append(SubTask(
                    id=subtask_id,
                    type=TaskType.DATA_QUERY,
                    parameters=parameters,
                    dependencies=[]
                ))
                subtask_id += 1
            
            if any(k in parameters for k in ["operation", "distance", "radius"]):
                subtasks.append(SubTask(
                    id=subtask_id,
                    type=TaskType.SPATIAL_ANALYSIS,
                    parameters=parameters,
                    dependencies=[subtask_id - 1]
                ))
                subtask_id += 1
            
            if re.search(r"生成|绘制|展示|导出|图|报表", request):
                subtasks.append(SubTask(
                    id=subtask_id,
                    type=TaskType.VISUALIZATION,
                    parameters=parameters,
                    dependencies=[subtask_id - 1]
                ))
                subtask_id += 1
        
        else:
            # 简单任务直接创建一个子任务
            subtasks.append(SubTask(
                id=subtask_id,
                type=task_type,
                parameters=parameters,
                dependencies=[]
            ))
        
        return subtasks
