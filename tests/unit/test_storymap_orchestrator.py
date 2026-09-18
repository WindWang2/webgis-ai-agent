"""自主 StoryMap 空间叙事编排器单测（ADR-0196）。

覆盖四个验收面：
1. 章节自动合成：8 步分析链路 → 4 个逻辑递进章节 + 每章镜头视角；
2. 关键帧轨迹平滑：贝塞尔/Catmull-Rom 插值无突变、无奇点（含跨 ±180°
   bearing、零长 leg、单关键帧退化）；
3. 离线打包：脱敏 + 自包含单文件 JSON/HTML；
4. API 契约：POST /api/v1/storymap/{compile,export}。
"""
from __future__ import annotations

import json
import math

import pytest

from app.lib.storymap.spec import (
    NARRATIVE_ARC,
    STORYMAP_SPEC_SCHEMA_VERSION,
    AudioNarrative,
    CameraKeyframe,
    StoryMapSpec,
    estimate_duration,
    narration_text,
)
from app.lib.storymap.story_compiler import compile_story_map, normalize_trace
from app.lib.storymap.story_compiler import _payload_bbox
from app.lib.storymap.camera_planner import (
    ARC_PITCH_BASE,
    CameraSample,
    build_camera_track,
    plan_camera_for_bbox,
    validate_track,
)
from app.lib.storymap.export_packager import (
    _embed_json,
    build_story_bundle,
    bundle_to_json,
    render_standalone_html,
    sanitize_dict,
)


# ── 1. 领域模型（spec.py） ──────────────────────────────────────────


def _reject_constant(name: str):
    raise AssertionError(f"non-strict JSON constant in payload: {name}")


class TestStoryMapSpecModel:
    def test_narrative_arc_is_canonical_five_roles(self):
        assert NARRATIVE_ARC == (
            "introduction",
            "macro_situation",
            "focus_dissection",
            "dynamic_simulation",
            "recommendation",
        )

    def test_spec_round_trip_keeps_unknown_keys(self):
        kf = CameraKeyframe(
            chapter_id="arc-introduction", t=0.0,
            center=[116.4, 39.9], zoom=4.0, pitch=10.0, bearing=0.0,
        )
        spec = StoryMapSpec(
            metadata={"title": "专报"},
            chapters=[{"id": "arc-introduction", "title": "引言", "narrative": "正文",
                       "arc_role": "introduction"}],
            camera_keyframes=[kf],
        )
        dumped = spec.model_dump()
        assert dumped["schema_version"] == STORYMAP_SPEC_SCHEMA_VERSION
        # extra="allow"：未知键 round-trip 保留（MapSpec 同纪律）
        spec2 = StoryMapSpec.model_validate({**dumped, "future_field": {"x": 1}})
        assert spec2.model_dump()["future_field"] == {"x": 1}

    def test_pitch_is_capped_at_60_and_bearing_bounded(self):
        with pytest.raises(ValueError):
            CameraKeyframe(chapter_id="c", t=0.0, center=[0, 0], zoom=4,
                           pitch=60.1, bearing=0)
        with pytest.raises(ValueError):
            CameraKeyframe(chapter_id="c", t=0.0, center=[0, 0], zoom=4,
                           pitch=0, bearing=180.5)

    def test_keyframe_chapter_reference_is_validated(self):
        with pytest.raises(ValueError):
            StoryMapSpec(
                metadata={"title": "x"},
                chapters=[{"id": "c1", "title": "t", "narrative": "n",
                           "arc_role": "introduction"}],
                camera_keyframes=[
                    CameraKeyframe(chapter_id="ghost", t=0.0, center=[0, 0], zoom=4)
                ],
            )

    def test_estimate_duration_and_narration_strip_markdown(self):
        md = "## 标题\n- 要点一\n- 要点二\n\n**结论**：[链接](http://x) 正文。"
        text = narration_text(md)
        assert "标题" in text and "要点一" in text
        assert "##" not in text and "](http" not in text
        assert estimate_duration("短") >= 2.0
        assert estimate_duration("汉" * 40) == pytest.approx(10.0)


# ── 2. 章节自动合成（story_compiler.py） ────────────────────────────


