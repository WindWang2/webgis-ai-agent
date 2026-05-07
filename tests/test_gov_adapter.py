"""GovDataAdapter tests"""
import pytest
from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import RawContent


def test_guess_format():
    adapter = GovDataAdapter()
    assert adapter._guess_format("http://example.com/data.csv") == "csv"
    assert adapter._guess_format("http://example.com/data.xlsx") == "xlsx"
    assert adapter._guess_format("http://example.com/data.json") == "json"
    assert adapter._guess_format("http://example.com/data") == "unknown"


def test_detect_encoding_utf8():
    adapter = GovDataAdapter()
    data = "hello,world".encode("utf-8")
    assert adapter._detect_encoding(data) == "utf-8"


def test_detect_encoding_gbk():
    adapter = GovDataAdapter()
    data = "中文".encode("gbk")
    assert adapter._detect_encoding(data) == "gbk"


def test_parse_date():
    adapter = GovDataAdapter()
    from datetime import datetime
    assert adapter._parse_date("2024-03-15") == datetime(2024, 3, 15)
    assert adapter._parse_date("2024-03") == datetime(2024, 3, 1)
    assert adapter._parse_date("") is None
    assert adapter._parse_date("invalid") is None


@pytest.mark.asyncio
async def test_parse_csv():
    adapter = GovDataAdapter()
    csv_data = "name,address,level\n清华附中,北京市海淀区,高中\n北大附中,北京市海淀区,高中".encode("utf-8")
    raw = RawContent(data=csv_data, content_type="text/csv", encoding="utf-8")

    structured = await adapter.parse(raw)
    assert len(structured.rows) == 2
    assert structured.rows[0]["name"] == "清华附中"
    assert len(structured.fields) == 3
    assert structured.fields[0].name == "name"


@pytest.mark.asyncio
async def test_parse_csv_with_nulls():
    adapter = GovDataAdapter()
    csv_data = "name,address\nA,\nB,addr2".encode("utf-8")
    raw = RawContent(data=csv_data, content_type="text/csv", encoding="utf-8")

    structured = await adapter.parse(raw)
    assert len(structured.rows) == 2
    assert structured.fields[0].nullable_ratio == 0.0
    assert structured.fields[1].nullable_ratio == 0.5
