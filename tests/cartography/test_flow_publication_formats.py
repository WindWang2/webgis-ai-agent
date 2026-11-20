import re
import subprocess
import uuid

import pytest

from app.services.flow_product.contracts import FlowProductConfig
from app.services.flow_product.service import preview
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.publication_export import render_publication_pdf
from app.services.session_data import session_data_manager


async def _net_spec():
    sid = f"flow-export-{uuid.uuid4().hex[:8]}"
    rows = {"type": "od_table", "rows": [
        {"origin_id": "A", "destination_id": "B", "origin_lng": 104.0, "origin_lat": 30.0,
         "destination_lng": 104.1, "destination_lat": 30.1, "trips": 8},
        {"origin_id": "B", "destination_id": "A", "origin_lng": 104.1, "origin_lat": 30.1,
         "destination_lng": 104.0, "destination_lat": 30.0, "trips": 3},
        {"origin_id": "C", "destination_id": "D", "origin_lng": 104.2, "origin_lat": 30.2,
         "destination_lng": 104.3, "destination_lat": 30.3, "trips": 10},
        {"origin_id": "D", "destination_id": "C", "origin_lng": 104.3, "origin_lat": 30.3,
         "destination_lng": 104.2, "destination_lat": 30.2, "trips": 2},
    ]}
    source_ref = await session_data_manager.store(sid, rows, prefix="bench")
    result = await preview(sid, FlowProductConfig(
        od_table_ref=source_ref, weight_field="trips", weight_unit="trips",
        direction_mode="net", geometry="curve", top_n=20,
    ))
    product = await session_data_manager.get(sid, result["product_ref"])
    edge_fc = await session_data_manager.get(sid, result["edge_ref"])
    node_fc = await session_data_manager.get(sid, result["node_ref"])
    sources = {}
    for key, source in result["mapspec_fragment"]["sources"].items():
        hydrated = dict(source)
        hydrated.pop("url", None)
        hydrated["inlineData"] = edge_fc if key.endswith("-edges-source") else node_fc
        sources[key] = hydrated
    spec = {
        "version": "1.10",
        "view": {"center": [104.15, 30.15], "zoom": 10, "bearing": 30},
        "sources": sources,
        "layers": result["mapspec_fragment"]["layers"],
        "layout": {"components": [
            {"id": "title", "type": "title", "enabled": True, "options": {"text": "成都通勤净流"}},
            {"id": "legend", "type": "legend", "enabled": True, "position": "bottom-left"},
            {"id": "north", "type": "north_arrow", "enabled": True, "position": "top-right"},
            {"id": "scale", "type": "scale_bar", "enabled": True, "position": "bottom-right"},
        ]},
    }
    await session_data_manager.clear_session(sid)
    return spec, product


@pytest.mark.asyncio
async def test_net_flow_svg_pdf_and_png_have_consistent_direction_and_weights(tmp_path):
    spec, _ = await _net_spec()
    svg96 = compile_mapspec_to_svg_detailed(spec, target_dpi=96, include_chrome=True)
    svg300 = compile_mapspec_to_svg_detailed(spec, target_dpi=300, include_chrome=True)
    for result in (svg96, svg300):
        assert result.flow_verification["complete"] is True
        assert "▶" in result.svg
        assert "rotate(" in result.svg
        assert "成都通勤净流" in result.svg
        assert "circle" in result.svg
        assert not {d["code"] for d in result.diagnostics if d["code"].startswith("flow_")}

    def widths(svg):
        return {round(float(v), 2) for v in re.findall(r'stroke-width="(\d+(?:\.\d+)?)"', svg)}

    assert {1.33, 10.67} <= widths(svg96.svg)
    assert {4.17, 33.33} <= widths(svg300.svg)
    assert svg96.svg.count("▶") >= 2
    assert svg300.svg.count("▶") >= 2

    pdf_path = tmp_path / "chengdu-net-flow.pdf"
    published = render_publication_pdf(spec, title="成都通勤净流", target_dpi=300)
    pdf_path.write_bytes(published.pdf)
    assert published.page_count == 1
    assert published.flow_verification["complete"] is True
    assert published.pdf.startswith(b"%PDF")
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) == 1
    pdf_text = subprocess.run(
        ["pdftotext", str(pdf_path), "-"], check=True, capture_output=True, text=True,
    ).stdout
    assert pdf_text.count("▶") >= 2

    png_base = tmp_path / "chengdu-net-flow"
    subprocess.run(
        ["pdftoppm", "-png", "-r", "96", "-singlefile", str(pdf_path), str(png_base)],
        check=True, capture_output=True,
    )
    from PIL import Image

    with Image.open(tmp_path / "chengdu-net-flow.png") as image:
        image.load()
        assert image.width > 100 and image.height > 100
        colors = image.convert("RGB").getcolors(maxcolors=2_000_000)
        assert colors is not None and len(colors) > 10
        nonwhite = sum(count for count, color in colors if color != (255, 255, 255))
        assert nonwhite > 1000