def _eight_step_trace() -> dict:
    """8 步分析链路：横跨 4 个叙事弧桶（引言/宏观/解剖/建言）。"""
    return {
        "turn_id": "turn-8",
        "session_id": "s-8",
        "stages": [
            {"stage": "USER_INTENT", "ts": 1.0,
             "user_intent": "分析北京市热岛效应分布"},
            {"stage": "DATA_PROFILE", "ts": 2.0,
             "stats": {"样本数": 12000, "均值℃": 32.5, "热岛强度": 2.8},
             "summary": "覆盖城六区 12000 个网格样本",
             "bbox": [116.0, 39.7, 116.8, 40.1]},
            {"stage": "TOOL_CALLS", "ts": 3.0,
             "tool_name": "hotspot_analysis", "conclusion": "检测到 3 处显著热点"},
            {"stage": "TOOL_RESULTS", "ts": 4.0,
             "conclusion": "热点集中于 CBD 与丰台仓库带",
             "bbox": [116.3, 39.8, 116.6, 40.0]},
            {"stage": "ARTIFACT_CREATION", "ts": 5.0,
             "ref": "ref:chart-heat-1",
             "chart": {"kind": "bar", "data": {"labels": ["CBD", "丰台"],
                                               "values": [3.1, 2.6]}}},
            {"stage": "ARGUMENTS", "ts": 6.0,
             "extent": [116.2, 39.75, 116.7, 40.05],
             "note": "热点核密度带宽 800m"},
            {"stage": "FINAL_VERDICT", "ts": 7.0,
             "verdict": "热岛格局与建成区密度强相关"},
            {"stage": "USER_OUTPUT", "ts": 8.0,
             "final_text": "建议对丰台仓库带实施屋顶绿化改造以缓解热岛。"},
        ],
    }


class TestChapterSynthesis:
    def test_eight_step_trace_yields_four_progressive_chapters(self):
        spec = compile_story_map(trace=_eight_step_trace())
        roles = [ch.arc_role for ch in spec.chapters]
        # 8 步 → 4 章，弧序保序（引言 → 宏观 → 解剖 → 建言；推演桶为空则跳过）
        assert len(spec.chapters) == 4
        assert roles == ["introduction", "macro_situation",
                         "focus_dissection", "recommendation"]
        assert roles == [r for r in NARRATIVE_ARC if r in roles]

    def test_every_chapter_gets_a_camera_view(self):
        spec = compile_story_map(trace=_eight_step_trace())
        kf_by_chapter = {kf.chapter_id: kf for kf in spec.camera_keyframes}
        assert set(kf_by_chapter) == {ch.id for ch in spec.chapters}
        for ch in spec.chapters:
            kf = kf_by_chapter[ch.id]
            assert 0.0 <= kf.pitch <= 60.0
            assert -180.0 <= kf.bearing <= 180.0
            assert 3.0 <= kf.zoom <= 18.0
            # 镜头中心落在该章素材 bbox 邻域（trace 覆盖北京）
            assert 115.5 <= kf.center[0] <= 117.3
            assert 39.3 <= kf.center[1] <= 40.5

    def test_full_arc_trace_yields_five_chapters_in_canonical_order(self):
        trace = _eight_step_trace()
        trace["stages"].insert(5, {"stage_id": 13, "ts": 5.5,
                                   "mutations": ["set_view"],
                                   "bbox": [116.25, 39.78, 116.65, 40.02]})
        spec = compile_story_map(trace=trace)
        roles = [ch.arc_role for ch in spec.chapters]
        assert roles == list(NARRATIVE_ARC)

    def test_stats_and_widgets_are_harvested(self):
        spec = compile_story_map(trace=_eight_step_trace())
        macro = next(c for c in spec.chapters if c.arc_role == "macro_situation")
        assert "12000" in macro.narrative  # 统计摘要进入正文
        dissect = next(c for c in spec.chapters if c.arc_role == "focus_dissection")
        assert dissect.linked_widget_ids, "图表产物应挂接到解剖章"
        widget = next(w for w in spec.linked_widgets
                      if w.id == dissect.linked_widget_ids[0])
        assert widget.kind == "chart" and widget.ref == "ref:chart-heat-1"

    def test_audio_narrative_generated_per_chapter(self):
        spec = compile_story_map(trace=_eight_step_trace())
        assert len(spec.audio_narrative) == len(spec.chapters)
        for a in spec.audio_narrative:
            assert isinstance(a, AudioNarrative)
            assert a.text and a.duration_hint_s >= 2.0

    def test_messages_fallback_produces_two_chapters(self):
        msgs = [
            {"role": "user", "content": "帮我分析上海降水变化"},
            {"role": "assistant", "content": "分析完成：近 30 年降水上升趋势显著。"},
        ]
        spec = compile_story_map(messages=msgs)
        assert [c.arc_role for c in spec.chapters] == ["introduction", "recommendation"]
        assert "降水" in spec.chapters[0].narrative

    def test_trace_and_messages_are_xor_required(self):
        with pytest.raises(ValueError):
            compile_story_map()

    def test_normalize_trace_accepts_stage_enum_names_and_ids(self):
        steps = normalize_trace(_eight_step_trace())
        assert [s.stage_id for s in steps][:3] == [1, 4, 9]
        assert all(s.payload for s in steps)

    def test_replay_trace_shape_is_accepted(self):
        rt = {
            "turn_id": "rt-1",
            "user_input": "做一份洪水风险专报",
            "tool_calls": [{"tool_name": "flood_model", "args": {"return_period": 100}}],
            "artifacts": [{"ref": "ref:chart-risk-9"}],
            "final_text": "百年一遇洪水淹没区主要在城南。",
        }
        spec = compile_story_map(trace=rt)
        roles = [c.arc_role for c in spec.chapters]
        assert roles[0] == "introduction"
        assert roles[-1] == "recommendation"
        assert "城南" in spec.chapters[-1].narrative


