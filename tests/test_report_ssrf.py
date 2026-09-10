"""Test SSRF & LFI protections in report generation (SEC-01)."""
import pytest
from app.services.report_service import safe_url_fetcher, sanitize_report_svg, ReportService


def test_safe_url_fetcher_rejects_file_scheme():
    with pytest.raises(ValueError, match="Disallowed URL scheme"):
        safe_url_fetcher("file:///etc/passwd")


def test_safe_url_fetcher_rejects_private_ips():
    with pytest.raises(ValueError, match="Blocked URL in PDF renderer"):
        safe_url_fetcher("http://127.0.0.1:8000/secret")

    with pytest.raises(ValueError, match="Blocked URL in PDF renderer"):
        safe_url_fetcher("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(ValueError, match="Blocked URL in PDF renderer"):
        safe_url_fetcher("http://10.0.1.5/internal")

    with pytest.raises(ValueError, match="Blocked URL in PDF renderer"):
        safe_url_fetcher("http://localhost:8080/admin")


def test_safe_url_fetcher_allows_data_uri():
    res = safe_url_fetcher("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")
    assert res is not None


def test_safe_url_fetcher_allows_public_https():
    # Public domain URL should pass security validation without raising ValueError
    res = safe_url_fetcher("https://tile.openstreetmap.org/0/0/0.png")
    # Even if weasyprint is not installed, it does not raise ValueError
    assert res is None or isinstance(res, dict)


def test_sanitize_report_svg_strips_entities_and_xxe():
    malicious_svg = """<?xml version="1.0"?>
    <!DOCTYPE foo [
      <!ELEMENT foo ANY >
      <!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
    <svg><text>&xxe;</text></svg>"""
    sanitized = sanitize_report_svg(malicious_svg)
    assert "file:///etc/passwd" not in sanitized
    assert "<!ENTITY" not in sanitized


def test_sanitize_report_svg_strips_scripts_and_file_href():
    svg_with_script = """<svg xmlns="http://www.w3.org/2000/svg">
        <script>alert(1)</script>
        <image href="file:///etc/shadow"/>
        <circle cx="50" cy="50" r="40" stroke="green" fill="yellow" />
    </svg>"""
    sanitized = sanitize_report_svg(svg_with_script)
    assert "<script>" not in sanitized
    assert "file:///etc/shadow" not in sanitized
    assert "circle" in sanitized


def test_report_service_jinja_filter_sanitizes_vector_svg():
    service = ReportService()
    template = service.template_env.get_template("report_default.html")
    malicious_svg = """<svg><script>evil()</script><image href="file:///etc/passwd"/><rect width="10" height="10"/></svg>"""
    rendered = template.render(
        title="Test Report",
        generated_at="2026-09-10",
        message_count=1,
        vector_svg=malicious_svg,
        has_conversation=False,
        has_tool_results=False,
        messages=[],
    )
    assert "evil()" not in rendered
    assert "file:///etc/passwd" not in rendered
    assert "rect" in rendered
