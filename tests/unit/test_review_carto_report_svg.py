"""Deep-review carto-platform CP-04 / CP-07：报告 SVG 净化回归。"""

from __future__ import annotations

import pytest

from app.services.report_service import sanitize_report_svg

SVG = 'xmlns="http://www.w3.org/2000/svg"'


def test_cp04_output_uses_default_svg_namespace():
    out = sanitize_report_svg(
        f'<svg {SVG} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
        '<defs><linearGradient id="g"/></defs><rect fill="url(#g)"/>'
        '<use xlink:href="#g"/><text>地图 &amp; 图例</text></svg>'
    )
    assert out.startswith("<svg ")
    assert "ns0:" not in out
    assert 'xlink:href="#g"' in out
    assert "<rect" in out and "地图 &amp; 图例" in out


def test_cp04_html_parser_sees_svg_elements():
    from html.parser import HTMLParser

    tags: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            tags.append(tag)

    P().feed(sanitize_report_svg(f'<svg {SVG}><path d="M0 0L1 1"/></svg>'))
    assert tags[:2] == ["svg", "path"]


@pytest.mark.parametrize(
    "payload,forbidden",
    [
        (f'<svg {SVG}><a><set attributeName="href" to="javascript:alert(1)"/></a></svg>', "javascript"),
        (f'<svg {SVG}><animate attributeName="href" values="javascript:alert(1)"/></svg>', "javascript"),
        (f'<svg {SVG}><a href="java&#x09;script:alert(1)"><text>x</text></a></svg>', "script:"),
        (f'<svg {SVG}><a href=" JaVaScRiPt:alert(1)"><text>x</text></a></svg>', "alert"),
        (f'<svg {SVG}><image href="data:image/svg+xml;base64,PHN2Zz4="/></svg>', "data:image/svg"),
        (f'<svg {SVG} onload="alert(1)">&nbsp;</svg>', "onload"),  # 解析失败 → fail closed
        (f'<svg {SVG}><style>@import url(//evil/x.css)</style></svg>', "@import"),
        (f'<svg {SVG}><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject></svg>', "div"),
        (f'<svg {SVG}><rect style="background:url(javascript:alert(1))"/></svg>', "javascript"),
    ],
)
def test_cp07_sanitizer_bypasses_closed(payload, forbidden):
    out = sanitize_report_svg(payload)
    assert forbidden.lower() not in out.lower()


def test_cp07_unparseable_svg_fails_closed():
    assert sanitize_report_svg(f'<svg {SVG} onload="alert(1)"><rect></svg>') == ""


def test_cp07_non_svg_root_rejected():
    assert sanitize_report_svg('<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>') == ""


def test_cp07_safe_data_image_kept():
    out = sanitize_report_svg(f'<svg {SVG}><image href="data:image/png;base64,iVBORw0KGgo="/></svg>')
    assert "data:image/png;base64" in out


def test_cp07_fallback_html_sanitizes_vector_svg():
    from app.services.report_service import ReportService

    html = ReportService()._fallback_html({
        "title": "t", "generated_at": "now", "message_count": 0,
        "vector_svg": f'<svg {SVG}><a><set attributeName="href" to="javascript:alert(1)"/></a></svg>',
    })
    assert "javascript" not in html
    assert "<svg " in html


async def test_cp07_shared_report_view_served_with_sandbox_csp(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.routes import report as mod
    from app.core.database import get_async_db

    html_file = tmp_path / "r.html"
    html_file.write_text("<html><body>report</body></html>")
    monkeypatch.setattr(mod, "REPORT_DIR", str(tmp_path))
    rep = MagicMock()
    rep.id, rep.format, rep.status = "r-123456789", "html", "completed"
    rep.file_path, rep.share_expires_at = str(html_file), None
    result = MagicMock()
    result.scalar_one_or_none.return_value = rep
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/v1/reports/shared/abc/view")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "default-src 'none'" in csp
    assert "script-src" not in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