# ── 3. 相机规划与轨迹平滑（camera_planner.py） ──────────────────────


class TestCameraPlanning:
    def test_bbox_to_view_center_zoom_pitch(self):
        view = plan_camera_for_bbox([116.0, 39.7, 116.8, 40.1], "macro_situation")
        assert view["center"] == pytest.approx([116.4, 39.9], abs=1e-6)
        assert 3.0 <= view["zoom"] <= 18.0
        assert view["pitch"] == pytest.approx(ARC_PITCH_BASE["macro_situation"], abs=10.0)

    def test_pitch_respects_arc_role_and_stays_under_60(self):
        for role in NARRATIVE_ARC:
            view = plan_camera_for_bbox([116.0, 39.7, 116.8, 40.1], role)
            assert 0.0 <= view["pitch"] <= 60.0, role
        macro = plan_camera_for_bbox([116.0, 39.7, 116.8, 40.1], "macro_situation")
        dissect = plan_camera_for_bbox([116.0, 39.7, 116.8, 40.1], "focus_dissection")
        assert dissect["pitch"] > macro["pitch"], "微观解剖应比宏观态势更倾斜"

    def test_high_latitude_bbox_does_not_inflate_zoom(self):
        low_lat = plan_camera_for_bbox([10.0, 40.0, 11.0, 40.5], "macro_situation")
        high_lat = plan_camera_for_bbox([80.0, 78.0, 81.0, 78.5], "macro_situation")
        # cos 修正后同为 ~1° 跨度，zoom 不应被极区虚高
        assert abs(low_lat["zoom"] - high_lat["zoom"]) < 1.5

    def test_degenerate_bbox_is_clamped(self):
        view = plan_camera_for_bbox([116.4, 39.9, 116.4, 39.9], "focus_dissection")
        assert math.isfinite(view["zoom"]) and math.isfinite(view["center"][0])


def _kf(chapter: str, t: float, center, zoom: float, pitch: float, bearing: float):
    return CameraKeyframe(chapter_id=chapter, t=t, center=list(center),
                          zoom=zoom, pitch=pitch, bearing=bearing)


class TestCameraTrackSmoothing:
    def _sample_chapter_track(self, **kwargs) -> list[dict]:
        kfs = [
            _kf("a", 0.0, [116.0, 39.7], 5.0, 10.0, 0.0),
            _kf("b", 0.0, [116.4, 39.9], 8.0, 40.0, 20.0),
            _kf("c", 0.0, [116.8, 40.1], 12.0, 55.0, -15.0),
        ]
        return build_camera_track(kfs, **kwargs)

    def test_track_is_continuous_without_jumps(self):
        track = self._sample_chapter_track(samples_per_leg=24)
        assert len(track) > 40
        assert validate_track(track, max_center_jump_deg=1.0, max_zoom_jump=1.0,
                              max_pitch_jump=15.0, max_bearing_jump=30.0) == []

    def test_track_time_is_monotonic(self):
        track = self._sample_chapter_track()
        ts = [s.t for s in track]
        assert ts == sorted(ts)
        assert ts[0] == pytest.approx(0.0) and ts[-1] == pytest.approx(1.0)

    def test_samples_reach_every_keyframe_no_nan(self):
        track = self._sample_chapter_track()
        for s in track:
            assert all(math.isfinite(v) for v in s.center)
            assert math.isfinite(s.zoom) and math.isfinite(s.pitch)
            assert -180.0 <= s.bearing <= 180.0
        ends = {(round(s.center[0], 1), round(s.center[1], 1)) for s in (track[0], track[-1])}
        assert (116.0, 39.7) in ends and (116.8, 40.1) in ends

    def test_bearing_takes_shortest_path_across_antimeridian(self):
        kfs = [
            _kf("a", 0.0, [0.0, 0.0], 6.0, 20.0, 170.0),
            _kf("b", 0.0, [1.0, 1.0], 6.0, 20.0, -170.0),
        ]
        track = build_camera_track(kfs, samples_per_leg=20)
        bearings = [s.bearing for s in track]

        def shortest_arc(a: float, b: float) -> float:
            return abs(((b - a + 540.0) % 360.0) - 180.0)

        max_step = max(shortest_arc(a, b) for a, b in zip(bearings, bearings[1:]))
        assert max_step <= 30.0, "跨 ±180° 不允许 340° 大回环"

    def test_duplicate_keyframes_do_not_produce_singularities(self):
        kfs = [
            _kf("a", 0.0, [116.4, 39.9], 8.0, 30.0, 10.0),
            _kf("a", 0.0, [116.4, 39.9], 8.0, 30.0, 10.0),
        ]
        track = build_camera_track(kfs, samples_per_leg=8)
        assert len(track) >= 2
        assert validate_track(track) == []

    def test_single_keyframe_degrades_to_one_sample(self):
        track = build_camera_track([_kf("a", 0.0, [0, 0], 4, 0, 0)])
        assert len(track) == 1

    def test_exactly_two_keyframes_is_valid(self):
        kfs = [_kf("a", 0.0, [0, 0], 4, 0, 0), _kf("b", 0.0, [10, 5], 6, 20, 90)]
        # 10° 长腿需要足够采样密度才能满足默认连续性闸（样本间距 < 1°）
        track = build_camera_track(kfs, samples_per_leg=24)
        assert validate_track(track) == []

    def test_zoom_is_monotone_within_a_leg(self):
        kfs = [_kf("a", 0.0, [0, 0], 4.0, 0.0, 0.0),
               _kf("b", 0.0, [1, 1], 9.0, 30.0, 0.0)]
        track = build_camera_track(kfs, samples_per_leg=16)
        zooms = [s.zoom for s in track]
        assert zooms == sorted(zooms), "缓动单调段内 zoom 不应振荡"


