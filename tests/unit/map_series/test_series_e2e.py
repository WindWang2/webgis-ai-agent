"""WP3 验收端到端 —— 真实 MapSpec 引擎建 2 成员会话 → 一致性 → 预览 → 传播
apply receipts（无 mock 引擎）。对应简报验收自检第 4 条。
"""
from __future__ import annotations

import pytest

from app.services.map_series import service as ms_service
from app.services.map_series.consistency import (
    CLASSIFICATION_SCOPE_CONFLICT,
    run_series_consistency,
)
from app.services.map_series.propagation import (
    apply_propagation,
    build_propagation_preview,
)
from app.services.map_series.store import create_series
from tests.unit.map_series.conftest import seed_member_session
from tests.unit.map_series.helpers import series_payload


@pytest.mark.asyncio
async def test_e2e_consistency_preview_apply_receipts(db, session_factory):
    from app.services.design_specs.versioning import create_spec as create_design_spec

    spec = create_design_spec(
        db,
        {
            "spec_version": "design_spec.v1",
            "name": "端到端宿主规范",
            "data_roles": [{
                "name": "population", "value_type": "number",
                "measurement": "count", "unit": "人",
                "comparable_across_maps": True,
            }],
        },
        creator_id="e2e-owner",
    )
    # 两个真实成员会话：方法不一致（quantiles vs equal_interval）
    await seed_member_session("sess-e2e-a", method="quantiles", color="#1d4ed8")
    await seed_member_session("sess-e2e-b", method="equal_interval", color="#1d4ed8")
    payload = series_payload(
        name="端到端系列",
        design_spec_id=spec.id,
        members=[
            {"member_id": "m1", "title": "图A", "session_id": "sess-e2e-a",
             "data_bindings": {"population": "primaries"}},
            {"member_id": "m2", "title": "图B", "session_id": "sess-e2e-b",
             "data_bindings": {"population": "primaries"}},
        ],
        comparability={"shared_classification": True, "shared_palette": True},
        dimension={"axis": "indicator", "labels": ["人口"]},
    )
    series = create_series(db, payload, creator_id="e2e-owner")

    # 1) 一致性：发现分类方法冲突（error）
    report = await run_series_consistency(db, series.id)
    assert report["ok"] is False
    assert any(f["code"] == CLASSIFICATION_SCOPE_CONFLICT for f in report["findings"])

    # 2) 传播预览：两成员均 eligible，将改 classification
    intents = {"classification": {"target_layer_id": "districts",
                                  "method": "natural_breaks", "k": 4}}
    preview = await build_propagation_preview(db, series.id, intents=intents)
    assert [m["eligible"] for m in preview["members"]] == [True, True]

    # 3) 逐图 CAS 传播：两成员 receipts committed
    result = await apply_propagation(db, series.id, intents=intents, actor="e2e-owner")
    assert result["summary"]["committed"] == 2, result
    for receipt in result["receipts"]:
        assert receipt["status"] == "committed"
        assert isinstance(receipt["revision"], int) and receipt["revision"] >= 1

    # 真实落图：两成员会话的 legend_spec.method 已一致
    for sid in ("sess-e2e-a", "sess-e2e-b"):
        mapspec = await ms_service.load_member_mapspec(sid)
        layer = next(ly for ly in mapspec["layers"] if ly["id"] == "districts")
        assert layer["legend_spec"]["method"] == "natural_breaks"
        assert layer["legend_spec"]["k"] == 4

    # 4) 复查一致性：分类冲突消除（ok 恢复 True）
    report2 = await run_series_consistency(db, series.id)
    assert report2["ok"] is True, report2["findings"]
    assert not any(
        f["code"] == CLASSIFICATION_SCOPE_CONFLICT for f in report2["findings"]
    )
