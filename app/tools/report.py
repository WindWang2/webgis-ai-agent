"""
报告生成工具 — Agent 可调用的分析报告生成能力

架构对齐声明 (Agent Philosophy Alignment):
在 "Agent is Everything" 架构下，报告生成不应是旁路操作。
Agent 作为思维主体，应当主动编排和输出报告。此工具将报告生成
纳入 Agent 的工具链，使其成为 Agent "思维外化"的一部分。
"""
import asyncio
import logging
import uuid
import os
from typing import Any, Optional

from app.tools.registry import ToolRegistry
from app.services.report_service import spatial_report_engine, REPORT_DIR
from app.services.mapspec_store import mapspec_store
from app.tools._utils import db_session
from app.models.db_model import Conversation, Message
from app.models.report import REPORT_STATUS_IN_PROGRESS, Report

logger = logging.getLogger(__name__)


def _prepare_report_record(session_id: str, format: str, title: Optional[str]) -> dict[str, Any]:
    """Phase 1: 读取会话消息并落 'processing' 状态的 Report 行。

    #426 计算隔离不变式 1：db_session() 是阻塞的同步 SQLAlchemy Session，
    必须经 asyncio.to_thread 在 worker 线程执行，禁止在事件循环上直接调用。
    Session 在本函数内创建、使用并关闭，绝不跨线程共享。
    """
    with db_session() as db:
        conversation = db.get(Conversation, session_id)
        if not conversation:
            return {"error": f"会话 {session_id} 不存在"}

        messages = (
            db.query(Message)
            .filter(Message.conversation_id == session_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        if not messages:
            return {"error": "会话中暂无消息，无法生成报告"}

        report_id = str(uuid.uuid4())
        report_title = title or conversation.title or "空间分析报告"
        ext = "md" if format in ("markdown", "md") else format
        file_name = f"{report_id}.{ext}"
        file_path = os.path.join(REPORT_DIR, file_name)

        report = Report(
            id=report_id,
            session_id=session_id,
            title=report_title,
            format=format,
            status=REPORT_STATUS_IN_PROGRESS,
            file_path=file_path,
        )
        db.add(report)
        db.commit()

        msg_dicts = [
            {
                "role": m.role,
                "content": m.content or "",
                "tool_calls": m.tool_calls,
                "tool_result": m.tool_result,
            }
            for m in messages
        ]

        return {
            "report_id": report_id,
            "report_title": report_title,
            "file_path": file_path,
            "msg_dicts": msg_dicts,
        }


def _finalize_report_record(report_id: str, success: bool, file_path: str) -> Optional[int]:
    """Phase 3: 写终态 status（completed/failed）。成功时返回文件大小。

    与 _prepare_report_record 同理：同步 SQLAlchemy 写必须在 worker 线程
    执行（#426），Session 线程内闭合。
    """
    with db_session() as db:
        report = db.get(Report, report_id)
        if success and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if report:
                report.status = "completed"
                report.file_size = file_size
                db.commit()
            return file_size
        else:
            if report:
                report.status = "failed"
                report.error_message = "生成过程未产出文件"
                db.commit()
            return None


def register_report_tools(registry: ToolRegistry):
    """注册报告生成工具到 Agent 工具链"""

    @registry.tool(
        tier=2, domains=["report"], name="generate_analysis_report",
        description=(
            "为当前会话生成一份专业的分析报告（PDF/HTML/Markdown）。"
            "报告将包含完整的对话记录、工具调用结果和空间分析过程。"
            "仅在用户明确要求生成报告时调用此工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["pdf", "html", "markdown"],
                    "description": "报告格式，默认 markdown",
                    "default": "markdown",
                },
                "title": {
                    "type": "string",
                    "description": "报告标题（可选，默认使用会话标题）",
                },
            },
            "required": [],
        },
    )
    async def generate_analysis_report(
        format: str = "markdown",
        title: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        """Agent 调用此工具主动生成分析报告

        Issue #584: 形参名必须精确为 ``session_id``——registry 的会话注入探针
        （inspect.signature 精确匹配）只认这个名字。旧形参 ``_session_id`` 收
        不到注入，agent 路径恒失败，唯一兜底是 **kwargs 里由 LLM 编造的
        ``session_id`` 实参——正是注入设计要封杀的不可信输入，构成跨会话读取
        面（读取/落盘任意会话的对话消息）。注册用的显式 parameters= schema 只
        暴露 format/title，session_id 不进 LLM 目录；registry 注入发生在函数
        执行前，会用可信 session_id 覆盖 LLM 传入的任何同名实参，因此这里不
        再接收 kwargs（多余实参直接 TypeErrors，被 registry 捕获为诚实错误）。
        """
        if not session_id:
            return {"error": "无法确定当前会话 ID，请在对话中重试。"}

        # #426 计算隔离：同步 SQLAlchemy 读写（db_session）经 to_thread
        # 下沉到 worker 线程；渲染由 spatial_report_engine.generate_report
        # 内部同样卸载（见 report_service.py）。
        prep = await asyncio.to_thread(_prepare_report_record, session_id, format, title)
        if "error" in prep:
            return {"error": prep["error"]}

        report_id = prep["report_id"]
        report_title = prep["report_title"]
        file_path = prep["file_path"]

        mapspec = await mapspec_store.get_mapspec(session_id)
        success = await spatial_report_engine.generate_report(
            session_id=session_id,
            session_title=report_title,
            messages=prep["msg_dicts"],
            output_path=file_path,
            format=format,
            mapspec=mapspec,
        )

        file_size = await asyncio.to_thread(
            _finalize_report_record, report_id, bool(success), file_path
        )
        if file_size is not None:
            return {
                "type": "report_generated",
                "report_id": report_id,
                "title": report_title,
                "format": format,
                "file_size_kb": round(file_size / 1024, 1),
                "download_url": f"/api/v1/reports/{report_id}/download",
                "message": f"报告「{report_title}」已生成完毕（{format.upper()} 格式，{round(file_size / 1024, 1)} KB）。",
            }
        return {"error": "报告生成过程失败，未能生成文件。"}