# ── 4. 离线打包（export_packager.py） ───────────────────────────────


def _spec() -> StoryMapSpec:
    return compile_story_map(trace=_eight_step_trace())


class TestExportPackaging:
    def test_bundle_manifest_counts(self):
        bundle = build_story_bundle(_spec())
        m = bundle["manifest"]
        assert m["chapter_count"] == 4
        assert m["widget_count"] == len(bundle["spec"]["linked_widgets"])
        assert bundle["schema_version"] == "storybundle-1"
        assert bundle["data"]["layers"] == []

    def test_sanitize_redacts_secret_keys_recursively(self):
        dirty = {"layers": [{"name": "l1", "api_key": "sk-123", "nested":
                 {"access_token": "t", "token": "x", "safe": 1}}]}
        clean = sanitize_dict(dirty)
        assert clean["layers"][0]["api_key"] == "REDACTED"
        assert clean["layers"][0]["nested"]["access_token"] == "REDACTED"
        assert clean["layers"][0]["nested"]["token"] == "REDACTED"
        assert clean["layers"][0]["nested"]["safe"] == 1

    def test_bundle_strips_secrets_by_default(self):
        spec_dict = _spec().model_dump()
        spec_dict["metadata"]["owner_token"] = "ot-42"
        bundle = build_story_bundle(StoryMapSpec.model_validate(spec_dict))
        text = json.dumps(bundle, ensure_ascii=False)
        assert "ot-42" not in text and "REDACTED" in text

    def test_single_file_html_is_self_contained(self):
        html = render_standalone_html(build_story_bundle(_spec()))
        assert html.startswith("<!DOCTYPE html>")
        assert 'id="story-bundle"' in html
        # 离线自包含：不允许任何外链引用
        assert "http://" not in html.replace("http://www.w3.org", "")
        assert "https://" not in html
        assert "<cdn" not in html.lower() and "src=" not in html.split("story-bundle")[0].split("<!DOCTYPE")[-1]

    def test_html_escapes_script_closing_tag(self):
        spec_dict = _spec().model_dump()
        spec_dict["chapters"][0]["narrative"] = "正文含 </script> 注入尝试"
        html = render_standalone_html(build_story_bundle(
            StoryMapSpec.model_validate(spec_dict)))
        embedded = html.split('id="story-bundle">', 1)[1].split("</script>", 1)[0]
        assert "</script>" not in embedded
        # 新方案：`<` 统一转义为合法 JSON 转义 \u003c（可无损还原）
        assert "\\u003c/script" in embedded
        assert json.loads(embedded)["spec"]["chapters"][0]["narrative"] == \
            "正文含 </script> 注入尝试"

    def test_json_bundle_round_trip(self):
        bundle = build_story_bundle(_spec())
        text = bundle_to_json(bundle)
        parsed = json.loads(text)
        assert parsed["spec"]["metadata"]["title"]
        assert parsed["manifest"]["chapter_count"] == 4

    def test_layers_and_mapspec_are_embedded(self):
        fc = {"type": "FeatureCollection",
              "features": [{"type": "Feature", "geometry": {"type": "Point",
                            "coordinates": [116.4, 39.9]}, "properties": {}}]}
        bundle = build_story_bundle(_spec(), layers=[fc],
                                    mapspec={"version": "1.2"})
        assert bundle["data"]["mapspec"] == {"version": "1.2"}
        assert bundle["manifest"]["layer_count"] == 1
        assert bundle["manifest"]["feature_count"] == 1


# ── 5. 服务壳与 API 契约 ────────────────────────────────────────────


class TestServiceHelpers:
    def test_bbox_from_geojson(self):
        from app.services.storymap import bbox_from_geojson

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [116.0, 39.7]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [116.8, 40.1]}, "properties": {}},
        ]}
        assert bbox_from_geojson(fc) == [116.0, 39.7, 116.8, 40.1]
        assert bbox_from_geojson({"type": "FeatureCollection", "features": []}) is None
        assert bbox_from_geojson(None) is None


