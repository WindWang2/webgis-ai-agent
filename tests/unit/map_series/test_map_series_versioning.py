"""Map Series 版本化测试 —— 单调/继承/跨系列 parent 拒绝/软废弃/命名冲突/成员增删。"""
from __future__ import annotations

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
from app.services.design_specs.versioning import create_spec as create_design_spec
from app.services.map_series.errors import (
    MapSeriesConflictError,
    MapSeriesNotFoundError,
    MapSeriesValidationError,
)
from app.services.map_series.store import (
    create_series,
    create_version,
    deprecate_version,
    get_series,
    remove_member,
    resolve_payload,
    retire_series,
    upsert_member,
)
from tests.unit.map_series.helpers import series_payload


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as session:
        # pin 目标设计规范（WP1 权威行；id 由权威生成）
        spec = create_design_spec(
            session,
            {"spec_version": "design_spec.v1", "name": "系列宿主规范"},
            creator_id="series-owner",
        )
        session._host_spec_id = spec.id  # noqa: SLF001 — 测试上下文携带 pin 目标
        yield session
        session.query(CartographyMapSeriesVersion).delete()
        session.query(CartographyMapSeries).delete()
        session.query(CartographyDesignSpecVersion).delete()
        session.query(CartographyDesignSpec).delete()
        session.commit()


def _payload(db, **kw) -> dict:
    return series_payload(design_spec_id=db._host_spec_id, **kw)


def test_create_records_pin_and_version_one(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    assert series.current_version == 1
    assert series.design_spec_id == db._host_spec_id  # noqa: SLF001
    assert series.spec_version == 1  # 缺省 = latest 解析时点记录
    assert series.structural_digest.startswith("mseries-sha256:")
    assert series.status == "draft"


def test_create_with_unknown_spec_pin_fails_closed(db):
    with pytest.raises(MapSeriesValidationError) as exc:
        create_series(
            db, series_payload(design_spec_id="missing-spec"), creator_id="series-owner"
        )
    assert any(v["reason"] == "SPEC_PIN_UNRESOLVABLE" for v in exc.value.violations)


def test_name_conflict_409(db):
    create_series(db, _payload(db, name="同名系列"), creator_id="series-owner")
    with pytest.raises(MapSeriesConflictError) as exc:
        create_series(db, _payload(db, name="同名系列"), creator_id="series-owner")
    assert exc.value.code == "MAP_SERIES_NAME_CONFLICT"


def test_version_monotonic_and_inheritance(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    v1 = db.query(CartographyMapSeriesVersion).filter_by(series_id=series.id).first()
    child = dict(_payload(db, description="子版计划"))
    row = create_version(db, series.id, child, parent_version_id=v1.id, created_by="series-owner")
    assert row.version == 2
    effective, chain = resolve_payload(db, row)
    assert effective["description"] == "子版计划"
    assert chain == [series.id, series.id]  # 同系列继承链
    # 主行前移
    refreshed = get_series(db, series.id)
    assert refreshed.current_version == 2
    assert refreshed.payload["description"] == "子版计划"
    row3 = create_version(db, series.id, _payload(db, description="v3"))
    assert row3.version == 3  # max+1 单调


def test_cross_series_parent_rejected(db):
    s1 = create_series(db, _payload(db, name="系列A"), creator_id="series-owner")
    s2 = create_series(db, _payload(db, name="系列B"), creator_id="series-owner")
    v1 = db.query(CartographyMapSeriesVersion).filter_by(series_id=s1.id).first()
    with pytest.raises(MapSeriesValidationError):
        create_version(db, s2.id, _payload(db, name="系列B"), parent_version_id=v1.id)


def test_soft_deprecated_version_still_resolves(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    row = deprecate_version(db, series.id, 1, note="old plan")
    assert row.deprecated_at is not None
    effective, _chain = resolve_payload(db, row)  # 兼容读取继续可用
    assert effective["name"]


def test_member_upsert_and_remove(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    upsert_member(
        db, series.id,
        {"member_id": "m3", "title": "细节图", "session_id": "sess-series-m3", "role": "detail"},
        can_write=lambda s: True,
    )
    refreshed = get_series(db, series.id)
    ids = [m["member_id"] for m in refreshed.payload["members"]]
    assert ids == ["m1", "m2", "m3"]
    # 同 id 覆盖
    upsert_member(
        db, series.id,
        {"member_id": "m3", "title": "细节图 v2", "session_id": "sess-series-m3"},
        can_write=lambda s: True,
    )
    refreshed = get_series(db, series.id)
    assert [m["title"] for m in refreshed.payload["members"]] == ["主图", "对比图", "细节图 v2"]
    remove_member(db, series.id, "m3", can_write=lambda s: True)
    assert [m["member_id"] for m in get_series(db, series.id).payload["members"]] == ["m1", "m2"]
    with pytest.raises(MapSeriesNotFoundError):
        remove_member(db, series.id, "m3", can_write=lambda s: True)


def test_member_cannot_carry_spec_pin(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    with pytest.raises(MapSeriesValidationError):
        upsert_member(
            db, series.id,
            {"member_id": "m9", "title": "x", "session_id": "s9",
             "design_spec_id": "spec-pin-0001"},
            can_write=lambda s: True,
        )


def test_retire_series_soft(db):
    series = create_series(db, _payload(db), creator_id="series-owner")
    retire_series(db, series.id, note="done")
    refreshed = get_series(db, series.id)  # 兼容读取继续可用
    assert refreshed.status == "retired"
    assert refreshed.retired_at is not None
