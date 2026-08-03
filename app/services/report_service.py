"""
报告生成服务 - 从会话历史生成 PDF/HTML/Markdown 报告
使用 Jinja2 模板渲染 HTML，WeasyPrint 转换为 PDF
"""
import html as html_mod
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import jinja2
import logging

import uuid
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.api_response import ErrCode
from app.models.report import Report
from app.models.db_model import Conversation, Message
from app.services.mapspec_to_svg import compile_mapspec_to_svg

try:
    import weasyprint
except ImportError:
    weasyprint = None

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "reports")


def file_ext(fmt: str) -> str:
    if fmt in ("markdown", "md"):
        return "md"
    return fmt


def serialize_report(r: Report) -> dict:
    return {
        "id": r.id,
        "session_id": r.session_id,
        "title": r.title,
        "format": r.format,
        "status": r.status,
        "file_size": r.file_size,
        "share_code": r.share_code,
        "share_expires_at": (
            r.share_expires_at.isoformat() if r.share_expires_at else None
        ),
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "download_url": f"/api/v1/reports/{r.id}/download" if r.status == "completed" else None,
    }


@dataclass
class ReportSagaResult:
    success: bool
    report_data: Optional[dict] = None
    message: str = ""
    err_code: Optional[ErrCode] = None


class ReportService:
    def __init__(self):
        template_path = os.path.join(os.path.dirname(__file__), "templates")
        self.template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_path),
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )

    async def create_and_generate(
        self,
        db: AsyncSession,
        session_id: str,
        format: str = "pdf",
        title: Optional[str] = None,
        session_factory=None,
        mapspec: Optional[dict[str, Any]] = None,
    ) -> ReportSagaResult:
        """
        Report status-lifecycle saga (ADR-0023):
        1. Fetch conversation & messages using active `db` session.
        2. Create Report row in 'generating' status, commit, and `db.expunge(report)`.
        3. Perform rendering (`generate_report`).
        4. Write final status ('completed'/'failed') via `session_factory` (or fallback `db`).
        """
        fmt = format.lower()
        allowed_formats = {"pdf", "html", "markdown", "md"}
        if fmt not in allowed_formats:
            return ReportSagaResult(
                success=False,
                err_code=ErrCode.VALIDATE_ERROR,
                message=f"不支持的格式: {fmt}，可选: {', '.join(sorted(allowed_formats))}",
            )

        conversation = await db.get(Conversation, session_id)
        if not conversation:
            return ReportSagaResult(
                success=False,
                err_code=ErrCode.NOT_FOUND,
                message="会话不存在",
            )

        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == session_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

        if not messages:
            return ReportSagaResult(
                success=False,
                err_code=ErrCode.VALIDATE_ERROR,
                message="会话中暂无消息，无法生成报告",
            )

        report_id = str(uuid.uuid4())
        report_title = title or conversation.title or "分析报告"
        file_name = f"{report_id}.{file_ext(fmt)}"
        file_path = os.path.join(REPORT_DIR, file_name)

        report = Report(
            id=report_id,
            session_id=session_id,
            title=report_title,
            format=fmt,
            status="generating",
            file_path=file_path,
        )
        db.add(report)
        await db.commit()
        await db.refresh(report)

        # Detach report object so async rendering doesn't hold the DB session
        db.expunge(report)

        final_status = "failed"
        final_error = "报告生成失败"
        final_size = None

        try:
            msg_dicts = [
                {
                    "role": m.role,
                    "content": m.content or "",
                    "tool_calls": m.tool_calls,
                    "tool_result": m.tool_result,
                }
                for m in messages
            ]

            success = await self.generate_report(
                session_id=session_id,
                session_title=report_title,
                messages=msg_dicts,
                output_path=file_path,
                format=fmt,
                mapspec=mapspec,
            )

            if success and os.path.exists(file_path):
                final_status = "completed"
                final_size = os.path.getsize(file_path)
                final_error = None
        except Exception as e:
            logger.error(f"Report generation error: {e}", exc_info=True)

        if session_factory is None:
            from app.core.database import AsyncSessionLocal
            session_factory = AsyncSessionLocal

        if session_factory is not None:
            async with session_factory() as db2:
                db2_report = await db2.get(Report, report_id)
                if db2_report is not None:
                    db2_report.status = final_status
                    db2_report.error_message = final_error
                    if final_size is not None:
                        db2_report.file_size = final_size
                    await db2.commit()
                    report.status = final_status
                    report.error_message = final_error
                    if final_size is not None:
                        report.file_size = final_size
        else:
            report.status = final_status
            report.error_message = final_error
            if final_size is not None:
                report.file_size = final_size
            await db.commit()
            await db.refresh(report)

        report_serialized = serialize_report(report)

        if report.status == "failed":
            return ReportSagaResult(
                success=False,
                report_data=report_serialized,
                err_code=ErrCode.SERVER_ERROR,
                message="报告生成失败",
            )

        return ReportSagaResult(
            success=True,
            report_data=report_serialized,
            message="报告生成成功",
        )


    async def generate_report(
        self,
        session_id: str,
        session_title: str,
        messages: list[dict[str, Any]],
        output_path: str,
        format: str = "pdf",
        mapspec: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        从会话消息生成报告。

        Args:
            session_id: 会话 ID
            session_title: 会话标题
            messages: 消息列表，每条包含 role / content / tool_result 等
            output_path: 输出文件路径
            format: pdf / html / markdown / md
            mapspec: 可选的 MapSpec 定义字典

        Returns:
            生成是否成功
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            report_data = self._prepare_report_data(
                session_id, session_title, messages, format, mapspec=mapspec
            )

            if format in ("markdown", "md"):
                md_content = self._render_markdown(report_data)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                return True

            # HTML (also used as PDF source)
            html_content = self._render_html(report_data)

            if format == "html":
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                return True

            if format == "pdf":
                self._html_to_pdf(html_content, output_path)
                return True

            logger.error(f"Unsupported report format: {format}")
            return False

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def _prepare_report_data(
        self,
        session_id: str,
        session_title: str,
        messages: list[dict[str, Any]],
        format: str,
        mapspec: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """将原始消息转换为模板可用的结构化数据。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # 提取用户和助手消息
        conversation_msgs = []
        tool_results = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_result_raw = msg.get("tool_result")

            if role == "user":
                conversation_msgs.append({
                    "role": "user",
                    "role_label": "用户",
                    "content": self._clean_text(content),
                })
            elif role == "assistant":
                conversation_msgs.append({
                    "role": "assistant",
                    "role_label": "助手",
                    "content": self._clean_text(content),
                })
            elif role == "tool" and tool_result_raw:
                tool_results.append({
                    "name": self._extract_tool_name(msg),
                    "result": self._format_tool_result(tool_result_raw),
                })

        vector_svg = None
        if mapspec:
            try:
                vector_svg = compile_mapspec_to_svg(mapspec, target_dpi=300)
            except Exception as ex:
                logger.warning(f"Failed to compile MapSpec to SVG for report: {ex}")

        return {
            "title": f"分析报告: {session_title}",
            "session_id": session_id,
            "session_title": session_title,
            "generated_at": now,
            "message_count": len(conversation_msgs),
            "format": format,
            "has_conversation": len(conversation_msgs) > 0,
            "has_tool_results": len(tool_results) > 0,
            "messages": conversation_msgs,
            "tool_results": tool_results,
            "vector_svg": vector_svg,
        }

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _render_html(self, data: dict[str, Any]) -> str:
        try:
            template = self.template_env.get_template("report_default.html")
            return template.render(**data)
        except jinja2.TemplateNotFound:
            logger.warning("Template report_default.html not found, using fallback")
            return self._fallback_html(data)

    def _fallback_html(self, data: dict[str, Any]) -> str:
        """Minimal inline fallback when template file is missing."""
        esc = html_mod.escape
        parts = [
            f"<h1>{esc(data['title'])}</h1>",
            f"<p>Generated: {esc(data['generated_at'])}</p>",
            f"<p>Messages: {data['message_count']}</p>",
        ]
        if data.get("vector_svg"):
            parts.append(f"<div class='vector-map-container'>{data['vector_svg']}</div>")
        for msg in data.get("messages", []):
            parts.append(
                f"<div><b>{esc(msg['role_label'])}</b><pre>{esc(msg['content'])}</pre></div>"
            )
        for tr in data.get("tool_results", []):
            parts.append(
                f"<div><b>{esc(tr['name'])}</b><pre>{esc(str(tr['result']))}</pre></div>"
            )
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{esc(data['title'])}</title></head><body>"
            + "\n".join(parts)
            + "</body></html>"
        )

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def _render_markdown(self, data: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append(f"# {data['title']}")
        lines.append("")
        lines.append(f"> Generated: {data['generated_at']}  |  Messages: {data['message_count']}")
        lines.append("")

        if data.get("has_tool_results"):
            lines.append("## Tool Results")
            lines.append("")
            for tr in data["tool_results"]:
                lines.append(f"### {tr['name']}")
                lines.append("")
                lines.append("```")
                lines.append(tr["result"])
                lines.append("```")
                lines.append("")

        if data.get("has_conversation"):
            lines.append("## Conversation")
            lines.append("")
            for msg in data["messages"]:
                role = "**User**" if msg["role"] == "user" else "**Assistant**"
                lines.append(f"##### {role}")
                lines.append("")
                lines.append(msg["content"])
                lines.append("")

        lines.append("---")
        lines.append(f"*Generated by WebGIS AI Agent · {data['generated_at']}*")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # PDF conversion
    # ------------------------------------------------------------------

    def _html_to_pdf(self, html_content: str, output_path: str) -> None:
        if weasyprint is None:
            raise ImportError(
                "WeasyPrint is not installed. Install with: pip install weasyprint"
            )
        weasyprint.HTML(string=html_content).write_pdf(output_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        """Sanitise text for safe embedding in HTML (we don't autoescape)."""
        if not text:
            return ""
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text.strip())
        return text

    @staticmethod
    def _extract_tool_name(msg: dict[str, Any]) -> str:
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            # OpenAI 格式: tool_calls[0]["function"]["name"]
            first_call = tool_calls[0]
            if isinstance(first_call, dict):
                fn = first_call.get("function", {})
                if isinstance(fn, dict):
                    return fn.get("name", "Tool")
                return first_call.get("name", "Tool")
            return "Tool"
        if isinstance(tool_calls, dict):
            # 单个 tool_call 格式
            fn = tool_calls.get("function", {})
            if isinstance(fn, dict):
                return fn.get("name", "Tool")
        return "Tool"


class SpatialReportEngine(ReportService):
    """Deep Spatial Report Engine consolidating metadata extraction, Jinja HTML templating, WeasyPrint PDF conversion, and report status saga."""

    def __init__(self):
        super().__init__()

    async def generate_report_saga(
        self,
        db: AsyncSession,
        session_id: str,
        format: str = "pdf",
        title: Optional[str] = None,
        session_factory=None,
        mapspec: Optional[dict[str, Any]] = None,
    ) -> ReportSagaResult:
        """Execute complete status-lifecycle saga for report generation."""
        return await self.create_and_generate(
            db=db,
            session_id=session_id,
            format=format,
            title=title,
            session_factory=session_factory,
            mapspec=mapspec,
        )


spatial_report_engine = SpatialReportEngine()
report_service = spatial_report_engine