@pytest.fixture
async def client():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.routes import storymap as storymap_routes

    app = FastAPI()
    app.include_router(storymap_routes.router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStorymapApi:
    async def test_compile_stateless_returns_spec(self, client):
        resp = await client.post("/api/v1/storymap/compile",
                                 json={"trace": _eight_step_trace()})
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == STORYMAP_SPEC_SCHEMA_VERSION
        assert len(body["chapters"]) == 4
        assert {kf["chapter_id"] for kf in body["camera_keyframes"]} == \
            {ch["id"] for ch in body["chapters"]}

    async def test_compile_requires_trace_or_messages(self, client):
        resp = await client.post("/api/v1/storymap/compile", json={})
        assert resp.status_code == 422

    async def test_compile_rejects_empty_payload_shape(self, client):
        resp = await client.post("/api/v1/storymap/compile",
                                 json={"messages": []})
        assert resp.status_code == 422

    async def test_export_json_bundle(self, client):
        spec = _spec().model_dump()
        resp = await client.post("/api/v1/storymap/export",
                                 json={"spec": spec, "format": "json"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == "storybundle-1"
        assert body["manifest"]["chapter_count"] == 4

    async def test_export_html_is_downloadable(self, client):
        spec = _spec().model_dump()
        resp = await client.post("/api/v1/storymap/export",
                                 json={"spec": spec, "format": "html"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.text.startswith("<!DOCTYPE html>")


# ── 6. 对抗评审加固（P1 XSS / bbox 全顶点 / antimeridian / 非有限数） ──


class TestReviewHardeningBackend:
    """独立对抗评审发现面的回归锁（每项先证伪旧实现）。"""

    def test_html_viewer_never_injects_raw_payload_tags(self):
        """P1：查看器拼接（innerHTML）必须转义数据面，且 JSON 内嵌用 \u003c。"""
        hostile = "<img src=x onerror=alert(1)>"
        spec = compile_story_map(messages=[
            {"role": "user", "content": hostile},
            {"role": "assistant", "content": "ok"},
        ])
        html = render_standalone_html(build_story_bundle(spec))
        # 数据面 `<` 以内嵌转义形态驻留，HTML 原文不得出现可执行的原始载荷
        assert "<img" not in html
        assert "onerror=alert(1)>" not in html
        # 查看器渲染路径必须走 esc() 转义函数（防未来把 \u003c 换回原样）
        assert "esc(" in html
        assert "+ ch.narrative +" not in html

    def test_embedded_json_round_trips_strictly_for_hostile_text(self):
        """P2-6：`<!--` / `</script>` 载荷不得破坏内嵌 JSON（\u003c 合法转义）。"""
        payload = {
            "narrative": "note <!-- comment --> and </script> and <img onerror=x> tail",
            "nested": ["<a>", "b<!--c"],
        }
        embedded = _embed_json(payload)
        # 严格 JSON（禁 NaN 等宽松项）必须无损还原
        parsed = json.loads(embedded, parse_constant=_reject_constant)
        assert parsed == payload
        assert "</script" not in embedded and "<!--" not in embedded

    def test_polygon_and_linestring_bbox_cover_all_vertices(self):
        """P2-4：GeoJSON 扫描必须收集全部顶点（旧实现只取首点）。"""
        poly = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[116, 39], [117, 39], [117, 40], [116, 40], [116, 39]]]},
        }]}
        assert _payload_bbox(poly) == [116.0, 39.0, 117.0, 40.0]

        line = {"type": "Feature", "properties": {},
                "geometry": {"type": "LineString", "coordinates": [[10, 10], [12, 14]]}}
        assert _payload_bbox(line) == [10.0, 10.0, 12.0, 14.0]

        from app.services.storymap import bbox_from_geojson
        assert bbox_from_geojson(poly) == [116.0, 39.0, 117.0, 40.0]
        assert bbox_from_geojson(
            {"type": "FeatureCollection", "features": [line]}
        ) == [10.0, 10.0, 12.0, 14.0]

        multi = {"type": "MultiPolygon", "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 0]]],
            [[[5, 5], [6, 5], [6, 6], [5, 5]]],
        ]}
        assert _payload_bbox(multi) == [0.0, 0.0, 6.0, 6.0]

    def test_antimeridian_bbox_keeps_view_in_pacific(self):
        """P2-2：跨 ±180° 经线的 bbox 不得把相机丢到大西洋。"""
        view = plan_camera_for_bbox([170.0, -10.0, -170.0, 10.0], "macro_situation")
        assert abs(view["center"][0]) >= 170.0
        assert 3.0 <= view["zoom"] <= 18.0

    def test_center_with_zoom_synthesizes_span(self):
        """P2-3：center+zoom 载荷必须按 zoom 合成观察范围（旧实现钉死 zoom=18）。"""
        bbox = _payload_bbox({"center": [116.4, 39.9], "zoom": 11})
        assert bbox is not None
        w, s, e, n = bbox
        assert e - w == pytest.approx(360.0 / 2 ** 10, rel=1e-6)
        spec = compile_story_map(trace={"stages": [
            {"stage": 14, "center": [116.4, 39.9], "zoom": 11},
        ]})
        kf = spec.camera_keyframes[0]
        assert kf.zoom == pytest.approx(11.0, abs=0.6)

    def test_non_finite_and_null_inputs_never_crash(self):
        """P2-5：NaN/Infinity/None 输入 → 显式安全值或 ValueError，绝不 NaN 穿透。"""
        assert _payload_bbox({"bbox": [float("nan"), 0, 1, 1]}) is None
        assert _payload_bbox({"center": [float("inf"), 0], "zoom": 10}) is None
        view = plan_camera_for_bbox([0, 0, 1, 1], "macro_situation")
        assert all(math.isfinite(view[k]) for k in ("zoom", "pitch", "bearing"))
        with pytest.raises(ValueError):
            plan_camera_for_bbox([float("nan"), 0, 1, 1], "macro_situation")
        with pytest.raises(ValueError):
            normalize_trace({"stages": [{"stage": 4, "ts": None}]})
        with pytest.raises(ValueError):
            normalize_trace({"stages": [{"stage": 4, "ts": "abc"}]})

    def test_camera_keyframe_rejects_non_finite(self):
        with pytest.raises(ValueError):
            CameraKeyframe(chapter_id="c", t=0.0, center=[float("nan"), 0], zoom=4)
        with pytest.raises(ValueError):
            CameraKeyframe(chapter_id="c", t=0.0, center=[0, 0], zoom=float("inf"))

    def test_duplicate_chapter_ids_rejected(self):
        with pytest.raises(ValueError):
            StoryMapSpec(
                metadata={"title": "x"},
                chapters=[
                    {"id": "dup", "title": "a", "narrative": "n", "arc_role": "introduction"},
                    {"id": "dup", "title": "b", "narrative": "m", "arc_role": "recommendation"},
                ],
            )

    def test_empty_trace_buckets_fall_back_to_messages(self):
        """P3-1：trace 有形状但桶全空且给了 messages → 降级两章（规格承诺）。"""
        spec = compile_story_map(
            trace={"stages": []},
            messages=[
                {"role": "user", "content": "帮我分析上海降水"},
                {"role": "assistant", "content": "结论：上升趋势显著。"},
            ],
        )
        assert [c.arc_role for c in spec.chapters] == ["introduction", "recommendation"]

    def test_validate_track_is_a_live_gate_not_a_tautology(self):
        """P2-9 正向用例：真实突变必须被判违规（既有 39 例全是 ==[] 从未证明会响）。"""
        jumped = build_camera_track(
            [_kf("a", 0.0, [0, 0], 4, 0, 0), _kf("b", 0.0, [10, 5], 6, 20, 90)],
            samples_per_leg=64,
        )
        clean = build_camera_track(
            [_kf("a", 0.0, [0, 0], 4, 0, 0), _kf("b", 0.0, [10, 5], 6, 20, 90)],
            samples_per_leg=8,
        )
        assert validate_track(jumped) == []
        # 低采样密度下的同一条轨迹会触发阈值 —— 记录该已知耦合（文档已注明）
        assert validate_track(clean) != []
        # 手工注入 5° 突跳的轨迹必须被逮住
        track = [CameraSample(0.0, [0, 0], 4, 0, 0), CameraSample(0.5, [5, 3], 5, 10, 0)]
        assert validate_track(track) != []

    def test_message_content_none_does_not_render_literal_none(self):
        spec = compile_story_map(messages=[
            {"role": "user", "content": None},
            {"role": "assistant", "content": "ok"},
        ])
        assert "None" not in spec.chapters[0].narrative

    def test_extreme_numeric_boundaries_stay_in_contract(self):
        """#1364：大整数/极值 zoom 不得 OverflowError/ZeroDivision → 500。"""
        huge = 10 ** 400
        assert _payload_bbox({"bbox": [huge, 0, 1, 1]}) is None
        assert _payload_bbox({"coordinates": [[huge, 39]]}) is None
        plus = _payload_bbox({"center": [116.4, 39.9], "zoom": 1e308})
        minus = _payload_bbox({"center": [116.4, 39.9], "zoom": -1e308})
        assert plus is not None and minus is not None
        for box in (plus, minus):
            assert all(math.isfinite(v) for v in box)
        with pytest.raises(ValueError):
            normalize_trace({"stages": [{"stage": 4, "ts": huge}]})

    def test_deep_payload_fails_closed_with_value_error(self):
        """#1365：深度门卫在递归遍历前拒绝载荷。"""
        deep = {"value": 1}
        for _ in range(1500):
            deep = {"nested": deep}
        with pytest.raises(ValueError, match="maximum nesting depth"):
            compile_story_map(trace={"stages": [{"stage": 4, "payload": deep}]})
        with pytest.raises(ValueError, match="maximum nesting depth"):
            compile_story_map(messages=[{"role": "user", "content": deep}])

    def test_lone_surrogates_are_cleaned_before_serialization(self):
        """#1366：\\ud800 不能穿透到 UTF-8/JSON 序列化边界。"""
        spec = compile_story_map(
            title="标题 \ud800",
            messages=[
                {"role": "user", "content": "问题 \ud800"},
                {"role": "assistant", "content": "结论 \ud800"},
            ],
        )
        dumped = json.dumps(spec.model_dump(), ensure_ascii=False)
        dumped.encode("utf-8")
        assert "\ud800" not in dumped
        bundle = build_story_bundle(spec, layers=[{"note": "layer \ud800"}])
        json.dumps(bundle, ensure_ascii=False).encode("utf-8")

    def test_sensitive_keys_match_token_boundaries_not_substrings(self):
        """#1368：真实敏感键命中；capital/author/rapid/therapist 不误杀。"""
        dirty = {
            "api_key": "1", "access_key": "2", "signing_key": "3",
            "encryption_key": "4", "AccessKeyId": "5",
            "capital": "Paris", "author": "Ada", "rapid": "fast",
            "therapist": "Lee", "camera_keyframes": [{"id": "k"}],
        }
        clean = sanitize_dict(dirty)
        for key in ("api_key", "access_key", "signing_key",
                    "encryption_key", "AccessKeyId"):
            assert clean[key] == "REDACTED", key
        assert clean["capital"] == "Paris"
        assert clean["author"] == "Ada"
        assert clean["rapid"] == "fast"
        assert clean["therapist"] == "Lee"
        assert clean["camera_keyframes"] == [{"id": "k"}]

    def test_session_id_is_explicitly_redacted_from_export_bundle(self):
        """#1368：metadata.session_id 策略是脱敏，并有导出测试锁定。"""
        spec = compile_story_map(messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ], session_id="sess-sensitive")
        bundle = build_story_bundle(spec)
        assert bundle["spec"]["metadata"]["session_id"] == "REDACTED"
        assert "sess-sensitive" not in json.dumps(bundle, ensure_ascii=False)

    def test_html_placeholders_are_filled_single_pass(self):
        """#1372：title 中的占位符字面量不得二次替换模板槽位。"""
        title = "研究 __VIEWER__ 与 __JSON__ 和 __TITLE__ 的字面量"
        spec = compile_story_map(title=title, messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ])
        html = render_standalone_html(build_story_bundle(spec))
        assert f"<title>{title} · 离线专报</title>" in html
        assert html.count('id="story-bundle"') == 1
        assert html.count("function esc(value)") == 1
        embedded = html.split('id="story-bundle">', 1)[1].split("</script>", 1)[0]
        assert json.loads(embedded)["spec"]["metadata"]["title"] == title

    def test_empty_bucket_fallback_summary_matches_messages_path(self):
        """#1373：空桶 + messages 的 summary 与 messages-only 相同。"""
        messages = [
            {"role": "user", "content": "分析上海降水"},
            {"role": "assistant", "content": "结论：上升趋势显著。"},
        ]
        fallback = compile_story_map(trace={"stages": []}, messages=messages)
        direct = compile_story_map(messages=messages)
        assert fallback.metadata.summary == direct.metadata.summary == \
            "结论：上升趋势显著。"

    def test_payload_bbox_scans_each_step_once(self, monkeypatch):
        """#1374：会话并集与章节并集复用同一次 payload bbox 提取。"""
        import app.lib.storymap.story_compiler as compiler

        calls = {"payload": 0, "coords": 0}
        real_payload_bbox = compiler._payload_bbox
        real_iter = compiler.iter_coord_points

        def count_payload(payload):
            calls["payload"] += 1
            return real_payload_bbox(payload)

        def count_iter(value, seen=None):
            if seen is None:  # 只计 _payload_bbox 的顶层遍历；递归子调用不计
                calls["coords"] += 1
            return real_iter(value, seen)

        trace = _eight_step_trace()
        for stage in trace["stages"]:
            stage.pop("bbox", None)
            stage.pop("extent", None)
            stage.pop("center", None)
            stage.pop("zoom", None)
            stage["feature"] = {"coordinates": [[116.0, 39.0], [117.0, 40.0]]}

        monkeypatch.setattr(compiler, "_payload_bbox", count_payload)
        monkeypatch.setattr(compiler, "iter_coord_points", count_iter)
        spec = compiler.compile_story_map(trace=trace)
        assert spec.chapters
        assert calls["payload"] == len(trace["stages"])
        assert calls["coords"] == len(trace["stages"])

    def test_bool_and_non_finite_ts_are_rejected(self):
        """#1375：显式 bool/None/NaN/Inf ts 一律 ValueError；键缺失用序号兜底。"""
        for bad in (True, False, None, float("nan"), float("inf"), "soon"):
            with pytest.raises(ValueError):
                normalize_trace({"stages": [{"stage": 4, "ts": bad}]})
        steps = normalize_trace({"stages": [{"stage": 4}, {"stage": 5}]})
        assert [s.ts for s in steps] == [0.0, 1.0]

    def test_empty_and_blank_chapter_ids_rejected(self):
        """#1375：空串/纯空白 chapter id 与 camera 引用一并拒绝。"""
        from pydantic import ValidationError

        base = {
            "chapters": [{
                "id": "", "title": "t", "narrative": "n",
                "arc_role": "introduction",
            }],
        }
        with pytest.raises(ValidationError):
            StoryMapSpec.model_validate(base)
        base["chapters"][0]["id"] = "   "
        with pytest.raises(ValidationError):
            StoryMapSpec.model_validate(base)

    def test_structured_stats_render_as_compact_json_not_repr(self):
        """#1375：结构化 stats 值渲染紧凑 JSON（无双引号 Python repr）。"""
        trace = {"stages": [{
            "stage": 4,
            "stats": {"by_region": {"east": 3, "west": 5}, "top": ["a", "b"]},
        }]}
        spec = compile_story_map(trace=trace)
        narrative = spec.chapters[0].narrative
        assert '"east":3' in narrative and '["a","b"]' in narrative
        assert "'" not in narrative.split("by_region", 1)[1]


