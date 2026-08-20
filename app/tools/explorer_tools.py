"""Explorer tool registration"""
import logging
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.tools.spatial_reasoning import register_spatial_reasoning
from app.tools.what_if_simulate import register_what_if_simulate

logger = logging.getLogger(__name__)


class DeepExploreArgs(BaseModel):
    query: str = Field(..., description="搜索查询，如'海淀区学校分布'")
    expected_data_type: str = Field("poi_list", description="期望数据类型: poi_list/boundary/heatmap")
    source_hint: list[str] = Field(default_factory=list, description="优先数据源: gov/osm/amap")
    auto_threshold: float = Field(0.7, ge=0.0, le=1.0, description="自动执行置信度阈值")


async def _resolve_session_owner_user_id(session_id: str) -> str:
    """解析会话归属 user_id（审计 S42 的可信上下文）。

    registry 在 dispatch 入口注入 `session_id`（来自调度上下文、覆盖 LLM
    提供的同名参数），因此基于它解析的归属是可信的 —— LLM 无法伪造
    user_id 参数。匿名会话（user_id IS NULL）解析为 ""：不注册任务归属，
    其任务无法通过 owner-verified 的 HTTP 端点监控，这是 S42 既有契约。
    """
    if not session_id:
        return ""
    try:
        from sqlalchemy import select
        from app.tools._utils import async_db_session
        from app.models.db_model import Conversation

        async with async_db_session() as db:
            row = (
                await db.execute(
                    select(Conversation.user_id).where(Conversation.id == session_id)
                )
            ).scalar_one_or_none()
        return row if isinstance(row, str) and row else ""
    except Exception as e:  # noqa: BLE001 — 归属解析失败不阻断探索启动
        logger.warning(f"[deep_explore] owner lookup failed for session {session_id}: {e}")
        return ""


def register_explorer_tools(registry: ToolRegistry):
    """注册探索引擎工具"""
    orchestrator = ExplorerOrchestrator()

    @tool(registry, tier=2, domains=["osm"], name="deep_explore",
          description="深度空间数据探索：当标准API无法获取足够数据时，自动发现、下载、解析外部数据源（政府开放数据等）并转化为地图图层。",
          args_model=DeepExploreArgs)
    async def deep_explore(
        query: str,
        expected_data_type: str = "poi_list",
        source_hint: list[str] = None,
        auto_threshold: float = 0.7,
        session_id: str = "",
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
            # #518：任务必须挂到会话归属下（审计 S42 register_owner），
            # 否则 /explorer/stream|status|abort 的 verify_owner 恒失败，
            # 前端独立进度流（streamExplorerProgress）与中止都走不通。
            user_id = await _resolve_session_owner_user_id(session_id)
            task_id = await orchestrator.start_exploration(
                query=query,
                context=context,
                session_id=session_id,
                user_id=user_id,
            )

            return {
                "type": "explorer_task",
                "task_id": task_id,
                "status": "started",
                "message": f"深度探索任务已启动 (task_id={task_id})。数据将通过 SSE 实时推送。",
            }

        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.error(f"deep_explore failed: {e}")
            return {
                "type": "explorer_task",
                "status": "failed",
                "error": str(e),
            }

    register_spatial_reasoning(registry)
    register_what_if_simulate(registry)
