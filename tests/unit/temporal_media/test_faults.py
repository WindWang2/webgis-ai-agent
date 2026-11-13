"""WP11/WP06 故障面与资源实测：场景 12/14/15/16/24 + 唯一帧/解码像素/缓存。

不以最终压缩文件大小代替工作量：记录唯一渲染帧数、编码重复/合并段、
独立解码像素、临时磁盘峰值、取消延迟与缓存命中。
"""
from __future__ import annotations

import json
import resource
from pathlib import Path

import pytest

from app.services.temporal_media.capabilities import layer_support, probe_capabilities
from app.services.temporal_media.contracts import (
    MAX_ENCODE_RAW_BYTES, MAX_TOTAL_DISPLAY_MS, MediaPlanArgs, TemporalMediaError,
)
from app.services.temporal_media.freeze import build_frozen, sha256_text
from app.services.temporal_media.planning import build_plan
from app.services.temporal_media.runner import execute_media_job
from app.services.temporal_media.store import (
    initial_state, load_state, save_plan, save_state,
)

from .conftest import make_plan, temporal_document
from .test_pipeline import run_pipeline

PRINCIPAL = "session:sess-g35"


def test_scenario_12_frozen_document_is_immune_to_live_map_changes():
    import copy
    from app.services.temporal_media.freeze import canonical_json

    doc = temporal_document([2019, 2020])
    original = copy.deepcopy(doc)
    _, _, plan = make_plan(doc, width=480, height=270)
    before = plan["frozen_fingerprints"]["document"]
    before_style = plan["frozen_fingerprints"]["style"]
    doc["layers"][0]["paint"]["circle-color"] = "#00ff00"
    doc["view"]["zoom"] = 3
    doc["sources"]["observations"]["inlineData"]["features"].append({
        "type": "Feature", "id": "late", "geometry": None,
        "properties": {"year": 2020},
    })
    assert canonical_json(doc) != canonical_json(original)
    assert canonical_json(plan["frozen"]["document"]) == canonical_json(original)
    assert sha256_text(canonical_json(plan["frozen"]["document"])) == before
    assert plan["frozen_fingerprints"]["style"] == before_style
    assert len(plan["frozen"]["document"]["sources"]["observations"]
               ["inlineData"]["features"]) == 2
    assert len(doc["sources"]["observations"]["inlineData"]["features"]) == 3


def test_T1_frozen_geographic_window_is_fixed_across_all_frames(media_env,
                                                                monkeypatch):
    """P0-1：只有 spec.view.center/zoom 的地图，每帧必须共用同一冻结 bounds。"""
    import app.services.mapspec_to_svg as svg_mod
    from .test_pipeline import run_pipeline

    _, _, plan = make_plan(width=480, height=270)
    assert plan["frozen"]["viewport"]["source"] == "frozen_all_features_union"
    extent = plan["frozen"]["viewport"]["extent"]
    assert len(extent) == 4 and extent[2] > extent[0] and extent[3] > extent[1]
    seen_bounds = []
    original = svg_mod.compile_mapspec_to_svg_detailed

    def spy(*args, **kwargs):
        seen_bounds.append(kwargs.get("bounds"))
        return original(*args, **kwargs)

    monkeypatch.setattr(svg_mod, "compile_mapspec_to_svg_detailed", spy)
    tmp = media_env["tmp"] / "fixed-viewport"
    _, digital, _, _, _, _ = run_pipeline(plan, tmp)
    assert len(seen_bounds) == len(digital) == 5
    assert all(list(b) == extent for b in seen_bounds), seen_bounds
    assert len({tuple(b) for b in seen_bounds}) == 1


def test_T1b_layout_frame_extent_takes_precedence(media_env):
    doc = temporal_document([2019, 2020])
    doc["layout"]["frames"] = [{"id": "f", "enabled": True,
                                "extent": [100.0, 30.0, 120.0, 45.0]}]
    _, _, plan = make_plan(doc, width=480, height=270)
    assert plan["frozen"]["viewport"]["source"] == "layout.frames[].extent"
    assert plan["frozen"]["viewport"]["extent"] == [100.0, 30.0, 120.0, 45.0]


def test_scenario_14_mvt_tile_sample_is_refused_not_claimed_as_full_map():
    doc = temporal_document([2019, 2020])
    doc["sources"]["observations"]["type"] = "mvt"
    doc["sources"]["observations"]["tiles"] = ["https://tiles.example/{z}/{x}/{y}"]
    frozen = build_frozen("sess-g35", doc, 3)
    support = layer_support(doc, frozen["temporal"])
    assert support["unsupported_layers"][0]["reason"] == \
        "mvt_tile_sample_not_full_extent"
    with pytest.raises(TemporalMediaError) as exc:
        build_plan(frozen,
                   MediaPlanArgs(session_id="sess-g35", width=480, height=270),
                   PRINCIPAL, probe_capabilities())
    assert exc.value.code == "BLOCKED_INCOMPLETE"


