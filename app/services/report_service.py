"""
报告生成服务 - 从会话历史生成 PDF/HTML/Markdown 报告
使用 Jinja2 模板渲染 HTML，WeasyPrint 转换为 PDF
"""
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import jinja2
import logging

try:
    import weasyprint
except ImportError:
    weasyprint = None

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "reports")


class ReportService:
    def __init__(self):
        template_path = os.path.join(os.path.dirname(__file__), "templates")
        self.template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_path),
            autoescape=False,
        )

    async def generate_report(
        self,
        session_id: str,
        session_title: str,
        messages: list[dict[str, Any]],
        output_path: str,
        format: str = "pdf",
    ) -> bool:
        """
        从会话消息生成报告。

        Args:
            session_id: 会话 ID
            session_title: 会话标题
            messages: 消息列表，每条包含 role / content / tool_result 等
            output_path: 输出文件路径
            format: pdf / html / markdown / md

        Returns:
            生成是否成功
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            report_data = self._prepare_report_data(
                session_id, session_title, messages, format
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
    ) -> dict[str, Any]:
        """将原始消息转换为模板可用的结构化数据。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        parts = [
            f"<h1>{data['title']}</h1>",
            f"<p>Generated: {data['generated_at']}</p>",
            f"<p>Messages: {data['message_count']}</p>",
        ]
        for msg in data.get("messages", []):
            parts.append(
                f"<div><b>{msg['role_label']}</b><pre>{msg['content']}</pre></div>"
            )
        for tr in data.get("tool_results", []):
            parts.append(
                f"<div><b>{tr['name']}</b><pre>{tr['result']}</pre></div>"
            )
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{data['title']}</title></head><body>"
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
            return tool_calls[0].get("name", "Tool")
        if isinstance(tool_calls, dict):
            return tool_calls.get("name", "Tool")
        return "Tool"

    @staticmethod
    def _format_tool_result(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (dict, list)):
            text = json.dumps(raw, indent=2, ensure_ascii=False)
            # Truncate very large results to keep report size reasonable
            if len(text) > 8000:
                text = text[:8000] + "\n... (truncated)"
            return text
        return str(raw)


__all__ = ["ReportService", "REPORT_DIR"]
