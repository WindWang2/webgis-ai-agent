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
from app.lib.storymap.camera_planner import (
    ARC_PITCH_BASE,
    build_camera_track,
    plan_camera_for_bbox,
    validate_track,
)
from app.lib.storymap.export_packager import (
    build_story_bundle,
    bundle_to_json,
    render_standalone_html,
    sanitize_dict,
)


# ── 1. 领域模型（spec.py） ──────────────────────────────────────────


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
        assert "<\\/script>" in embedded

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
