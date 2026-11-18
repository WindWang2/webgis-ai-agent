"""Behavioral contracts retained by the backend Ruff debt cleanup."""

import pytest


def test_receipt_download_template_keeps_single_literal_braces():
    from app.schemas.data_delivery_schema import DeliveryReceipt
    from app.services.data_delivery.service import _receipt_summary

    receipt = DeliveryReceipt(
        delivery_id="delivery-1", job_id="42", format="geojson",
        status="completed", filename="features.geojson",
    )
    url = _receipt_summary(receipt)["download_url"]
    assert url == "/api/v1/data-delivery/jobs/{job_id}/download"
    assert url.format(job_id=receipt.job_id) == "/api/v1/data-delivery/jobs/42/download"


def test_text_render_still_validates_height_without_using_it_for_layout():
    from app.services.mapspec_svg_chrome_layout import render_authored_text_block

    with pytest.raises(ValueError, match="height-invalid"):
        render_authored_text_block({
            "box": {"width": 120, "height": "height-invalid"},
            "text": {"lines": ["Visible"]},
        })


def test_text_render_keeps_all_lines_and_escaping_with_valid_height():
    from app.services.mapspec_svg_chrome_layout import render_authored_text_block

    svg = render_authored_text_block({
        "box": {"width": 120, "height": 80},
        "text": {"lines": ["First <line>", "Second & final"]},
    })
    assert 'data-g34-lines="2"' in svg
    assert 'data-g34-line="1">First &lt;line&gt;</text>' in svg
    assert 'data-g34-line="2">Second &amp; final</text>' in svg
