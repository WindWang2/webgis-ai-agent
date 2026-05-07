"""Explorer tool registration"""
import logging
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext

logger = logging.getLogger(__name__)


class DeepExploreArgs(BaseModel):
    query: str = Field(..., description="搜索查询，如'海淀区学校分布'")
    expected_data_type: str = Field("poi_list", description="期望数据类型: poi_list/boundary/heatmap")
    source_hint: list[str] = Field(default_factory=list, description="优先数据源: gov/osm/amap")
    auto_threshold: float = Field(0.7, ge=0.0, le=1.0, description="自动执行置信度阈值")


def register_explorer_tools(registry: ToolRegistry):
    """注册探索引擎工具"""
    orchestrator = ExplorerOrchestrator()

    @tool(registry, name="deep_explore",
          description="深度空间数据探索：当标准API无法获取足够数据时，自动发现、下载、解析外部数据源（政府开放数据等）并转化为地图图层。",
          args_model=DeepExploreArgs)
    async def deep_explore(
        query: str,
        expected_data_type: str = "poi_list",
        source_hint: list[str] = None,
        auto_threshold: float = 0.7,
    ) -> dict:
        """
        执行深度探索。
        返回任务启动状态，实际数据通过 SSE 异步推送。
        """
        if source_hint is None:
            source_hint = []

        try:
            context = SearchContext(
                query=query,
                expected_data_type=expected_data_type,
                source_hint=source_hint,
                auto_threshold=auto_threshold,
            )
            task_id = await orchestrator.start_exploration(
                query=query,
                context=context,
            )

            return {
                "type": "explorer_task",
                "task_id": task_id,
                "status": "started",
                "message": f"深度探索任务已启动 (task_id={task_id})。数据将通过 SSE 实时推送。",
            }

        except Exception as e:
            logger.error(f"deep_explore failed: {e}")
            return {
                "type": "explorer_task",
                "status": "failed",
                "error": str(e),
            }
