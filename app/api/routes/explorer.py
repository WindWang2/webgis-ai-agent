"""Explorer API Route"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/explorer", tags=["探索引擎"])

orchestrator = ExplorerOrchestrator()


class StartExploreRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    session_id: Optional[str] = None
    expected_data_type: str = "poi_list"
    source_hint: list[str] = Field(default_factory=list)
    auto_threshold: float = 0.7


class ExploreStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int = 0
    result: Optional[dict] = None


@router.post("/start")
async def start_exploration(req: StartExploreRequest, _user: dict = Depends(get_current_user)) -> dict:
    """启动深度探索任务"""
    try:
        context = SearchContext(
            query=req.query,
            expected_data_type=req.expected_data_type,
            source_hint=req.source_hint,
            auto_threshold=req.auto_threshold,
        )
        task_id = await orchestrator.start_exploration(
            query=req.query,
            context=context,
            session_id=req.session_id or "",
        )
        return {"task_id": task_id, "status": "started"}
    except Exception as e:
        logger.error(f"Failed to start exploration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/status/{task_id}")
async def get_task_status(task_id: str) -> ExploreStatusResponse:
    """查询任务状态"""
    try:
        status = await orchestrator.get_task_status(task_id)
        return ExploreStatusResponse(
            task_id=task_id,
            status=status.get("status", "unknown"),
            progress=status.get("progress", 0),
            result=status.get("result"),
        )
    except Exception as e:
        logger.error(f"Explorer status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/abort/{task_id}")
async def abort_task(task_id: str, _user: dict = Depends(get_current_user)) -> dict:
    """中止任务"""
    success = await orchestrator.abort_task(task_id)
    return {"task_id": task_id, "aborted": success}


@router.get("/stream/{task_id}")
async def stream_progress(task_id: str):
    """SSE 实时进度流"""
    async def event_generator():
        async for event in orchestrator.stream_progress(task_id):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
