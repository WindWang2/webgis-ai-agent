"""Explorer — parse 阶段错误隔离与自动字段映射（P4 补强 E6 相邻面）。"""
from __future__ import annotations

import base64

import pytest

from app.services.explorer.parse_stage import run_parse_stage


def _payload(rows: list[dict], fieldnames: list[str]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")


def _fetch_result(source_id: str, csv_text: str) -> dict:
    return {"source_id": source_id, "ref_id": f"ref_{source_id}", "size_bytes": 10, "format": "csv"}


@pytest.mark.asyncio
async def test_parse_extracts_rows_and_stores(monkeypatch) -> None:
    csv_text = "name,phone\na,110\nb,120\n"
    stored: list[dict] = []

    stored_payload = {"data": base64.b64encode(csv_text.encode()).decode("ascii"),
                      "codec": "base64", "content_type": "text/csv", "encoding": "utf-8"}
    result = await run_parse_stage(
        "t1", [_fetch_result("s1", csv_text)],
        load_ref=lambda ref: stored_payload,
        store_ref=lambda d, kind: (stored.append(d), "ref_parsed")[1])
    assert result.success is True
    assert stored, "parse 产物必须经 store_ref 落库"


@pytest.mark.asyncio
async def test_parse_tolerates_unreadable_source(monkeypatch) -> None:
    async def broken_load(ref):
        return None

    result = await run_parse_stage(
        "t2", [{"source_id": "s1", "ref_id": "missing", "size_bytes": 0, "format": "csv"}],
        load_ref=broken_load)
    # 单源损坏：阶段失败并带可读消息（不静默成功）。
    assert result.success is False or result.data.get("errors")
