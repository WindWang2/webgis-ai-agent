"""Unit tests for ReportService WeasyPrint vector SVG map injection."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.report_service import ReportService


@pytest.mark.asyncio
async def test_generate_report_embeds_vector_svg():
    service = ReportService()

    mapspec = {
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
