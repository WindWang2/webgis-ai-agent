"""Unit tests for ReportService WeasyPrint vector SVG map injection."""
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import report_service as report_service_mod
from app.services.report_service import ReportService


# WeasyPrint is an optional system dependency (requires native libs: cairo,
# pango, gdk-pixbuf). The integration test below runs the REAL WeasyPrint render
# (no mock) only when it is importable; otherwise it skips. This matches the
# spec #271 requirement: a backend E2E test guarded by
# ``@pytest.mark.skipif(weasyprint is None)`` that asserts the output PDF is a
# real, non-empty PDF.
weasyprint = report_service_mod.weasyprint


# Fixture MapSpec reused across the vector-injection tests.
_MAPSPEC = {
    "sources": {
        "s1": {
            "type": "geojson",
            "data": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                        "properties": {"name": "Beijing"},
                    }
                ],
            },
        }
    },
    "layers": [
        {
            "id": "pts",
            "type": "circle",
            "source": "s1",
            "paint": {"circle-color": "#de2d26", "circle-radius": 6},
        }
    ],
}


@pytest.mark.asyncio
async def test_generate_report_embeds_vector_svg():
    service = ReportService()

    mapspec = _MAPSPEC

    messages = [
        {"role": "user", "content": "生成北京市地图报告"},
        {"role": "assistant", "content": "这是生成的地图报告。"},
    ]

    with patch("app.services.report_service.weasyprint", create=True) as mock_weasyprint:
        mock_pdf = MagicMock()
        mock_weasyprint.HTML.return_value = mock_pdf

        success = await service.generate_report(
            session_id="test_session",
            session_title="北京地图测试",
            messages=messages,
            output_path="/tmp/test_report.pdf",
            format="pdf",
            mapspec=mapspec,
        )

        assert success is True
        # Verify WeasyPrint HTML instantiation contained vector SVG markup with DPI scaled radius.
        # The compiler now emits the canonical minimal form (_fmt_num strips
        # trailing zeros): 6 * (300/72) = 25.0 -> "25" (not "25.0").
        html_passed = mock_weasyprint.HTML.call_args[1]["string"]
        assert 'r="25"' in html_passed
        assert "#de2d26" in html_passed


@pytest.mark.asyncio
async def test_create_and_generate_forwards_mapspec_to_vector_svg():
    """P0-1 regression: create_and_generate must propagate mapspec so that
    _prepare_report_data compiles a non-None vector_svg for the PDF.

    Previously the production callers never passed mapspec, so vector_svg was
    always None even though the plumbing existed. This test locks the
    service-level contract: mapspec in -> vector_svg populated.
    """
    service = ReportService()

    db = AsyncMock()
    conv = MagicMock(id="sess-1", title="T")
    db.get = AsyncMock(return_value=conv)

    msg = MagicMock(role="user", content="hi", tool_calls=None, tool_result=None)
    scalars_mock = MagicMock(all=MagicMock(return_value=[msg]))
    exec_result = MagicMock(scalars=MagicMock(return_value=scalars_mock))
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.expunge = MagicMock()

    with (
        patch("app.services.report_service.weasyprint", create=True) as mock_wp,
        patch("app.core.database.AsyncSessionLocal") as mock_factory,
    ):
        mock_wp.HTML.return_value = MagicMock()
        # session_factory path: return a no-op async context manager
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        await service.create_and_generate(  # noqa: F841 — return value not asserted; side effect exercised
            db=db,
            session_id="sess-1",
            format="pdf",
            title="T",
            mapspec=_MAPSPEC,
        )

    # The saga reached render and WeasyPrint.HTML was called with an HTML
    # string containing the compiled vector SVG (non-None mapspec flowed through).
    assert mock_wp.HTML.called
    html_passed = mock_wp.HTML.call_args[1]["string"]
    assert "mapspec-vector-layers" in html_passed


@pytest.mark.asyncio
async def test_create_and_generate_without_mapspec_omits_vector_svg():
    """P0-1 complement: when no mapspec is available (legacy sessions), the
    report still renders, just without the vector map. Guards against
    regressions that would crash on mapspec=None.
    """
    service = ReportService()

    db = AsyncMock()
    conv = MagicMock(id="sess-1", title="T")
    db.get = AsyncMock(return_value=conv)

    msg = MagicMock(role="user", content="hi", tool_calls=None, tool_result=None)
    scalars_mock = MagicMock(all=MagicMock(return_value=[msg]))
    exec_result = MagicMock(scalars=MagicMock(return_value=scalars_mock))
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.expunge = MagicMock()

    with (
        patch("app.services.report_service.weasyprint", create=True) as mock_wp,
        patch("app.core.database.AsyncSessionLocal") as mock_factory,
    ):
        mock_wp.HTML.return_value = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        await service.create_and_generate(  # noqa: F841 — return value not asserted; side effect exercised
            db=db,
            session_id="sess-1",
            format="pdf",
            title="T",
            mapspec=None,
        )

    assert mock_wp.HTML.called
    html_passed = mock_wp.HTML.call_args[1]["string"]
    assert "mapspec-vector-layers" not in html_passed


# ──────────────────────────────────────────────────────────────────────
# Spec #271: real WeasyPrint integration test.
#
# The tests above mock WeasyPrint, so they verify the HTML wiring but never
# produce a real PDF. Spec #271 requires an E2E test that runs WeasyPrint for
# real and asserts the output file exists, is non-empty, and is a valid PDF
# (magic bytes %PDF-). Guarded with skipif(weasyprint is None) since
# WeasyPrint needs native system libs (cairo/pango) that may not be present in
# every environment; it runs wherever WeasyPrint is installed.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(weasyprint is None, reason="WeasyPrint not installed (needs cairo/pango system libs)")
@pytest.mark.asyncio
async def test_generate_report_renders_real_pdf_with_magic_bytes():
    """E2E: generate_report(format='pdf') runs the real WeasyPrint engine and
    writes a valid, non-empty PDF whose first 5 bytes are '%PDF-'.

    No mocks: the full pipeline (data prep -> HTML render -> WeasyPrint ->
    file write) executes. If WeasyPrint is missing the test skips; where it is
    present (CI with the native deps) it asserts a genuine PDF is produced.
    """
    service = ReportService()

    messages = [
        {"role": "user", "content": "生成北京市地图报告"},
        {"role": "assistant", "content": "这是生成的地图报告。"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "real_report.pdf")

        success = await service.generate_report(
            session_id="e2e_real_pdf",
            session_title="E2E Real PDF Test",
            messages=messages,
            output_path=output_path,
            format="pdf",
            mapspec=_MAPSPEC,
        )

        assert success is True
        # File exists and is non-empty.
        assert os.path.isfile(output_path)
        assert os.path.getsize(output_path) > 0

        # Magic bytes: a real PDF starts with %PDF-.
        with open(output_path, "rb") as f:
            head = f.read(5)
        assert head == b"%PDF-"

        # Page count: WeasyPrint emits at least one /Type /Page object. Read the
        # whole file and require >= 1 page entry so a zero-page PDF fails.
        with open(output_path, "rb") as f:
            content = f.read()
        page_obj_matches = __import__("re").findall(rb"/Type\s*/Page(?!s)\b", content)
        assert len(page_obj_matches) >= 1
