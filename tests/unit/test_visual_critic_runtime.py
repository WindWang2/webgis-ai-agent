"""VLM Visual Critic Runtime 契约测试（ADR-0185 / docs/dev/vlm-visual-critic-spec.md）。

覆盖面（TDD 先行，实现于 app/lib/harness/visual_judge/）：

1. contracts —— Pydantic 严格模式（extra=forbid / 白名单维度 / bbox 值域次序 /
   置信度数值域 / report 状态一致性）；
2. snapshot_extractor —— 解码/魔数/尺寸/哈希/灰度直方图初筛与 fail-closed；
3. vlm_provider —— OpenAI 兼容 / Gemini 双通道请求组装 + 严密 JSON-schema
   + 输出解析；
4. fake_vlm / golden_images —— 10 黄金样本确定性 Mock 体系；
5. critic_engine —— fail-closed 矩阵（损坏图/超时/无 key/坏输出/超限）、
   (session_id, mapspec_fingerprint, image_sha256) 记忆化幂等（单轮至多一次
   外呼）、评分推导；
6. visual_evaluator 挂接 —— opt-in 开关、judge 优先级、record-only 不变、
   L5 derive_goal_satisfaction 消费面兼容。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app.lib.harness import visual_evaluator as ve
from app.lib.harness.visual_judge import (
    GOLDEN_SAMPLES,
    VisualBBox,
    VisualCritiqueItem,
    VisualDimension,
    VisualDimensionScore,
    VisualJudgeReport,
    SnapshotExtractor,
    SnapshotError,
    FakeVLMClient,
    render_golden_image,
    build_openai_request,
    build_gemini_request,
    gemini_endpoint,
    parse_critic_output,
    CriticOutputError,
    CriticProviderError,
    CriticTimeout,
    GeminiVLMClient,
    OpenAICompatVLMClient,
    VLMRequest,
    VisualCriticEngine,
    build_critic_engine,
    visual_critic_runtime_enabled,
)

# ── 公共夹具 ─────────────────────────────────────────────────────────────

_VISUAL_ENV_KEYS = (
    "CARTO_VISUAL_CRITIC_RUNTIME",
    "CARTO_VISUAL_JUDGE",
    "CARTO_VISUAL_JUDGE_VLM",
    "CARTO_VISUAL_JUDGE_SCREENSHOT",
    "CARTO_VISUAL_JUDGE_MODEL",
    "CARTO_VISUAL_JUDGE_MODE",
    "CARTO_VISUAL_JUDGE_PROVIDER",
    "CARTO_VISUAL_JUDGE_BASE_URL",
    "CARTO_VISUAL_JUDGE_API_KEY",
    "CARTO_VISUAL_JUDGE_TIMEOUT_S",
    "CARTO_VISUAL_CRITIC_MAX_IMAGE_BYTES",
    "CARTO_VISUAL_CRITIC_MIN_EDGE_PX",
)


@pytest.fixture(autouse=True)
def _clean_visual_env(monkeypatch):
    """视觉裁判 env 全清（存量 selfheal 套件同纪律）+ 记忆化隔离。"""
    for name in _VISUAL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    ve._judge_memo.clear()


def _png_bytes(color=(200, 30, 30), size=(96, 72)) -> bytes:
    """确定性合成 PNG（默认尺寸满足 min_edge=64）。"""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _make_cartography(fingerprint: str = "fp-test-1"):
    return SimpleNamespace(
        session_id="sess-1",
        mapspec_fingerprint=fingerprint,
        status="passed",
        desired_status="passed",
        visual_evidence=[],
        checks=[],
    )


def _engine_with(sample: str | None = None, **kwargs) -> tuple[VisualCriticEngine, FakeVLMClient]:
    client = FakeVLMClient(sample=sample, **kwargs)
    return VisualCriticEngine(client), client


# ── 1. contracts：Pydantic 严格契约 ──────────────────────────────────────


class TestContracts:
    def test_dimension_whitelist_is_five_fixed_values(self):
        assert {d.value for d in VisualDimension} == {
            "readability",
            "color_discriminability",
            "composition_balance",
            "information_density",
            "spatial_alignment",
        }

    def test_critique_item_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            VisualCritiqueItem.model_validate({
                "dimension": "readability",
                "severity": "error",
                "suggestion": "x",
                "confidence": 0.9,
                "mutations": [{"op": "delete_layer"}],  # 改图意图 ⇒ 拒收
            })

    def test_critique_item_rejects_non_whitelisted_dimension(self):
        with pytest.raises(ValidationError):
            VisualCritiqueItem.model_validate({
                "dimension": "polish_completeness",  # legacy 维度不进 v2 白名单
                "suggestion": "x",
                "confidence": 0.5,
            })

    def test_critique_item_rejects_bool_confidence(self):
        with pytest.raises(ValidationError):
            VisualCritiqueItem.model_validate({
                "dimension": "readability",
                "suggestion": "x",
                "confidence": True,
            })

    def test_critique_item_truncates_long_strings(self):
        item = VisualCritiqueItem.model_validate({
            "dimension": "readability",
            "suggestion": "s" * 5000,
            "evidence": "e" * 5000,
            "defect_type": "d" * 500,
            "confidence": 0.7,
        })
        assert len(item.suggestion) == 300
        assert len(item.evidence) == 300
        assert len(item.defect_type) == 60

    def test_bbox_accepts_sequence_and_validates_order_and_range(self):
        bbox = VisualBBox.model_validate([10.0, 20.0, 40.0, 80.0])
        assert (bbox.ymin, bbox.xmin, bbox.ymax, bbox.xmax) == (10.0, 20.0, 40.0, 80.0)
        with pytest.raises(ValidationError):  # ymax <= ymin
            VisualBBox.model_validate([40.0, 20.0, 10.0, 80.0])
        with pytest.raises(ValidationError):  # 越界
            VisualBBox.model_validate([-5.0, 0.0, 30.0, 80.0])
        with pytest.raises(ValidationError):  # 非 4 元
            VisualBBox.model_validate([1.0, 2.0, 3.0])
        with pytest.raises(ValidationError):  # 非数值元素
            VisualBBox.model_validate(["a", "b", "c", "d"])
        with pytest.raises(ValidationError):  # 非 4 元且含 bool
            VisualBBox.model_validate([True, 2.0, 3.0, 4.0])

    def test_dimension_score_rationale_is_truncated(self):
        score = VisualDimensionScore(
            dimension="readability", score=5.0, confidence=0.5,
            rationale="r" * 5000)
        assert len(score.rationale) == 300

    def test_dimension_score_bounds(self):
        with pytest.raises(ValidationError):
            VisualDimensionScore(dimension="readability", score=11.0, confidence=0.5)
        with pytest.raises(ValidationError):
            VisualDimensionScore(dimension="readability", score=5.0, confidence=1.5)

    def test_report_not_evaluated_requires_reason_and_empty_critiques(self):
        report = VisualJudgeReport(
            status="not_evaluated", reason="provider_timeout", session_id="s",
            mapspec_fingerprint="f", image_sha256="", image_width=0, image_height=0,
        )
        assert report.critiques == []
        with pytest.raises(ValidationError):  # 缺 reason
            VisualJudgeReport(status="not_evaluated", reason="", session_id="s",
                              mapspec_fingerprint="f", image_sha256="",
                              image_width=0, image_height=0)
        with pytest.raises(ValidationError):  # not_evaluated 不得携带批评
            VisualJudgeReport(status="not_evaluated", reason="x",
                              critiques=[VisualCritiqueItem(
                                  dimension="readability", suggestion="s",
                                  confidence=0.5)],
                              session_id="s", mapspec_fingerprint="f",
                              image_sha256="", image_width=0, image_height=0)

    def test_report_evaluated_requires_five_dimension_scores(self):
        base = dict(session_id="s", mapspec_fingerprint="f", image_sha256="abc",
                    image_width=100, image_height=100)
        with pytest.raises(ValidationError):
            VisualJudgeReport(status="evaluated", dimension_scores=[], **base)
        scores = [
            VisualDimensionScore(dimension=d, score=10.0, confidence=0.0)
            for d in VisualDimension
        ]
        report = VisualJudgeReport(status="evaluated", dimension_scores=scores, **base)
        assert report.overall_confidence == 0.0
        assert not report.has_blocking_defects

    def test_report_summary_is_bounded_and_l5_compatible(self):
        scores = [VisualDimensionScore(dimension=d, score=6.0, confidence=0.9)
                  for d in VisualDimension]
        report = VisualJudgeReport(
            status="evaluated",
            critiques=[VisualCritiqueItem(
                dimension="readability", severity="error", suggestion="labels overlap",
                confidence=0.9, bbox=VisualBBox(ymin=10, xmin=10, ymax=30, xmax=40),
                defect_type="overlapping_labels")],
            dimension_scores=scores, overall_score=6.0, overall_confidence=0.9,
            session_id="s", mapspec_fingerprint="f", image_sha256="abc",
            image_width=640, image_height=480, provider="fake", model="fake-vlm",
        )
        summary = report.to_summary()
        assert summary["source"] == "visual_judge"     # L5 消费面兼容键
        assert summary["status"] == "evaluated"
        assert summary["error_count"] == 1
        assert summary["warning_count"] == 0
        assert summary["runtime"] == "visual_critic_v2"
        assert len(json.dumps(summary)) < 8192         # 有界
        assert report.has_blocking_defects


# ── 2. snapshot_extractor：快照管线 ──────────────────────────────────────


class TestSnapshotExtractor:
    def test_valid_png_extracts_fingerprint_and_dimensions(self):
        data = _png_bytes()
        snap = SnapshotExtractor().extract(data)
        assert snap.image_sha256 == hashlib.sha256(data).hexdigest()
        assert (snap.width, snap.height) == (96, 72)
        assert snap.mime == "image/png"
        assert snap.data_url.startswith("data:image/png;base64,")
        assert "hints" in snap.pre_screen

    def test_accepts_data_url_input(self):
        data = _png_bytes()
        snap = SnapshotExtractor().extract(_data_url(data))
        assert snap.image_sha256 == hashlib.sha256(data).hexdigest()

    def test_rejects_empty_bytes(self):
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor().extract(b"")
        assert ei.value.reason == "image_empty"

    def test_rejects_corrupt_bytes(self):
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor().extract(b"this is not an image at all")
        assert ei.value.reason == "image_corrupt"

    def test_rejects_truncated_png(self):
        data = _png_bytes()
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor().extract(data[: len(data) // 3])
        assert ei.value.reason == "image_corrupt"

    def test_rejects_invalid_base64_text(self):
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor().extract("data:image/png;base64,@@@not-base64@@@")
        assert ei.value.reason == "image_corrupt"

    def test_rejects_oversized_bytes(self):
        data = _png_bytes()
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor(max_bytes=16).extract(data)
        assert ei.value.reason == "image_oversized"

    def test_rejects_too_small_image(self):
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor().extract(_png_bytes(size=(8, 8)))
        assert ei.value.reason == "image_too_small"

    def test_pre_screen_flags_low_contrast_and_blank(self):
        flat = SnapshotExtractor().extract(_png_bytes(color=(128, 128, 128)))
        assert "low_contrast" in flat.pre_screen["hints"]
        dark = SnapshotExtractor().extract(_png_bytes(color=(5, 5, 5)))
        assert "extreme_dark" in dark.pre_screen["hints"]
        noisy = SnapshotExtractor().extract(render_golden_image("clean_balanced_map"))
        assert noisy.pre_screen["hints"] == []  # 正常图不误报
        assert noisy.pre_screen["p5_p95_spread"] > 24

    def test_pre_screen_is_deterministic(self):
        data = render_golden_image("overlapping_labels")
        a = SnapshotExtractor().extract(data)
        b = SnapshotExtractor().extract(data)
        assert a.pre_screen == b.pre_screen

    def test_accepts_jpeg_and_webp_magic(self):
        for fmt, mime in (("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            img = Image.new("RGB", (96, 72), (90, 140, 90))
            buf = io.BytesIO()
            img.save(buf, format=fmt)
            snap = SnapshotExtractor().extract(buf.getvalue())
            assert snap.mime == mime
            assert snap.data_url.startswith(f"data:{mime};base64,")

    def test_env_override_with_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_MAX_IMAGE_BYTES", "not-a-number")
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_MIN_EDGE_PX", "also-bad")
        extractor = SnapshotExtractor()
        assert extractor.max_bytes == 4 * 1024 * 1024
        assert extractor.min_edge == 64

    def test_max_edge_param_flags_oversized_dimensions(self):
        with pytest.raises(SnapshotError) as ei:
            SnapshotExtractor(max_edge=50).extract(_png_bytes(size=(96, 72)))
        assert ei.value.reason == "image_oversized"


# ── 3. vlm_provider：请求组装与输出解析 ──────────────────────────────────


def _vlm_request(**over) -> VLMRequest:
    base = dict(
        provider="openai_compatible", model="vlm-x",
        image_data_url=_data_url(_png_bytes()), user_context="",
    )
    base.update(over)
    return VLMRequest(**base)


class TestVLMProvider:
    def test_openai_request_carries_strict_json_schema(self):
        payload = build_openai_request(_vlm_request())
        assert payload["model"] == "vlm-x"
        assert payload["temperature"] == 0
        fmt = payload["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        dims_enum = fmt["json_schema"]["schema"]["properties"]["critiques"]["items"][
            "properties"]["dimension"]["enum"]
        assert set(dims_enum) == {d.value for d in VisualDimension}
        user_content = payload["messages"][1]["content"]
        assert user_content[1]["type"] == "image_url"
        assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_openai_request_includes_bounded_user_context(self):
        payload = build_openai_request(_vlm_request(user_context="failed_rules: a,b"))
        text = payload["messages"][1]["content"][0]["text"]
        assert "failed_rules" in text

    def test_gemini_request_maps_schema_dialect_and_inline_data(self):
        req = _vlm_request(provider="gemini")
        payload = build_gemini_request(req)
        gen = payload["generationConfig"]
        assert gen["responseMimeType"] == "application/json"
        assert "responseSchema" in gen
        dims_enum = gen["responseSchema"]["properties"]["critiques"]["items"][
            "properties"]["dimension"]["enum"]
        assert set(dims_enum) == {d.value for d in VisualDimension}
        part = payload["contents"][0]["parts"][1]
        assert part["inline_data"]["mime_type"] == "image/png"
        assert part["inline_data"]["data"]  # base64 非空
        assert gemini_endpoint("https://g.example/", "gemini-2") == (
            "https://g.example/v1beta/models/gemini-2:generateContent")

    def test_parse_accepts_plain_and_fenced_json(self):
        good = json.dumps({"critiques": [{"dimension": "readability"}]})
        assert parse_critic_output(good) == [{"dimension": "readability"}]
        fenced = "```json\n" + good + "\n```"
        assert parse_critic_output(fenced) == [{"dimension": "readability"}]

    def test_parse_rejects_malformed_output(self):
        for junk in ("", "no json here", "[1,2,3]", '{"critiques": "nope"}',
                     '{"other": []}'):
            with pytest.raises(CriticOutputError):
                parse_critic_output(junk)

    def test_schema_constant_is_shared_by_both_dialects(self):
        from app.lib.harness.visual_judge import CRITIC_OUTPUT_SCHEMA
        assert build_openai_request(_vlm_request())["response_format"][
            "json_schema"]["schema"] is CRITIC_OUTPUT_SCHEMA
        assert build_gemini_request(_vlm_request(provider="gemini"))[
            "generationConfig"]["responseSchema"] is CRITIC_OUTPUT_SCHEMA


class TestVLMClientsOverMockTransport:
    """provider 客户端 httpx 路径（MockTransport 注入，零网络）。"""

    @staticmethod
    def _openai_handler(content: str = ""):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            TestVLMClientsOverMockTransport.last_request = request
            return httpx.Response(200, json={
                "choices": [{"message": {"content": content}}]})
        return handler

    last_request = None

    def _openai_client(self, handler):
        import httpx

        return OpenAICompatVLMClient(
            "https://api.example.com", "sk-test", "vlm-x",
            transport=httpx.MockTransport(handler))

    def _gemini_client(self, handler):
        import httpx

        return GeminiVLMClient(
            "https://g.example", "g-key", "gemini-2",
            transport=httpx.MockTransport(handler))

    async def test_openai_client_returns_content_and_sends_schema(self):
        good = json.dumps({"critiques": []})
        client = self._openai_client(self._openai_handler(good))
        out = await client.critique(_vlm_request())
        assert json.loads(out)["critiques"] == []
        req = self.last_request
        assert req.headers["Authorization"] == "Bearer sk-test"
        body = json.loads(req.content)
        assert body["response_format"]["json_schema"]["strict"] is True

    async def test_openai_client_maps_timeout_and_http_error(self):
        import httpx

        def timeout_handler(request):
            raise httpx.ConnectTimeout("too slow")

        with pytest.raises(CriticTimeout):
            await self._openai_client(timeout_handler).critique(_vlm_request())

        def err_handler(request):
            return httpx.Response(500, json={"error": "boom"})

        with pytest.raises(CriticProviderError):
            await self._openai_client(err_handler).critique(_vlm_request())

    async def test_openai_client_empty_content_is_output_error(self):
        client = self._openai_client(self._openai_handler("   "))
        with pytest.raises(CriticOutputError):
            await client.critique(_vlm_request())

    async def test_gemini_client_returns_text_and_sends_key_header(self):
        import httpx

        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["key"] = request.headers.get("x-goog-api-key")
            return httpx.Response(200, json={"candidates": [
                {"content": {"parts": [{"text": '{"critiques": []}'}]}}]})

        out = await self._gemini_client(handler).critique(
            _vlm_request(provider="gemini", model="gemini-2"))
        assert json.loads(out)["critiques"] == []
        assert captured["url"].endswith("/v1beta/models/gemini-2:generateContent")
        assert captured["key"] == "g-key"

    async def test_gemini_client_maps_timeout(self):
        import httpx

        def timeout_handler(request):
            raise httpx.ReadTimeout("gemini slow")

        with pytest.raises(CriticTimeout):
            await self._gemini_client(timeout_handler).critique(
                _vlm_request(provider="gemini"))


# ── 4. fake_vlm / golden_images：确定性 Mock 体系 ────────────────────────


class TestGoldenMockSystem:
    def test_registry_has_exactly_ten_samples(self):
        assert len(GOLDEN_SAMPLES) == 10
        names = {s.name for s in GOLDEN_SAMPLES}
        assert names == {
            "overlapping_labels", "low_contrast_dark_theme",
            "adjacent_palette_confusion", "symbol_clutter_overdensity",
            "sparse_canvas_underdensity", "bottom_heavy_layout",
            "overlay_offset_misalignment", "extreme_tilt_rotation",
            "tiny_unreadable_text", "clean_balanced_map",
        }

    def test_expected_dimensions_and_severities(self):
        by_name = {s.name: s for s in GOLDEN_SAMPLES}
        assert by_name["overlapping_labels"].dimension == "readability"
        assert by_name["overlapping_labels"].severity == "error"
        assert by_name["low_contrast_dark_theme"].dimension == "color_discriminability"
        assert by_name["adjacent_palette_confusion"].dimension == "color_discriminability"
        assert by_name["symbol_clutter_overdensity"].dimension == "information_density"
        assert by_name["sparse_canvas_underdensity"].severity == "warning"
        assert by_name["bottom_heavy_layout"].dimension == "composition_balance"
        assert by_name["overlay_offset_misalignment"].dimension == "spatial_alignment"
        assert by_name["extreme_tilt_rotation"].severity == "warning"
        assert by_name["tiny_unreadable_text"].dimension == "readability"
        clean = by_name["clean_balanced_map"]
        assert clean.response["critiques"] == []  # 良图对照：零批评

    def test_golden_images_render_deterministically(self):
        for sample in GOLDEN_SAMPLES:
            a = render_golden_image(sample.name)
            b = render_golden_image(sample.name)
            assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()
            assert a.startswith(b"\x89PNG")

    def test_all_golden_images_pass_snapshot_extraction(self):
        extractor = SnapshotExtractor()
        for sample in GOLDEN_SAMPLES:
            snap = extractor.extract(render_golden_image(sample.name))
            assert snap.width >= 64 and snap.height >= 64

    def test_unknown_sample_name_rejected(self):
        with pytest.raises(KeyError):
            render_golden_image("no_such_sample")
        with pytest.raises(KeyError):
            FakeVLMClient(sample="no_such_sample")

    async def test_fake_client_routes_canned_response_and_counts_calls(self):
        client = FakeVLMClient(sample="overlapping_labels")
        text = await client.critique(_vlm_request())
        parsed = json.loads(text)
        assert parsed["critiques"][0]["dimension"] == "readability"
        assert client.calls == 1

    async def test_fake_client_supports_raw_response_queue_and_faults(self):
        client = FakeVLMClient(responses=["garbage !!"])
        assert await client.critique(_vlm_request()) == "garbage !!"
        broken = FakeVLMClient(exception=CriticTimeout("slow"))
        with pytest.raises(CriticTimeout):
            await broken.critique(_vlm_request())

    async def test_fake_client_exhausted_queue_is_output_error(self):
        from app.lib.harness.visual_judge import CriticOutputError as _OutErr

        client = FakeVLMClient(responses=["one"])
        await client.critique(_vlm_request())
        with pytest.raises(_OutErr):
            await client.critique(_vlm_request())
        assert client.call_count == 2


# ── 5. critic_engine：fail-closed 矩阵 + 记忆化 + 评分推导 ───────────────


class TestCriticEngineFailClosed:
    async def test_happy_path_evaluated_with_structured_diagnosis(self):
        engine, client = _engine_with("overlapping_labels")
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1",
            image=render_golden_image("overlapping_labels"),
        )
        assert report.status == "evaluated"
        assert report.reason == ""
        assert client.calls == 1
        assert report.has_blocking_defects
        top = report.critiques[0]
        assert top.dimension == VisualDimension.READABILITY
        assert top.severity == "error"
        assert top.bbox is not None  # 图面坐标定位
        assert report.image_sha256
        assert report.model == "fake-vlm"
        scores = {s.dimension: s for s in report.dimension_scores}
        assert len(scores) == 5
        assert scores[VisualDimension.READABILITY].score == pytest.approx(6.0)  # 10−4
        assert scores[VisualDimension.SPATIAL_ALIGNMENT].score == pytest.approx(10.0)
        assert scores[VisualDimension.SPATIAL_ALIGNMENT].confidence == 0.0  # 未观察
        assert report.overall_confidence > 0

    async def test_clean_map_evaluated_without_blocking_defects(self):
        engine, client = _engine_with("clean_balanced_map")
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1",
            image=render_golden_image("clean_balanced_map"),
        )
        assert report.status == "evaluated"
        assert report.critiques == []
        assert not report.has_blocking_defects
        assert report.overall_confidence == 0.0  # 零观察 ⇒ 零置信背书
        assert client.calls == 1

    async def test_corrupt_image_is_fail_closed_without_vlm_call(self):
        engine, client = _engine_with("overlapping_labels")
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=b"broken-bytes")
        assert report.status == "not_evaluated"
        assert report.reason == "image_corrupt"
        assert client.calls == 0  # 初筛拦截，不外呼

    async def test_empty_image_fail_closed(self):
        engine, _ = _engine_with()
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=b"")
        assert (report.status, report.reason) == ("not_evaluated", "image_empty")

    async def test_oversized_image_fail_closed(self):
        engine, client = _engine_with("overlapping_labels")
        engine.extractor = SnapshotExtractor(max_bytes=16)
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "image_oversized")
        assert client.calls == 0

    async def test_too_small_image_fail_closed(self):
        engine, _ = _engine_with()
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes(size=(8, 8)))
        assert (report.status, report.reason) == ("not_evaluated", "image_too_small")

    async def test_vlm_timeout_fail_closed(self):
        engine, _ = _engine_with(exception=CriticTimeout("slow provider"))
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "provider_timeout")

    async def test_provider_http_error_fail_closed(self):
        engine, _ = _engine_with(exception=CriticProviderError("502"))
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "provider_error")

    async def test_unexpected_provider_exception_still_fail_closed(self):
        engine, _ = _engine_with(exception=RuntimeError("socket exploded"))
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "provider_error")

    async def test_garbage_output_fail_closed(self):
        engine, _ = _engine_with(responses=["not json at all"])
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "invalid_output")

    async def test_wrong_shape_output_fail_closed(self):
        engine, _ = _engine_with(responses=[json.dumps({"critiques": 42})])
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "invalid_output")

    async def test_missing_client_fail_closed(self):
        engine = VisualCriticEngine()  # 直构无 client
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "not_configured")

    async def test_placeholder_key_build_reports_no_api_key(self, monkeypatch):
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_API_KEY", "your-api-key-here")
        engine = build_critic_engine()
        assert engine.client is None
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "no_api_key")

    async def test_unknown_provider_build_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_PROVIDER", "bogus-provider")
        engine = build_critic_engine()
        assert engine.client is None
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert (report.status, report.reason) == ("not_evaluated", "not_configured")

    async def test_gemini_build_selects_gemini_client(self, monkeypatch):
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_PROVIDER", "gemini")
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_API_KEY", "real-key")
        engine = build_critic_engine()
        assert engine.client is not None
        assert engine.client.provider == "gemini"

    async def test_mutation_intent_critique_is_quarantined(self):
        raw = json.dumps({"critiques": [
            {"dimension": "readability", "severity": "error", "suggestion": "ok",
             "confidence": 0.8},
            {"dimension": "readability", "severity": "error", "suggestion": "bad",
             "confidence": 0.9, "mutations": [{"op": "remove_layer"}]},
            {"dimension": "no_such_dimension", "suggestion": "?", "confidence": 0.5},
            "not-a-dict",
        ]})
        engine, _ = _engine_with(responses=[raw])
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert report.status == "evaluated"
        assert len(report.critiques) == 1  # 3 条污染全部判废，只留净批评

    async def test_invalid_bbox_blanked_not_verdict_killing(self):
        raw = json.dumps({"critiques": [
            {"dimension": "composition_balance", "severity": "warning",
             "suggestion": "heavy bottom", "confidence": 0.6,
             "bbox": [200.0, 0.0, 10.0, 50.0]},  # 次序非法
        ]})
        engine, _ = _engine_with(responses=[raw])
        report = await engine.evaluate(
            session_id="s1", mapspec_fingerprint="fp1", image=_png_bytes())
        assert report.status == "evaluated"
        assert report.critiques[0].bbox is None  # 定位置空，批评保留


class TestCriticEngineMemoization:
    async def test_same_key_single_vlm_call(self):
        engine, client = _engine_with("overlapping_labels")
        image = render_golden_image("overlapping_labels")
        r1 = await engine.evaluate(session_id="s1", mapspec_fingerprint="fp1", image=image)
        r2 = await engine.evaluate(session_id="s1", mapspec_fingerprint="fp1", image=image)
        assert client.calls == 1  # 单轮至多一次外呼
        assert r2.status == r1.status == "evaluated"

    async def test_cache_hits_including_not_evaluated(self):
        engine, client = _engine_with(exception=CriticTimeout("slow"))
        image = _png_bytes()
        await engine.evaluate(session_id="s1", mapspec_fingerprint="fp1", image=image)
        r2 = await engine.evaluate(session_id="s1", mapspec_fingerprint="fp1", image=image)
        assert client.calls == 1
        assert r2.reason == "provider_timeout"  # 缓存回放，不再外呼

    async def test_cache_key_distinguishes_session_fingerprint_image(self):
        image = _png_bytes()
        e1, c1 = _engine_with("overlapping_labels")
        await e1.evaluate(session_id="s1", mapspec_fingerprint="fp", image=image)
        await e1.evaluate(session_id="s2", mapspec_fingerprint="fp", image=image)
        await e1.evaluate(session_id="s1", mapspec_fingerprint="fp2", image=image)
        assert c1.calls == 3
        e2, c2 = _engine_with("overlapping_labels")
        await e2.evaluate(session_id="s1", mapspec_fingerprint="fp", image=_png_bytes((10, 10, 10)))
        await e2.evaluate(session_id="s1", mapspec_fingerprint="fp", image=_png_bytes((240, 10, 10)))
        assert c2.calls == 2  # 换图 ⇒ 换 sha ⇒ 新外呼

    async def test_cache_is_bounded_lru(self):
        engine, client = _engine_with("overlapping_labels")
        for i in range(70):
            img = _png_bytes(color=(i % 256, (i * 7) % 256, 30))
            await engine.evaluate(session_id="s", mapspec_fingerprint=f"fp{i}", image=img)
        assert len(engine.cache) == 64
        # 最老一代（fp0）已被逐出 ⇒ 重评触发新外呼
        await engine.evaluate(session_id="s", mapspec_fingerprint="fp0",
                              image=_png_bytes(color=(0, 0, 30)))
        assert client.calls == 71

    async def test_cache_clear_forces_re_evaluation(self):
        engine, client = _engine_with("overlapping_labels")
        image = _png_bytes()
        await engine.evaluate(session_id="s", mapspec_fingerprint="fp", image=image)
        engine.cache.clear()
        await engine.evaluate(session_id="s", mapspec_fingerprint="fp", image=image)
        assert client.calls == 2

    async def test_non_serializable_summary_stays_bounded_and_evaluates(self):
        engine, client = _engine_with("clean_balanced_map")
        report = await engine.evaluate(
            session_id="s", mapspec_fingerprint="fp", image=_png_bytes(),
            deterministic_summary={"bad": {1, 2, 3}},  # set 不可 JSON 序列化
        )
        assert report.status == "evaluated"
        assert client.calls == 1


class TestRuntimeEnabledFlag:
    def test_flag_is_opt_in(self):
        assert visual_critic_runtime_enabled() is False
        import os
        os.environ["CARTO_VISUAL_CRITIC_RUNTIME"] = "1"
        try:
            assert visual_critic_runtime_enabled() is True
        finally:
            os.environ.pop("CARTO_VISUAL_CRITIC_RUNTIME", None)
        assert visual_critic_runtime_enabled() is False


# ── 6. visual_evaluator 挂接：opt-in / 优先级 / record-only / L5 ─────────


def _write_screenshot(monkeypatch, tmp_path, data: bytes) -> Path:
    p = tmp_path / "map.png"
    p.write_bytes(data)
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_SCREENSHOT", str(p))
    return p


class TestVisualEvaluatorWiring:
    async def test_runtime_off_keeps_legacy_fail_closed_shape(self):
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        assert len(cartography.visual_evidence) == 1
        row = cartography.visual_evidence[0]
        assert row["status"] == "not_evaluated"
        assert row["reason"] == "visual_judge_disabled"
        assert "runtime" not in row  # legacy 摘要无 v2 键

    async def test_runtime_on_evaluates_via_engine(self, monkeypatch, tmp_path):
        _write_screenshot(monkeypatch, tmp_path, render_golden_image("overlapping_labels"))
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_RUNTIME", "1")
        engine, client = _engine_with("overlapping_labels")
        monkeypatch.setattr(ve, "_build_critic_engine", lambda: engine)
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        assert client.calls == 1
        summary = cartography.visual_evidence[-1]
        assert summary["status"] == "evaluated"
        assert summary["runtime"] == "visual_critic_v2"
        assert summary["source"] == "visual_judge"  # L5 消费面不变
        assert len(summary["dimension_scores"]) == 5
        rows = [c for c in cartography.checks if c["rule"] == "VISUAL_READABILITY"]
        assert rows and rows[0]["status"] == "fail"
        assert rows[0]["evidence_class"] == "visual"
        assert rows[0]["evidence"]["bbox"] is not None
        assert rows[0]["repairability"] == "not_repairable"  # readability 无 AUTO_SAFE 动作
        # record-only：只有追加，不改写三态 verdict 相关状态
        assert cartography.status == "passed"

    async def test_runtime_on_color_error_maps_auto_safe_palette_action(
        self, monkeypatch, tmp_path
    ):
        _write_screenshot(monkeypatch, tmp_path, render_golden_image("low_contrast_dark_theme"))
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_RUNTIME", "1")
        engine, _ = _engine_with("low_contrast_dark_theme")
        monkeypatch.setattr(ve, "_build_critic_engine", lambda: engine)
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        rows = [c for c in cartography.checks if c["rule"] == "VISUAL_COLOR_DISCRIMINABILITY"]
        assert rows and rows[0]["status"] == "fail"
        assert rows[0]["repairability"] == "auto_safe"
        assert rows[0]["suggested_fix"] == {"operation": "rotate_palette",
                                            "dimension": "color_discriminability"}

    async def test_runtime_on_fail_closed_row_on_provider_error(
        self, monkeypatch, tmp_path
    ):
        _write_screenshot(monkeypatch, tmp_path, _png_bytes())
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_RUNTIME", "1")
        engine, _ = _engine_with(exception=CriticTimeout("slow"))
        monkeypatch.setattr(ve, "_build_critic_engine", lambda: engine)
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        oracle = [c for c in cartography.checks if c["rule"] == "VISUAL_ORACLE"]
        assert oracle and oracle[0]["status"] == "not_evaluated"
        assert oracle[0]["evidence"]["reason"] == "provider_timeout"
        assert not any(c["status"] == "pass" for c in cartography.checks)  # 绝不伪造 pass

    async def test_runtime_attach_is_memoized_across_invocations(
        self, monkeypatch, tmp_path
    ):
        _write_screenshot(monkeypatch, tmp_path, render_golden_image("bottom_heavy_layout"))
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_RUNTIME", "1")
        engine, client = _engine_with("bottom_heavy_layout")
        monkeypatch.setattr(ve, "_build_critic_engine", lambda: engine)
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        await ve.attach_visual_judgement("sess-1", cartography, {})
        assert client.calls == 1  # (session, fingerprint, sha) 命中缓存
        assert len(cartography.visual_evidence) == 2  # 摘要照常逐次落账

    async def test_injected_judge_takes_priority_over_runtime(
        self, monkeypatch, tmp_path
    ):
        _write_screenshot(monkeypatch, tmp_path, _png_bytes())
        monkeypatch.setenv("CARTO_VISUAL_CRITIC_RUNTIME", "1")
        module = types.ModuleType("ac01_priority_judge")

        async def judge(snapshot):
            return [{"dimension": "readability", "severity": "info",
                     "suggestion": "injected", "confidence": 0.5}]

        module.judge = judge
        monkeypatch.setitem(sys.modules, "ac01_priority_judge", module)
        monkeypatch.setenv("CARTO_VISUAL_JUDGE", "ac01_priority_judge:judge")
        engine, client = _engine_with("overlapping_labels")
        monkeypatch.setattr(ve, "_build_critic_engine", lambda: engine)
        cartography = _make_cartography()
        await ve.attach_visual_judgement("sess-1", cartography, {})
        assert client.calls == 0  # 引擎未被触达
        assert "runtime" not in cartography.visual_evidence[-1]  # legacy 注入路径

    async def test_openai_client_built_when_real_key_present(self, monkeypatch):
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_API_KEY", "sk-real-test-key")
        engine = build_critic_engine()
        assert engine.client is not None
        assert engine.client.provider == "openai_compatible"
        assert engine.client.model  # 模型名来自 env 或 settings 回落


class TestL5DerivationCompatibility:
    def _cartography_with_v2_summary(self, *, error: bool):
        cartography = _make_cartography()
        score = 6.0 if error else 9.5
        critiques = [{
            "dimension": "readability", "severity": "error" if error else "info",
            "suggestion": "labels overlap", "evidence_class": "visual",
            "confidence": 0.9, "evidence": "",
        }] if error else []
        cartography.visual_evidence.append({
            "evidence_class": "visual", "source": "visual_judge",
            "status": "evaluated", "reason": "", "mode": "record_only",
            "runtime": "visual_critic_v2",
            "critiques": critiques, "error_count": 1 if error else 0,
            "warning_count": 0, "fingerprint": "fp", "screenshot_digest": "ab",
            "duration_ms": 10, "model": "fake-vlm",
            "dimension_scores": [
                {"dimension": d.value, "score": score, "confidence": 0.9}
                for d in VisualDimension
            ],
            "overall_score": score, "overall_confidence": 0.9,
        })
        return cartography

    def test_v2_visual_error_fails_l5_when_l4_passed(self):
        cartography = self._cartography_with_v2_summary(error=True)
        cartography.passed = True
        verdict = ve.derive_goal_satisfaction(cartography)
        assert verdict["status"] == "fail"
        assert verdict["reason"] == "visual_error_critique"

    def test_v2_visual_never_backs_l4_failure(self):
        cartography = self._cartography_with_v2_summary(error=True)
        cartography.passed = False
        verdict = ve.derive_goal_satisfaction(cartography)
        assert verdict == {"status": "not_evaluated", "reason": "l4_anchor_not_passed"}

    def test_v2_visual_concurs_only_without_errors(self):
        cartography = self._cartography_with_v2_summary(error=False)
        cartography.passed = True
        verdict = ve.derive_goal_satisfaction(cartography)
        assert verdict == {"status": "pass", "reason": "visual_judge_concurred"}
