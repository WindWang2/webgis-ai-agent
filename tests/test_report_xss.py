"""Security: fallback HTML in report_service must escape user content."""
import pytest
from app.services.report_service import ReportService


def _make_service():
    return ReportService()


def _sample_data(title="Test", messages=None, tool_results=None):
    return {
        "title": title,
        "generated_at": "2026-01-01",
        "message_count": 1,
        "messages": messages or [{"role_label": "User", "content": "hello"}],
        "tool_results": tool_results or [],
        "has_tool_results": False,
    }


class TestReportFallbackHtmlXSS:
    def test_title_is_escaped(self):
        svc = _make_service()
        data = _sample_data(title='<script>alert("xss")</script>')
        html = svc._fallback_html(data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_message_content_is_escaped(self):
        svc = _make_service()
        data = _sample_data(messages=[
            {"role_label": "User", "content": '<img src=x onerror="alert(1)">'}
        ])
        html = svc._fallback_html(data)
        # <img must be escaped so browser doesn't interpret it as a tag
        assert "<img" not in html
        assert "&lt;img" in html

    def test_tool_result_is_escaped(self):
        svc = _make_service()
        data = _sample_data(tool_results=[
            {"name": "tool", "result": '"><script>alert(1)</script>'}
        ])
        html = svc._fallback_html(data)
        assert "<script>" not in html

    def test_normal_content_rendered(self):
        svc = _make_service()
        data = _sample_data(title="Normal Report")
        html = svc._fallback_html(data)
        assert "<h1>Normal Report</h1>" in html
