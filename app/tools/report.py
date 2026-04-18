"""
报告生成工具 — Agent 可调用的分析报告生成能力

架构对齐声明 (Agent Philosophy Alignment):
在 "Agent is Everything" 架构下，报告生成不应是旁路操作。
Agent 作为思维主体，应当主动编排和输出报告。此工具将报告生成
纳入 Agent 的工具链，使其成为 Agent "思维外化"的一部分。
"""
import logging
import uuid
import os
from typing import Optional

from app.tools.registry import ToolRegistry
from app.services.report_service import ReportService, REPORT_DIR
from app.core.database import SessionLocal
from app.models.db_model import Conversation, Message
from app.models.report import Report

logger = logging.getLogger(__name__)


def register_report_tools(registry: ToolRegistry):
    """注册报告生成工具到 Agent 工具链"""

    @registry.tool(
        name="generate_analysis_report",
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
        _session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Agent 调用此工具主动生成分析报告"""
        session_id = _session_id or kwargs.get("session_id")
        if not session_id:
            return {"error": "无法确定当前会话 ID，请在对话中重试。"}

        db = SessionLocal()
        try:
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

            # Prepare
            report_id = str(uuid.uuid4())
            report_title = title or conversation.title or "空间分析报告"
            ext = "md" if format in ("markdown", "md") else format
            file_name = f"{report_id}.{ext}"
            file_path = os.path.join(REPORT_DIR, file_name)

            # Create DB record
            report = Report(
                id=report_id,
                session_id=session_id,
                title=report_title,
                format=format,
                status="generating",
                file_path=file_path,
            )
            db.add(report)
            db.commit()

            # Generate
            svc = ReportService()
            msg_dicts = [
                {
                    "role": m.role,
                    "content": m.content or "",
                    "tool_calls": m.tool_calls,
                    "tool_result": m.tool_result,
                }
                for m in messages
            ]

            success = await svc.generate_report(
                session_id=session_id,
                session_title=conversation.title or report_title,
                messages=msg_dicts,
                output_path=file_path,
                format=format,
            )

            if success and os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                report.status = "completed"
                report.file_size = file_size
                db.commit()

                return {
                    "type": "report_generated",
                    "report_id": report_id,
                    "title": report_title,
                    "format": format,
                    "file_size_kb": round(file_size / 1024, 1),
                    "download_url": f"/api/v1/reports/{report_id}/download",
                    "message": f"报告「{report_title}」已生成完毕（{format.upper()} 格式，{round(file_size / 1024, 1)} KB）。",
                }
            else:
                report.status = "failed"
                report.error_message = "生成过程未产出文件"
                db.commit()
                return {"error": "报告生成失败，请稍后重试。"}

        except Exception as e:
            logger.error(f"Report tool error: {e}", exc_info=True)
            return {"error": f"报告生成异常: {str(e)}"}
        finally:
            db.close()
