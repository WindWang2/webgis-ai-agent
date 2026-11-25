"""一致性检查测试 —— 各 codes 正例 + 负例（真实 MapSpec 引擎建 2-3 成员会话）。

负例锁定：不同指标（axis 非 indicator / 无共同可比角色）绝不产生
PALETTE_MISMATCH_SHARED。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
from app.services.design_specs.versioning import create_spec as create_design_spec
from app.services.map_series.consistency import (
    ASSET_VERSION_DRIFT,
    CLASSIFICATION_SCOPE_CONFLICT,
    EXTENT_DIVERGENCE,
    LEGEND_MISSING,
    LOCKED_FIELD_CONFLICT,
    PALETTE_MISMATCH_SHARED,
    SESSION_UNAVAILABLE,
    SOURCE_MISSING,
    SPEC_PIN_MISSING,
    TITLE_MISSING,
    UNIT_MISMATCH,
    run_series_consistency,
)
from app.services.map_series.store import create_series
from tests.unit.map_series.helpers import series_payload


@pytest.fixture()
def host_spec(db):
    spec = create_design_spec(
        db,
        {
            "spec_version": "design_spec.v1",
            "name": "一致性宿主规范",
            "data_roles": [
                {
                    "name": "population",
                    "value_type": "number",
                    "measurement": "count",
                    "unit": "人",
                    "comparable_across_maps": True,
                }
            ],
        },
        creator_id="series-owner",
    )
    return spec.id


def _member(member_id: str, sid: str, **kw) -> Dict[str, Any]:
    base = {
        "member_id": member_id,
        "title": f"成员{member_id}",
        "session_id": sid,
        "role": "primary",
        "data_bindings": {"population": "primaries"},
    }
    base.update(kw)
    return base


async def _make_two_member_series(
    db,
    session_factory,
    host_spec_id: str,
    *,
    axis: str = "indicator",
    shared_classification: bool = True,
    shared_palette: bool = True,
    shared_extent: bool = False,
    member_a_kw: Dict[str, Any] | None = None,
    member_b_kw: Dict[str, Any] | None = None,
    session_a_kw: Dict[str, Any] | None = None,
    session_b_kw: Dict[str, Any] | None = None,
    name: str = "一致性系列",
    seed_b: bool = True,
):
    await session_factory("sess-cons-a", **(session_a_kw or {}))
    if seed_b:
        await session_factory("sess-cons-b", **(session_b_kw or {}))
    payload = series_payload(
        name=name,
        design_spec_id=host_spec_id,
        members=[
            _member("m1", "sess-cons-a", **(member_a_kw or {})),
            _member("m2", "sess-cons-b", **(member_b_kw or {})),
        ],
        comparability={
            "shared_classification": shared_classification,
            "shared_palette": shared_palette,
            "shared_extent_by_role": shared_extent,
        },
        dimension={"axis": axis, "labels": ["人口"]},
    )
    return create_series(db, payload, creator_id="series-owner")


def _codes(result: Dict[str, Any]) -> List[str]:
    return [f["code"] for f in result["findings"]]


@pytest.mark.asyncio
class TestConsistencyCodes:
    async def test_clean_series_ok(self, db, session_factory, host_spec):
        series = await _make_two_member_series(db, session_factory, host_spec, name="一致干净系列")
        result = await run_series_consistency(db, series.id)
        assert result["ok"] is True, result["findings"]
        assert _codes(result) == [], result["findings"]

    async def test_spec_pin_missing_when_spec_unresolvable(
        self, db, session_factory, host_spec
    ):
        series = await _make_two_member_series(db, session_factory, host_spec, name="pin缺失系列")
        db.query(CartographyDesignSpecVersion).delete()
        db.query(CartographyDesignSpec).delete()
        db.commit()
        result = await run_series_consistency(db, series.id)
        assert result["ok"] is False
        assert _codes(result).count(SPEC_PIN_MISSING) == 2  # 逐成员可定位
        for f in result["findings"]:
            if f["code"] == SPEC_PIN_MISSING:
                assert f["member_id"] in ("m1", "m2")

    async def test_session_unavailable_not_evaluated(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec, name="会话缺失系列", seed_b=False
        )
        result = await run_series_consistency(db, series.id)
        assert SESSION_UNAVAILABLE in _codes(result)
        un = next(f for f in result["findings"] if f["code"] == SESSION_UNAVAILABLE)
        assert un["member_id"] == "m2"
        assert "not_evaluated" in un["message"]

    async def test_unit_mismatch_error(self, db, session_factory, host_spec):
        # 规范 data_roles 声明 population 单位 = 人（comparable）；成员 B 图例
        # 声明 persons —— 对照契约 + 跨成员双重不一致，均 UNIT_MISMATCH。
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="单位不一致系列",
            session_a_kw={"unit": "人"},
            session_b_kw={"unit": "persons"},
        )
        result = await run_series_consistency(db, series.id)
        codes = [f["code"] for f in result["findings"] if f["code"] == UNIT_MISMATCH]
        assert codes, result["findings"]
        assert result["ok"] is False
        for f in result["findings"]:
            if f["code"] == UNIT_MISMATCH:
                assert f["member_id"] == "m2"  # A 契约一致，B 双重偏离

    async def test_classification_scope_conflict(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="方法冲突系列",
            session_a_kw={"method": "quantiles"},
            session_b_kw={"method": "equal_interval"},
        )
        result = await run_series_consistency(db, series.id)
        assert CLASSIFICATION_SCOPE_CONFLICT in _codes(result)
        assert result["ok"] is False

    async def test_palette_mismatch_positive_only_for_indicator_comparable_group(
        self, db, session_factory, host_spec
    ):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="色带漂移系列",
            axis="indicator",
            session_a_kw={"color": "#1d4ed8", "ramp": ["#eff6ff", "#1d4ed8"]},
            session_b_kw={"color": "#dc2626", "ramp": ["#fef2f2", "#dc2626"]},
        )
        result = await run_series_consistency(db, series.id)
        assert PALETTE_MISMATCH_SHARED in _codes(result)
        assert result["ok"] is False

    async def test_palette_mismatch_negative_axis_region(self, db, session_factory, host_spec):
        # 负例：axis=region 时不同色带绝不报 PALETTE_MISMATCH
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="区域轴色带自由系列",
            axis="region",
            session_a_kw={"color": "#1d4ed8", "ramp": ["#eff6ff", "#1d4ed8"]},
            session_b_kw={"color": "#dc2626", "ramp": ["#fef2f2", "#dc2626"]},
        )
        result = await run_series_consistency(db, series.id)
        assert PALETTE_MISMATCH_SHARED not in _codes(result)

    async def test_palette_mismatch_negative_no_comparable_role(self, db, session_factory, host_spec):
        # 负例：成员绑定不同字段/角色（无可比组）→ 不强制同色域
        await session_factory("sess-cons-a", color="#1d4ed8")
        await session_factory("sess-cons-b", color="#dc2626")
        payload = series_payload(
            name="无可比角色系列",
            design_spec_id=host_spec,
            members=[
                _member("m1", "sess-cons-a"),
                _member("m2", "sess-cons-b", data_bindings={"gdp": "primaries"}),
            ],
            comparability={"shared_palette": True},
            dimension={"axis": "indicator", "labels": []},
        )
        series = create_series(db, payload, creator_id="series-owner")
        result = await run_series_consistency(db, series.id)
        assert PALETTE_MISMATCH_SHARED not in _codes(result)

    async def test_title_and_source_missing(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="组件缺席系列",
            session_a_kw={"with_title": False, "with_attribution": False},
        )
        result = await run_series_consistency(db, series.id)
        codes = _codes(result)
        assert TITLE_MISSING in codes and SOURCE_MISSING in codes
        tm = next(f for f in result["findings"] if f["code"] == TITLE_MISSING)
        assert tm["severity"] == "warning" and tm["member_id"] == "m1"

    async def test_legend_missing(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="图例缺席系列",
            session_a_kw={"with_stops_no_legend": True, "legend_spec": False},
            shared_classification=False,
            shared_palette=False,
        )
        result = await run_series_consistency(db, series.id)
        assert LEGEND_MISSING in _codes(result)
        lm = next(f for f in result["findings"] if f["code"] == LEGEND_MISSING)
        assert lm["component"] == "districts"

    async def test_asset_version_drift(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="资产漂移系列",
            session_a_kw={"symbol_asset_revision": 1},
            session_b_kw={"symbol_asset_revision": 2},
            shared_classification=False,
            shared_palette=False,
        )
        result = await run_series_consistency(db, series.id)
        assert ASSET_VERSION_DRIFT in _codes(result)
        drift = next(f for f in result["findings"] if f["code"] == ASSET_VERSION_DRIFT)
        assert drift["severity"] == "warning"

    async def test_extent_divergence_disclosure(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="视域分歧系列",
            shared_extent=True,
            shared_classification=False,
            shared_palette=False,
            session_a_kw={"bounds": [103.0, 30.0, 104.0, 31.0]},
            session_b_kw={"bounds": [120.0, 20.0, 121.0, 21.0]},
        )
        result = await run_series_consistency(db, series.id)
        assert EXTENT_DIVERGENCE in _codes(result)
        ed = next(f for f in result["findings"] if f["code"] == EXTENT_DIVERGENCE)
        assert ed["severity"] == "info"

    async def test_locked_field_conflict_with_intents(self, db, session_factory, host_spec):
        series = await _make_two_member_series(
            db, session_factory, host_spec,
            name="锁定冲突系列",
            member_a_kw={"locked_fields": ["palettes"]},
            shared_classification=False,
            shared_palette=False,
        )
        result = await run_series_consistency(
            db, series.id,
            intents={"palette": {"target_layer_id": "districts", "color": "#123456"}},
        )
        assert LOCKED_FIELD_CONFLICT in _codes(result)
        lf = next(f for f in result["findings"] if f["code"] == LOCKED_FIELD_CONFLICT)
        assert lf["member_id"] == "m1"
        # 不传 intents 时无该 finding
        result2 = await run_series_consistency(db, series.id)
        assert LOCKED_FIELD_CONFLICT not in _codes(result2)
