"""Security: monitoring report fallback HTML must escape user content."""
import pytest
from app.tools.monitoring_report import _fallback_monitoring_html


class TestMonitoringReportXSS:
    def test_title_escaped(self):
        html = _fallback_monitoring_html(
            title='<script>alert(1)</script>',
            region_name="test",
            period="2026",
            assets=[],
            summary_text=None,
            conclusions=None,
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_region_name_escaped(self):
        html = _fallback_monitoring_html(
            title="ok",
            region_name='<img src=x onerror="alert(1)">',
            period="2026",
            assets=[],
            summary_text=None,
            conclusions=None,
        )
        assert "<img" not in html

    def test_summary_escaped(self):
        html = _fallback_monitoring_html(
            title="ok",
            region_name="r",
            period="2026",
            assets=[],
            summary_text='<b>bold</b><script>alert(1)</script>',
            conclusions=None,
        )
        assert "<script>" not in html

    def test_conclusions_escaped(self):
        html = _fallback_monitoring_html(
            title="ok",
            region_name="r",
            period="2026",
            assets=[],
            summary_text=None,
            conclusions='"><script>alert(1)</script>',
        )
        assert "<script>" not in html

    def test_asset_name_escaped(self):
        html = _fallback_monitoring_html(
            title="ok",
            region_name="r",
            period="2026",
            assets=[{"name": '<script>alert(1)</script>', "id": 1, "format": "tif", "file_size_kb": 100, "time": "2026"}],
            summary_text=None,
            conclusions=None,
        )
        assert "<script>" not in html

    def test_normal_content_works(self):
        html = _fallback_monitoring_html(
            title="环境监测报告",
            region_name="北京",
            period="2026-Q1",
            assets=[],
            summary_text="正常内容",
            conclusions="建议加强监测",
        )
        assert "<h1>环境监测报告</h1>" in html