class TestStorymapApiHardening:
    """API 级加固契约：畸形输入必须 4xx，NaN 优雅兜底（绝不 500）。"""

    async def test_compile_rejects_unusable_ts_with_422_not_500(self, client):
        """P2-5：显式 null/字符串 ts 必须 422（旧实现 None → TypeError 500）。"""
        for bad_ts in (None, "abc"):
            resp = await client.post(
                "/api/v1/storymap/compile",
                json={"trace": {"stages": [{"stage": 4, "ts": bad_ts}]}},
            )
            assert resp.status_code == 422, f"ts={bad_ts!r} → {resp.status_code}"

    async def test_compile_tolerates_non_finite_bbox_with_fallback_camera(self, client):
        """P2-5：NaN bbox 被过滤 → 200 + 有限兜底相机（绝不 NaN 穿透 500）。

        httpx 的 json= 用 allow_nan=False 客户端即拒 NaN —— 以原始 JSON 文本
        发 `NaN` 字面量（Python json.loads 服务端接受），还原真实攻击面。
        """
        raw = '{"trace": {"stages": [{"stage": 4, "bbox": [NaN, 0, 1, 1]}]}}'
        resp = await client.post(
            "/api/v1/storymap/compile",
            content=raw,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        kf = resp.json()["camera_keyframes"][0]
        assert all(isinstance(v, (int, float)) for v in kf["center"])

    async def test_compile_rejects_deep_payload_with_422_not_500(self, client):
        """#1365：~1500 层 trace/messages 在 compile 上是确定性 4xx。"""
        deep = {"value": 1}
        for _ in range(1500):
            deep = {"nested": deep}
        resp = await client.post(
            "/api/v1/storymap/compile",
            json={"trace": {"stages": [{"stage": 4, "payload": deep}]}},
        )
        assert resp.status_code == 422
        resp = await client.post(
            "/api/v1/storymap/compile",
            json={"messages": [{"role": "user", "content": deep}]},
        )
        assert resp.status_code == 422

    async def test_export_rejects_deep_spec_with_422_for_json_and_html(self, client):
        """#1365：同一深度策略在 json/html 两个导出形态上一致。"""
        spec = _spec().model_dump()
        deep = {"value": 1}
        for _ in range(1500):
            deep = {"nested": deep}
        spec["extra"] = deep
        for fmt in ("json", "html"):
            resp = await client.post(
                "/api/v1/storymap/export",
                json={"spec": spec, "format": fmt},
            )
            assert resp.status_code == 422, fmt

    async def test_compile_and_export_clean_lone_surrogates(self, client):
        """#1366：JSON 转义进入的 \\ud800 在 compile/export 上都不 500。"""
        raw = (
            '{"messages":['
            '{"role":"user","content":"问题 \\ud800"},'
            '{"role":"assistant","content":"结论 \\ud800"}],'
            '"title":"标题 \\ud800"}'
        )
        resp = await client.post(
            "/api/v1/storymap/compile",
            content=raw,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        spec = resp.json()
        for fmt in ("json", "html"):
            out = await client.post(
                "/api/v1/storymap/export",
                json={"spec": spec, "format": fmt},
            )
            assert out.status_code == 200, fmt

    async def test_extreme_zoom_and_bigints_do_not_500(self, client):
        """#1364：center+zoom ±1e308、大整数 ts 都在契约内处理。"""
        for zoom in (1e308, -1e308):
            resp = await client.post(
                "/api/v1/storymap/compile",
                json={"trace": {"stages": [{
                    "stage": 4, "center": [116.4, 39.9], "zoom": zoom,
                }]}},
            )
            assert resp.status_code == 200
            kf = resp.json()["camera_keyframes"][0]
            assert all(isinstance(v, (int, float)) for v in kf["center"])
            assert all(math.isfinite(v) for v in
                       (kf["zoom"], kf["pitch"], kf["bearing"]))
        resp = await client.post(
            "/api/v1/storymap/compile",
            json={"trace": {"stages": [{"stage": 4, "ts": 10 ** 400}]}},
        )
        assert resp.status_code == 422
