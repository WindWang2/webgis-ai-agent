"""Unit tests for ReportService WeasyPrint vector SVG map injection."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.report_service import ReportService


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
        # Verify WeasyPrint HTML instantiation contained vector SVG markup with DPI scaled radius
        html_passed = mock_weasyprint.HTML.call_args[1]["string"]
        expected_r = round(6 * (300 / 72.0), 2)  # base_r * (target_dpi / 72)
        assert f'r="{expected_r}"' in html_passed
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

        res = await service.create_and_generate(
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

        res = await service.create_and_generate(
            db=db,
            session_id="sess-1",
            format="pdf",
            title="T",
            mapspec=None,
        )

    assert mock_wp.HTML.called
    html_passed = mock_wp.HTML.call_args[1]["string"]
    assert "mapspec-vector-layers" not in html_passed