def test_scenario_15_static_numeric_raster_background_uses_g26_bindings():
    doc = temporal_document([2019, 2020])
    doc["sources"]["background"] = {"type": "numeric_raster",
                                    "ref": "raster:regional"}
    doc["layers"].append({"id": "background", "source": "background",
                          "type": "raster",
                          "raster_style": {"colormap": "viridis"}})
    frozen = build_frozen(
        "sess-g35", doc, 3,
        raster_bindings={"background": {"path": "/bounded/background.tif"}})
    support = layer_support(doc, frozen["temporal"])
    assert support["complete"] is True
    assert support["static_raster_layers"] == ["background"]
    plan = build_plan(
        frozen, MediaPlanArgs(session_id="sess-g35", width=480, height=270),
        PRINCIPAL, probe_capabilities())
    assert plan["frozen"]["raster_bindings"]["background"]["path"] == \
        "/bounded/background.tif"
    assert plan["partial"]["is_partial"] is False


def test_scenario_16_partial_explicitly_removes_unreproducible_layers():
    doc = temporal_document([2019, 2020])
    doc["sources"]["external"] = {"type": "raster",
                                  "tiles": ["https://tiles.example/{z}/{x}/{y}.png"]}
    doc["layers"].append({"id": "external", "source": "external", "type": "raster"})
    _, _, plan = make_plan(doc, width=480, height=270,
                           partial_policy="allow_partial")
    assert plan["partial"]["is_partial"] is True
    from app.services.temporal_media.render import build_compile_doc
    compile_doc, omitted = build_compile_doc(plan)
    assert "external" not in [layer["id"] for layer in compile_doc["layers"]]
    assert omitted[0]["reason"] == "external_tiles_licence_not_embedded"


def test_scenario_24_precheck_caps_are_effective_and_measurable(media_env):
    years = list(range(2019, 2039))
    _, _, moderate = make_plan(temporal_document(years),
                               width=1280, height=720, dwell_ms=1000, fps=4)
    assert moderate["display"]["encoded_frame_count"] == 80
    assert moderate["resource_estimate"]["raw_pixel_bytes"] < MAX_ENCODE_RAW_BYTES
    with pytest.raises(TemporalMediaError) as exc:
        make_plan(temporal_document(years), width=1920, height=1080,
                  dwell_ms=1000, fps=24, timing_policy="proportional")
    assert exc.value.code == "RESOURCE_BUDGET_EXCEEDED"
    assert MAX_ENCODE_RAW_BYTES > 0 and MAX_TOTAL_DISPLAY_MS == 120_000


def test_resource_measurement_unique_frames_vs_encoded_frames(media_env):
    """唯一渲染帧不随 fps 重复增长；记录 RSS/磁盘/解码像素。"""
    _, _, plan = make_plan(width=480, height=270, dwell_ms=1000, fps=4)
    tmp = media_env["tmp"] / "resources"
    plan, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    disk_bytes = sum(p.stat().st_size for p in frames_dir.glob("*.png"))
    assert len({d["digital_frame_id"] for d in digital}) == 5  # 唯一渲染帧
    assert len(encoded) == 20                                  # 编码重复帧
    assert disk_bytes > 0 and disk_bytes < MAX_ENCODE_RAW_BYTES
    from app.services.temporal_media.verify import decode_container
    decoded = decode_container(Path(media["path"]), "apng")
    decoded_pixels = decoded["n_frames"] * 480 * 270
    assert decoded_pixels == 20 * 480 * 270
    assert rss_kb < 2 * 1024 * 1024  # 2 GiB 预算内（实测断言，不是声明）
    (media_env["tmp"] / "resources.json").write_text(json.dumps({
        "unique_rendered_frames": 5, "encoded_frames": len(encoded),
        "container_bytes": media["bytes"], "frame_disk_bytes": disk_bytes,
        "decoded_pixels": decoded_pixels, "peak_rss_kb": rss_kb,
    }, indent=2))


def test_scenario_19_cancel_latency_and_temp_cleanup(media_env):
    import time
    _, _, plan = make_plan(width=480, height=270)
    save_plan(PRINCIPAL, "g35-cancel-latency", plan)
    save_state(PRINCIPAL, "g35-cancel-latency",
               initial_state(PRINCIPAL, "g35-cancel-latency", plan))
    from app.services.temporal_media.store import request_cancel
    start = time.monotonic()
    request_cancel(PRINCIPAL, "g35-cancel-latency")
    receipt = execute_media_job(PRINCIPAL, "g35-cancel-latency",
                                recheck=lambda: None)
    latency = time.monotonic() - start
    assert receipt["status"] == "cancelled"
    assert latency < 5.0
    from app.services.temporal_media.store import job_dir
    assert not (job_dir(PRINCIPAL, "g35-cancel-latency") / "work").exists()
    assert load_state(PRINCIPAL, "g35-cancel-latency")["status"] == "cancelled"


def test_scenario_11_fixed_style_is_not_reclassified_per_frame(media_env):
    """不同年份值域变化：冻结 style/legend 指纹贯穿全部帧，同值同色。"""
    _, _, plan = make_plan(width=480, height=270)
    tmp = media_env["tmp"] / "fixed-style"
    _, digital, encoded, media, frames_dir, svgs_dir = run_pipeline(plan, tmp)
    fingerprints = {d["fingerprints"]["style"] for d in digital}
    assert len(fingerprints) == 1
    assert next(iter(fingerprints)) == plan["frozen_fingerprints"]["style"]
    from app.services.temporal_media.verify import verify_delivery
    assert verify_delivery(plan, encoded, media, digital, frames_dir,
                           svgs_dir)["passed"]
