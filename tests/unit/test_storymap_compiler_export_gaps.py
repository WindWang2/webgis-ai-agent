"""StoryMap 编译器/导出/会话路由的未覆盖分支（#1552 测试债清偿，G08 后续）。

test_storymap_orchestrator.py 已覆盖主路径；本文件锁定其未触及的分支：

- normalize_trace：未识别形状显式 ValueError（不猜测）、非法 stage id
  （bool/越界/未知名）静默丢弃、ReplayTrace outcome.verdict → S17；
- 编译降级与优先级：单条消息只出引言、trace 与 messages 并存时 trace 胜、
  _payload_bbox 取值优先级链（bbox > extent > center）、center 无 zoom 的
  点状 bbox、_union_bbox 跨反子午线展开；
- metadata 溯源：session_id/turn_id 自 trace 提升、final_text → summary、
  标题默认值；
- 联动素材：ref:table/stats/grid/admin 词表映射（grid/admin → table）、
  同 ref 去重、无 ref chart 的 widget-chart-N 自动 id、stats 6 条上限；
- 导出：脱敏开关（sanitize=False 保留原值）、HTML 内嵌安全
  （\\u2028/\\u2029 转义 + title 实体转义 + 缺省标题）、非 Mapping 图层
  条目过滤与 feature_count 容错；
- 服务壳：layers_to_featurecollections 过滤与行名覆盖；
- 会话路由：POST /sessions/{id}/compile 的 422 错误映射与 200 契约
  （依赖覆盖 + monkeypatch 服务函数，不触真实 DB）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.lib.storymap.export_packager import (
    _embed_json,
    build_story_bundle,
    render_standalone_html,
    sanitize_dict,
)
from app.lib.storymap.spec import (
    StoryChapter,
    StoryMapMetadata,
    StoryMapSpec,
)
from app.lib.storymap.story_compiler import (
    _payload_bbox,
    _union_bbox,
    compile_story_map,
    normalize_trace,
)


def _spec() -> StoryMapSpec:
    return StoryMapSpec(
        metadata=StoryMapMetadata(title="测试专报", session_id="sess-123"),
        chapters=[StoryChapter(id="c1", title="章", narrative="正文",
                               arc_role="introduction")],
    )


class TestNormalizeTraceGaps:
    def test_unrecognized_shape_raises(self):
        with pytest.raises(ValueError, match="unrecognized trace shape"):
            normalize_trace({"foo": "bar"})
        # stages 键存在但非 list → 同样不得猜测
        with pytest.raises(ValueError):
            normalize_trace({"stages": "not-a-list"})

    def test_invalid_stage_ids_silently_skipped(self):
        stages = [
            {"stage": 19},        # 越界
            {"stage": 0},         # 越界
            {"stage": True},      # bool 不是合法 stage
            {"stage": "NOPE"},    # 未知名
            {"stage": "data_profile", "ts": 1.5},
            {"stage_id": 5, "stage": 6},  # stage_id 优先
        ]
        steps = normalize_trace({"stages": stages})
        assert [s.stage_id for s in steps] == [4, 5]
        assert steps[0].ts == 1.5

    def test_replaytrace_outcome_verdict_and_missing_user_input(self):
        # 有 user_input：S1 + S9 + S17(verdict) + S18
        with_ui = normalize_trace({
            "user_input": "你好",
            "tool_calls": [{"tool_name": "poi_search"}],
            "outcome": {"verdict": "通过"},
            "final_text": "完成",
        })
        assert [s.stage_id for s in with_ui] == [1, 9, 17, 18]
        # 无 user_input → 无 S1；无 outcome.verdict → 无 S17
        bare = normalize_trace({"tool_calls": [{"tool_name": "t"}]})
        assert [s.stage_id for s in bare] == [9]


class TestCompileFallbacks:
    def test_single_message_compiles_intro_only(self):
        spec = compile_story_map(messages=[{"role": "user", "content": "分析降水"}])
        assert len(spec.chapters) == 1
        assert spec.chapters[0].arc_role == "introduction"

    def test_trace_takes_precedence_over_messages(self):
        trace = {"stages": [{"stage": 4, "summary": "宏观态势"}]}
        spec = compile_story_map(
            trace=trace, messages=[{"role": "user", "content": "不该出现"}],
        )
        assert [c.arc_role for c in spec.chapters] == ["macro_situation"]
        assert all("不该出现" not in c.narrative for c in spec.chapters)
        assert spec.metadata.summary == "宏观态势"

    def test_payload_bbox_priority_and_point_center(self):
        # 优先级链：bbox > extent > center(+zoom 合成)；非有限值视同缺失。
        assert _payload_bbox(
            {"bbox": [1.0, 2.0, 3.0, 4.0], "center": [99.0, 99.0]}
        ) == [1.0, 2.0, 3.0, 4.0]
        assert _payload_bbox(
            {"bbox": [1.0, 2.0, 3.0, 4.0], "extent": [5.0, 6.0, 7.0, 8.0]}
        ) == [1.0, 2.0, 3.0, 4.0]
        assert _payload_bbox({"extent": [5.0, 6.0, 7.0, 8.0]}) == [
            5.0, 6.0, 7.0, 8.0,
        ]
        # center 无 zoom → 点状 bbox
        assert _payload_bbox({"center": [116.4, 39.9]}) == [
            116.4, 39.9, 116.4, 39.9,
        ]
        # center + zoom=3 → 合成跨度 360/2^(3-1)=90°
        assert _payload_bbox({"center": [116.4, 39.9], "zoom": 3.0}) == [
            71.4, 17.4, 161.4, 62.4,
        ]
        assert _payload_bbox({}) is None

    def test_union_bbox_antimeridian_expansion(self):
        # 含跨反子午线盒（e<w）→ 统一展开 +360 后并集。
        assert _union_bbox(
            [[170.0, -10.0, -170.0, 10.0], [-10.0, -5.0, 10.0, 5.0]]
        ) == [-10.0, -10.0, 190.0, 10.0]
        assert _union_bbox([None, None]) is None
        assert _union_bbox([]) is None

    def test_metadata_provenance_and_default_title(self):
        trace = {
            "session_id": "s-1", "turn_id": "t-1",
            "stages": [{"stage": 18, "final_text": "结论句"}],
        }
        spec = compile_story_map(trace=trace)
        assert spec.metadata.session_id == "s-1"
        assert spec.metadata.turn_id == "t-1"
        assert spec.metadata.summary == "结论句"
        assert spec.metadata.title == "StoryMap 空间叙事专报"  # 默认标题
        # 显式参数优先于 trace 内字段
        spec2 = compile_story_map(trace=trace, session_id="s-2", title="自定义")
        assert spec2.metadata.session_id == "s-2"
        assert spec2.metadata.title == "自定义"


class TestWidgetHarvest:
    def test_ref_kind_mapping_via_compile(self):
        note = "对比 ref:table-a1 ref:grid-g2 ref:admin-a3 ref:stats-s4 ref:chart-c5"
        spec = compile_story_map(
            trace={"stages": [{"stage": 9, "note": note}]},
        )
        by_ref = {w.ref: w.kind for w in spec.linked_widgets}
        assert by_ref == {
            "ref:table-a1": "table",
            "ref:grid-g2": "table",    # grid 折到 table
            "ref:admin-a3": "table",   # admin 折到 table
            "ref:stats-s4": "stats",
            "ref:chart-c5": "chart",
        }

    def test_widget_dedup_and_auto_id(self):
        stages = [
            {"stage": 9, "ref": "ref:chart-x",
             "chart": {"type": "bar", "data": [1]}},
            {"stage": 9, "ref": "ref:chart-x",
             "chart": {"type": "line"}},          # 同 ref → 去重
            {"stage": 9, "chart": {"type": "kpi"}, "title": "无 ref 图"},
        ]
        spec = compile_story_map(trace={"stages": stages})
        widgets = spec.linked_widgets
        assert [w.id for w in widgets] == ["ref:chart-x", "widget-chart-2"]
        assert widgets[0].data == {"type": "bar", "data": [1]}
        assert widgets[1].data == {"type": "kpi"}
        assert widgets[1].title == "无 ref 图"

    def test_stat_bullets_capped_at_six(self):
        stats = {f"k{i}": i for i in range(8)}
        spec = compile_story_map(
            trace={"stages": [{"stage": 4, "stats": stats}]},
        )
        bullets = [ln for ln in spec.chapters[0].narrative.splitlines()
                   if ln.startswith("- k")]
        assert len(bullets) == 6


class TestExportPackagerGaps:
    def test_sanitize_sensitive_key_paths(self):
        # 词元命中 / 折叠子串命中 / 普通词不误杀
        out = sanitize_dict({
            "accesskeyid": "x",   # 折叠子串 "accesskey"
            "sessionid": "y",     # 词元 "sessionid"
            "keyboard": "z",      # 不得误杀
        })
        assert out["accesskeyid"] == "REDACTED"
        assert out["sessionid"] == "REDACTED"
        assert out["keyboard"] == "z"
        # sanitize=False 保留原值（文档化逃生门）
        spec = _spec()
        assert build_story_bundle(spec, sanitize=True)["spec"]["metadata"][
            "session_id"] == "REDACTED"
        assert build_story_bundle(spec, sanitize=False)["spec"]["metadata"][
            "session_id"] == "sess-123"

    def test_html_embedding_safety(self):
        # \\u2028/\\u2029 转义且可无损还原
        payload = {"s": "a b c"}
        embedded = _embed_json(payload)
        assert "\\u2028" in embedded and "\\u2029" in embedded
        import json as _json

        assert _json.loads(embedded)["s"] == payload["s"]
        # title 实体转义进 <title> 文本节点
        bundle = {"spec": {"metadata": {"title": "A & B <C>"}}}
        html = render_standalone_html(bundle)
        assert "<title>A &amp; B &lt;C&gt; · 离线专报</title>" in html
        # 缺省标题
        assert "<title>StoryMap · 离线专报</title>" in render_standalone_html({})

    def test_bundle_layer_filtering_and_feature_count(self):
        spec = _spec()
        bundle = build_story_bundle(spec, layers=[
            {"type": "FeatureCollection", "features": [1, 2]},
            "junk",                       # 非 Mapping → 过滤
            42,
            {"no_features_key": True},    # features 缺失 → 计 0
        ])
        assert bundle["manifest"]["layer_count"] == 2
        assert bundle["manifest"]["feature_count"] == 2


class TestServiceAndSessionRoute:
    def test_layers_to_featurecollections_filter_and_name_override(self):
        from app.services.storymap import layers_to_featurecollections

        rows = [
            {"name": "行图层",
             "geojson": {"type": "FeatureCollection", "features": [],
                         "name": "FC原名"}},
            {"geojson": {"type": "FeatureCollection", "features": []}},
            {"name": "无几何", "geojson": {"type": "Point"}},
            "not-a-mapping",
            {"name": "",
             "geojson": {"type": "FeatureCollection", "name": "空行名"}},
        ]
        fcs = layers_to_featurecollections(rows)
        assert len(fcs) == 3
        assert fcs[0]["name"] == "行图层"      # 行名覆盖 FC 名
        assert "name" not in fcs[1]            # 行无名且 FC 无名
        assert fcs[2]["name"] == "空行名"      # 行名空串 → 保留 FC 名

    async def test_session_compile_route_contract(self, monkeypatch):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.routes import storymap as routes_mod
        from app.services.auth_history_bridge import require_owned_session
        from app.core.database import get_async_db

        app = FastAPI()
        app.include_router(routes_mod.router, prefix="/api/v1")
        app.dependency_overrides[require_owned_session] = lambda: SimpleNamespace(
            id="conv-1")
        app.dependency_overrides[get_async_db] = lambda: object()

        # 422 映射：服务层 ValueError → 契约 422（不 500）
        async def failing(db, conv):
            raise ValueError("boom")

        monkeypatch.setattr(routes_mod, "compile_for_session", failing)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/v1/storymap/sessions/conv-1/compile")
            assert resp.status_code == 422
            assert resp.json()["detail"] == "boom"

        # 200 契约：spec 正常返回
        async def ok(db, conv):
            return _spec()

        monkeypatch.setattr(routes_mod, "compile_for_session", ok)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/v1/storymap/sessions/conv-1/compile")
            assert resp.status_code == 200
            assert resp.json()["chapters"][0]["id"] == "c1"
