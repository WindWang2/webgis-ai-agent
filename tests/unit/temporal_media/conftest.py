"""G35 测试夹具：合成（simulated）时序 MapSpec，真实执行渲染/编码/解码。

诚实声明：MapSpec 会话存储与 DB 在这些单元/流水线测试中以纯函数
``build_frozen`` 注入合成文档；渲染（SVG 编译 + rsvg 栅格化）、编码
（APNG/GIF）与独立解码**全部真实执行，无 mock**。Pi 全链在 integration/
里另用固定 pin 的真实子进程。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

from app.services.temporal_media.capabilities import probe_capabilities
from app.services.temporal_media.contracts import MediaPlanArgs
from app.services.temporal_media.freeze import build_frozen
from app.services.temporal_media.planning import build_plan


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _frame_start(year: int, tz: str, day: int = 1) -> datetime:
    return datetime(year, 1, day, 0, 0, 0, tzinfo=ZoneInfo(tz))


def _time_value(year: int, representation: str, tz: str, day: int = 1) -> Any:
    instant = datetime(year, 6 if day == 1 else 3, 15 if day == 1 else day,
                       12, 0, 0, tzinfo=ZoneInfo(tz))
    if representation == "epoch_s":
        return int(instant.timestamp())
    if representation == "epoch_ms":
        return int(instant.timestamp() * 1000)
    if representation == "iso_string":
        return instant.isoformat().replace("+00:00", "Z")
    if representation == "date_string":
        return f"{year:04d}-{'06' if day == 1 else '03'}-{15 if day == 1 else day:02d}"
    return int(year)


def temporal_document(
    years: Optional[List[int]] = None,
    *,
    representation: str = "year_int",
    tz: str = "UTC",
    granularity: str = "year",
    identical_picture: bool = False,
    second_layer: bool = False,
    days: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """带 temporal_view.v1 绑定的内联矢量 MapSpec（每年一个真实观测桶）。"""
    years = list(years or [2019, 2020, 2021, 2022, 2023])
    field = {"year_int": "year", "epoch_s": "ts", "epoch_ms": "ts",
             "iso_string": "stamp", "date_string": "stamp"}[representation]
    features: List[Dict[str, Any]] = []
    for index, year in enumerate(years):
        day = days[index] if days else 1
        x = 116.30 if identical_picture else 116.30 + 0.12 * index
        y = 39.90 if identical_picture else 39.90 + 0.04 * index
        features.append({
            "type": "Feature", "id": f"obs-{year}",
            "geometry": {"type": "Point", "coordinates": [x, y]},
            "properties": {
                field: _time_value(year, representation, tz, day),
                "label": f"观测{year}", "value": 100 + index,
            },
        })
    sources: Dict[str, Any] = {
        "observations": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": features}},
    }
    layers: List[Dict[str, Any]] = [
        {"id": "observations", "source": "observations", "type": "circle",
         "paint": {"circle-radius": 9, "circle-color": "#dc2626"}},
    ]
    if second_layer:
        second = []
        for index, year in enumerate(years):
            x = 116.80 if identical_picture else 116.80 + 0.10 * index
            second.append({
                "type": "Feature", "id": f"obs-b-{year}",
                "geometry": {"type": "Point", "coordinates": [x, 39.70]},
                "properties": {
                    field: _time_value(year, representation, tz,
                                       days[index] if days else 1),
                    "label": f"B{year}", "value": 200 + index,
                },
            })
        sources["observations_b"] = {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": second}}
        layers.append({"id": "observations_b", "source": "observations_b",
                       "type": "circle",
                       "paint": {"circle-radius": 7, "circle-color": "#1d4ed8"}})

    if granularity == "day":
        assert days is not None
        frames = []
        for year, day in zip(years, days):
            start = datetime(year, 3, day, 0, 0, 0, tzinfo=ZoneInfo(tz))
            end = datetime(year, 3, day + 1, 0, 0, 0, tzinfo=ZoneInfo(tz))
            frames.append({"id": f"f-{year}-{day:02d}", "label": f"{year}-{day:02d}",
                           "start": _iso(start), "end": _iso(end),
                           "feature_count": 1})
    else:
        frames = []
        for year in years:
            start = _frame_start(year, tz)
            end = _frame_start(year + 1, tz)
            frames.append({"id": f"f-{year}", "label": f"{year}",
                           "start": _iso(start), "end": _iso(end),
                           "feature_count": 1})
    temporal = {
        "schema_version": "temporal_view.v1",
        "enabled": True,
        "layers": [{"layer_id": lid} for lid in
                   (["observations", "observations_b"] if second_layer
                    else ["observations"])],
        "time_field": field,
        "time_semantics": "snapshot",
        "time_representation": representation,
        "time_format_hint": "",
        "timezone": tz,
        "granularity": granularity,
        "extent": [frames[0]["start"], frames[-1]["end"]],
        "frames": frames,
        "cursor_frame": frames[0]["id"],
        "playback": {"playing": False, "rate": 1.0},
        "missing_policy": "strict",
        "quality": [],
        "source_ref": "ref:test/temporal-observations",
        "content_revision": 7,
        "user_pinned": False,
        "render_path": "client_filter",
        "disclosures": [],
    }
    return {
        "version": "1.1",
        "sources": sources,
        "layers": layers,
        "view": {"center": [116.5, 39.8], "zoom": 8},
        "layout": {"components": [
            {"id": "title", "type": "title",
             "options": {"text": "年度观测（合成数据 · simulated）"}},
            {"id": "legend", "type": "legend"},
        ]},
        "temporal": temporal,
    }


@pytest.fixture()
def media_env(tmp_path, monkeypatch):
    """隔离 DATA_DIR/exports（owner 侧车、帧缓存、发布产物均落在 tmp）。"""
    from app.core.config import settings
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir))
    return {"data_dir": data_dir, "tmp": tmp_path}


def make_plan(document: Optional[Dict[str, Any]] = None,
              *, owner: str = "session:sess-g35",
              session_id: str = "sess-g35",
              revision: int = 3,
              **kwargs: Any):
    """构造冻结快照 + 能力合同 + 计划（与生产 preview/submit 同一函数）。"""
    doc = document or temporal_document()
    frozen = build_frozen(session_id, doc, revision)
    caps = probe_capabilities()
    args = MediaPlanArgs(session_id=session_id, **kwargs)
    plan = build_plan(frozen, args, owner, caps)
    return frozen, caps, plan


@pytest.fixture()
def small_plan():
    """5 个年度观测、640×360 的小型计划（测试快速且仍走真实编码器）。"""
    _, _, plan = make_plan(width=640, height=360)
    return plan
