"""Explorer API Route"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes.layer import _verify_session_owner
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.core.auth import get_current_user
from app.schemas.explorer_schema import (
    ExploreAbortResponse,
    ExploreStatusResponse,
    StartExploreRequest,
    StartExploreResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/explorer", tags=["探索引擎"])

orchestrator = ExplorerOrchestrator()


@router.post("/start", response_model=StartExploreResponse)
async def start_exploration(req: StartExploreRequest, _user: dict = Depends(get_current_user)) -> StartExploreResponse:
    """启动深度探索任务

    审计 S42：若带 session_id，校验归属 —— 防止匿名启动任务消耗 LLM/API 配额
    或把任务结果写到他人 session 的 session_data_manager。
    """
    if req.session_id:
        await _verify_session_owner(req.session_id, _user.get("user_id"))
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
            user_id=_user.get("user_id", ""),
        )
        return StartExploreResponse(task_id=task_id, status="started")
    except Exception as e:
        logger.error(f"Failed to start exploration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/status/{task_id}", response_model=ExploreStatusResponse)
async def get_task_status(task_id: str, _user: dict = Depends(get_current_user)) -> ExploreStatusResponse:
    """查询探索任务状态（审计 S42：校验任务所有权）"""
    # #526: durable owner check — the in-process _task_owners map dies with the
    # process, so a post-restart poll would 404 a legit owner's chain.
    if not await orchestrator.verify_chain_owner(task_id, _user.get("user_id", "")):
        raise HTTPException(status_code=404, detail="Task not found")
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


@router.post("/abort/{task_id}", response_model=ExploreAbortResponse)
async def abort_task(task_id: str, _user: dict = Depends(get_current_user)) -> ExploreAbortResponse:
    """中止探索任务（审计 S42：校验任务所有权）"""
    if not await orchestrator.verify_chain_owner(task_id, _user.get("user_id", "")):
        raise HTTPException(status_code=404, detail="Task not found")
    success = await orchestrator.abort_task(task_id)
    return ExploreAbortResponse(task_id=task_id, aborted=success)


@router.get("/stream/{task_id}")
async def stream_progress(task_id: str, _user: dict = Depends(get_current_user)):
    """SSE 实时进度流（审计 S42：校验任务所有权）"""
    if not await orchestrator.verify_chain_owner(task_id, _user.get("user_id", "")):
        raise HTTPException(status_code=404, detail="Task not found")
    async def event_generator():
        async for event in orchestrator.stream_progress(task_id):
            yield event

    # transport goal E-F-1：补齐 SSE 响应头。之前只设 media_type，缺
    # Cache-Control（浏览器/中间代理可能缓存 SSE）和 X-Accel-Buffering（nginx
    # 即使关了 proxy_buffering，这个响应头也是显式声明，避免被某些代理聚合）。
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
