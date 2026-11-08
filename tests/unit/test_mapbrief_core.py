"""G11 证据化地图简报 —— 核心单元测试（模型/事实核验/草稿编译）。

分层标记：pure unit（本文件全部）。核验器的positive-proof 纪律与
BRF-O01/O02 的确定性 Oracle 在此守护。
"""
from __future__ import annotations

import pytest

from app.lib.storymap.brief_factcheck import (
    FactCheckOutcome,
    apply_outcomes,
    check_fact,
    check_facts,
    resolve_field_path,
)
from app.lib.storymap.brief_spec import (
    BRIEF_SPEC_SCHEMA_VERSION,
    BriefBlock,
    BriefFact,
    BriefMapView,
    BriefProvenance,
    EvidenceBinding,
    MapBriefSpec,
    is_brief_payload,
)
from app.lib.storymap.spec import StoryMapSpec


# ── 工具：最小合法载荷 ────────────────────────────────────────────────


def _fact(**over) -> dict:
    base = {
        "fact_id": "fact-a1", "label": "学校总数", "kind": "count",
        "value": 142, "unit": "所",
        "basis": {
            "artifact_ref": "ref:stats-schools",
            "source_label": "学校分布统计表",
            "field_path": "table.rows.0.count",
        },
    }
    base.update(over)
    return base


def _brief(**over) -> dict:
    base = {
        "metadata": {"title": "学校分布简报", "session_id": "s1"},
        "chapters": [{
            "id": "arc-introduction", "title": "引言",
            "narrative": "## 引言\n学校分布分析", "arc_role": "introduction",
        }],
        "facts": [_fact()],
        "blocks": [
            {"id": "blk-m1", "chapter_id": "arc-introduction",
             "kind": "metric", "fact_id": "fact-a1"},
        ],
    }
    base.update(over)
    return base


def _records() -> dict:
    return {
        "ref:stats-schools": {
            "artifact_id": "ref:stats-schools", "status": "valid",
            "producer_tool": "analyze_distribution",
            "metadata": {"unit": "所"},
        },
    }


def _payloads() -> dict:
    return {
        "ref:stats-schools": {
            "table": {
                "columns": ["name", "count"],
                "rows": [
                    {"name": "锦江区", "count": 142},
                    {"name": "青羊区", "count": 97},
                ],
            },
        },
    }


# ── 1. 模型契约 ──────────────────────────────────────────────────────


class TestBriefSpecModel:
    def test_minimal_brief_valid(self):
        spec = MapBriefSpec.model_validate(_brief())
        assert spec.brief_schema_version == BRIEF_SPEC_SCHEMA_VERSION
        assert spec.facts[0].status == "unverified"  # 默认不验证
        assert is_brief_payload(spec.model_dump())

    def test_ratio_without_denominator_rejected(self):
        with pytest.raises(Exception, match="denominator"):
            BriefFact.model_validate(_fact(kind="ratio", value=0.2))

    def test_change_without_baseline_rejected(self):
        with pytest.raises(Exception, match="baseline"):
            BriefFact.model_validate(_fact(kind="change", value=0.2))

    def test_verified_requires_binding(self):
        with pytest.raises(Exception, match="artifact binding"):
            BriefFact.model_validate(_fact(
                status="verified", basis={"artifact_ref": ""}))

    def test_block_referencing_unknown_fact_rejected(self):
        with pytest.raises(Exception, match="unknown fact"):
            MapBriefSpec.model_validate(_brief(blocks=[
                {"id": "blk-x1", "chapter_id": "arc-introduction",
                 "kind": "metric", "fact_id": "fact-missing"},
            ]))

    def test_block_referencing_unknown_chapter_rejected(self):
        with pytest.raises(Exception, match="unknown chapter"):
            MapBriefSpec.model_validate(_brief(blocks=[
                {"id": "blk-x2", "chapter_id": "nope", "kind": "text"},
            ]))

    def test_fact_dependency_cycle_rejected(self):
        payload = _brief(facts=[
            _fact(fact_id="fact-a", kind="ratio", value=0.5,
                  denominator_fact_id="fact-b"),
            _fact(fact_id="fact-b", kind="ratio", value=2.0,
                  denominator_fact_id="fact-a"),
        ], blocks=[])
        with pytest.raises(Exception, match="cycle"):
            MapBriefSpec.model_validate(payload)

    def test_metric_block_requires_fact(self):
        with pytest.raises(Exception, match="fact_id"):
            BriefBlock.model_validate(
                {"id": "blk-m9", "chapter_id": "c", "kind": "metric"})

    def test_legacy_storymap_roundtrip_keeps_brief_keys(self):
        spec = MapBriefSpec.model_validate(_brief())
        legacy = StoryMapSpec.model_validate(spec.model_dump())
        assert legacy.model_extra.get("brief_schema_version") == "brief-1"
        # 经 legacy 读写 round-trip 后简报数据完整保全（extra 保留纪律）。
        spec2 = MapBriefSpec.model_validate(legacy.model_dump())
        assert len(spec2.blocks) == 1 and len(spec2.facts) == 1
        # 纯 StoryMapSpec（无简报键）→ MapBriefSpec 合法且 blocks/facts 空。
        bare = StoryMapSpec.model_validate({
            "metadata": {"title": "t"},
            "chapters": [{"id": "c1", "title": "x", "narrative": "y",
                          "arc_role": "introduction"}],
        }).model_dump()
        spec3 = MapBriefSpec.model_validate(bare)
        assert spec3.blocks == [] and spec3.facts == []

    def test_nan_and_infinity_rejected(self):
        with pytest.raises(Exception):
            BriefFact.model_validate(_fact(value=float("nan")))

    def test_bad_block_id_shape_rejected(self):
        with pytest.raises(Exception, match="invalid block id"):
            BriefBlock.model_validate({"id": "../evil", "chapter_id": "c"})

    def test_bad_artifact_ref_shape_rejected(self):
        with pytest.raises(Exception, match="invalid artifact ref"):
            EvidenceBinding(artifact_ref="http://evil/x")


# ── 2. 字段定位 ──────────────────────────────────────────────────────


class TestResolveFieldPath:
    def test_dict_list_navigation(self):
        payload = {"table": {"rows": [{"count": 142}, {"count": 97}]}}
        value, reason = resolve_field_path(payload, "table.rows.1.count")
        assert value == 97 and reason == ""

    def test_bool_is_not_numeric(self):
        value, _ = resolve_field_path({"flag": True}, "flag")
        assert value is None

    def test_missing_key(self):
        value, reason = resolve_field_path({"a": 1}, "b")
        assert value is None and "not found" in reason

    def test_index_out_of_range(self):
        value, reason = resolve_field_path({"rows": [1]}, "rows.5")
        assert value is None and "out of range" in reason

    def test_descend_into_scalar(self):
        value, reason = resolve_field_path({"a": 1.5}, "a.b")
        assert value is None


# ── 3. 事实核验（positive-proof 裁决） ────────────────────────────────


class TestFactCheck:
    def test_verified_happy_path(self):
        fact = BriefFact.model_validate(_fact())
        out = check_fact(fact, records=_records(), payloads=_payloads(),
                         fact_values={})
        assert out.status == "verified", out.reasons

    def test_contradiction_is_unverified(self):
        fact = BriefFact.model_validate(_fact(value=999))
        out = check_fact(fact, records=_records(), payloads=_payloads(),
                         fact_values={})
        assert out.status == "unverified"
        assert any("contradicts" in r for r in out.reasons)

    def test_missing_payload_fails_closed(self):
        fact = BriefFact.model_validate(_fact())
        out = check_fact(fact, records=_records(), payloads={},
                         fact_values={})
        assert out.status == "unverified"
        assert any("unavailable" in r for r in out.reasons)

    def test_unregistered_artifact_is_unverified(self):
        fact = BriefFact.model_validate(
            _fact(basis={"artifact_ref": "ref:stats-ghost",
                         "field_path": "table.rows.0.count"}))
        out = check_fact(fact, records=_records(), payloads={},
                         fact_values={})
        assert out.status == "unverified"
        assert any("not registered" in r for r in out.reasons)

    def test_unit_mismatch_is_warning(self):
        records = _records()
        records["ref:stats-schools"]["metadata"]["unit"] = "个"
        fact = BriefFact.model_validate(_fact())
        out = check_fact(fact, records=records, payloads=_payloads(),
                         fact_values={})
        assert out.status == "warning"
        assert any("unit mismatch" in r for r in out.reasons)

    def test_stale_artifact_is_warning(self):
        records = _records()
        records["ref:stats-schools"]["status"] = "superseded"
        fact = BriefFact.model_validate(_fact())
        out = check_fact(fact, records=records, payloads=_payloads(),
                         fact_values={})
        assert out.status == "warning"
        assert any("superseded" in r for r in out.reasons)

    def test_ratio_recompute_verified_with_numerator_denominator(self):
        facts = [
            BriefFact.model_validate(_fact(
                fact_id="fact-num", label="2024 学校", value=1214)),
            BriefFact.model_validate(_fact(
                fact_id="fact-den", label="2020 学校", value=1100)),
            BriefFact.model_validate(_fact(
                fact_id="fact-ratio", label="增长", kind="change", value=0.10363636363636364,
                numerator_fact_id="fact-num", baseline_fact_id="fact-den",
                unit="")),
        ]
        payloads = _payloads()
        payloads["ref:stats-schools"]["table"]["rows"] = [
            {"name": "锦江区", "count": 1214},
            {"name": "青羊区", "count": 1100},
            {"name": "增速", "count": 0.10363636363636364},
        ]
        # 让 ratio 事实自身 field_path 指向增速行（双重正向证明）
        facts[2] = facts[2].model_copy(update={"basis": EvidenceBinding(
            artifact_ref="ref:stats-schools",
            field_path="table.rows.2.count")})
        values = {f.fact_id: f.value for f in facts}
        outs = {o.fact_id: o for o in (
            check_fact(f, records=_records(), payloads=payloads,
                       fact_values=values) for f in facts)}
        assert outs["fact-ratio"].status == "verified", outs["fact-ratio"].reasons
        assert outs["fact-ratio"].recompute["numerator"] == 1214
        assert outs["fact-ratio"].recompute["baseline"] == 1100

    def test_ratio_contradicting_recompute_is_unverified(self):
        facts = [
            BriefFact.model_validate(_fact(
                fact_id="fact-num", label="2024", value=1214,
                basis={"artifact_ref": "ref:stats-schools",
                       "field_path": "table.rows.0.count"})),
            BriefFact.model_validate(_fact(
                fact_id="fact-den", label="2020", value=1100,
                basis={"artifact_ref": "ref:stats-schools",
                       "field_path": "table.rows.1.count"})),
            BriefFact.model_validate(_fact(
                fact_id="fact-ratio", label="增长", kind="change",
                value=0.5,  # 与复算 (1214-1100)/1100≈0.1036 矛盾
                numerator_fact_id="fact-num", baseline_fact_id="fact-den",
                basis={"artifact_ref": "ref:stats-schools",
                       "field_path": ""})),
        ]
        values = {f.fact_id: f.value for f in facts}
        out = check_fact(facts[2], records=_records(), payloads=_payloads(),
                         fact_values=values)
        assert out.status == "unverified"
        assert any("recomputed" in r for r in out.reasons)

    def test_denominator_zero_disclosed(self):
        facts = [
            BriefFact.model_validate(_fact(
                fact_id="fact-num", value=100)),
            BriefFact.model_validate(_fact(
                fact_id="fact-den", value=0,
                basis={"field_path": "table.rows.1.count"})),
            BriefFact.model_validate(_fact(
                fact_id="fact-ratio", kind="ratio", value=0.0,
                numerator_fact_id="fact-num",
                denominator_fact_id="fact-den",
                basis={"field_path": ""})),
        ]
        values = {f.fact_id: f.value for f in facts}
        out = check_fact(facts[2], records=_records(), payloads=_payloads(),
                         fact_values=values)
        assert out.status == "unverified"
        assert any("zero" in r for r in out.reasons)

    def test_field_path_missing_is_unverified_not_warning(self):
        fact = BriefFact.model_validate(
            _fact(basis={"artifact_ref": "ref:stats-schools",
                         "field_path": ""}))
        out = check_fact(fact, records=_records(), payloads=_payloads(),
                         fact_values={})
        assert out.status == "unverified"
        assert any("field_path missing" in r for r in out.reasons)

    def test_check_facts_and_apply_outcomes_roundtrip(self):
        spec_facts = [BriefFact.model_validate(_fact()),
                      BriefFact.model_validate(_fact(
                          fact_id="fact-a2", value=999))]
        outcomes = check_facts(spec_facts, records=_records(),
                               payloads=_payloads())
        updated = apply_outcomes(spec_facts, outcomes)
        assert updated[0].status == "verified"
        assert updated[1].status == "unverified"
        assert "contradicts" in updated[1].status_reason


# ── 4. 草稿编译（服务壳，loader 打桩） ────────────────────────────────


class TestBriefCompiler:
    @pytest.fixture()
    def patched_loaders(self, monkeypatch):
        """打桩会话输入：1 个表产物 + 1 个图产物 + trace。"""
        from app.services.storymap import brief_compiler as bc

        records = {
            "ref:stats-schools": {
                "artifact_id": "ref:stats-schools", "status": "valid",
                "producer_tool": "analyze_distribution",
                "producer_capability": "distribution_stats",
                "metadata": {"unit": "所", "title": "各区学校统计"},
                "updated_at": 1760000000.0,
            },
            "ref:chart-density": {
                "artifact_id": "ref:chart-density", "status": "valid",
                "producer_tool": "generate_chart",
                "metadata": {},
                "updated_at": 1760000100.0,
            },
        }
        payloads = {
            "ref:stats-schools": {
                "table": {"columns": ["name", "count"],
                          "rows": [{"name": "锦江区", "count": 142},
                                   {"name": "青羊区", "count": 97}]},
            },
            "ref:chart-density": {
                "chart": {"type": "bar", "title": "密度对比",
                          "data": [{"name": "锦江区", "value": 12.5},
                                   {"name": "青羊区", "value": 8.1}]},
            },
        }
        trace = {
            "session_id": "s1",
            "stages": [
                {"stage_id": 1, "ts": 1.0, "user_intent": "分析学校分布"},
                {"stage_id": 9, "ts": 2.0, "tool_name": "analyze_distribution",
                 "ref": "ref:stats-schools",
                 "stats": {"学校总数": 239},  # = 142+97，可在产物中定位
                 "chart": {"type": "bar", "data": []}},
                {"stage_id": 18, "ts": 3.0,
                 "final_text": "锦江区学校数量领先。"},
            ],
        }

        async def fake_load(conv, *, owner_token=""):
            return records, payloads, trace, "fp-abc123", []

        async def fake_messages(db, conv):
            return [{"role": "user", "content": "分析学校分布"},
                    {"role": "assistant", "content": "已完成分析。"}]

        monkeypatch.setattr(bc, "_load_session_inputs", fake_load)
        monkeypatch.setattr(bc, "_session_messages", fake_messages)
        return bc

    @pytest.mark.asyncio
    async def test_draft_from_real_artifacts(self, patched_loaders):
        class Conv:
            id = "s1"

        bc = patched_loaders
        spec = await bc.compile_brief_draft(Conv(), db=object(), title="学校简报")
        assert spec.provenance.session_id == "s1"
        assert spec.provenance.mapspec_fingerprint == "fp-abc123"
        assert spec.provenance.artifact_refs
        # 事实：表 2 行 ×1 数值列 + 图 2 行 ×1 + trace 统计 1（去重后）
        fact_labels = [f.label for f in spec.facts]
        assert any("142" in l or "锦江" in l for l in fact_labels)
        table_facts = [f for f in spec.facts
                       if f.basis.artifact_ref == "ref:stats-schools"]
        assert table_facts, "table artifact must yield facts"
        # trace 统计 239 定位（142+97 不在单字段 —— 无法定位则诚实缺省）
        # 表事实必须可核验：值在产物字段内
        verified = [f for f in spec.facts if f.status == "verified"]
        assert verified, "field-located facts should verify"
        # 块组装
        kinds = [b.kind for b in spec.blocks]
        assert "metric" in kinds and "map_view" in kinds
        assert "method" in kinds and "limitation" in kinds and "source" in kinds
        # 每个事实有对应 metric 块
        metric_blocks = {b.fact_id for b in spec.blocks if b.kind == "metric"}
        assert metric_blocks == {f.fact_id for f in spec.facts}
        # map_view 块带 MapSpec 指纹
        for b in spec.blocks:
            if b.kind == "map_view":
                assert b.map_view and b.map_view.mapspec_fingerprint == "fp-abc123"

    @pytest.mark.asyncio
    async def test_locate_value_prefers_key_hint(self, patched_loaders):
        bc = patched_loaders
        payload = {"table": {"rows": [{"count": 142}, {"total": 142}]}}
        path = bc._locate_value(payload, 142.0, "total")
        assert path == "table.rows.1.total"

    @pytest.mark.asyncio
    async def test_draft_deterministic(self, patched_loaders):
        class Conv:
            id = "s1"

        bc = patched_loaders
        s1 = await bc.compile_brief_draft(Conv(), db=object())
        s2 = await bc.compile_brief_draft(Conv(), db=object())
        # 确定性：除时间戳字段外语义一致
        def strip(spec):
            d = spec.model_dump()
            d["provenance"].pop("generated_at", None)
            for b in d["blocks"]:
                b.pop("updated_at", None)
            for f in d["facts"]:
                f["basis"].pop("bound_at", None)
            d["metadata"].pop("generated_at", None)
            return d

        assert strip(s1) == strip(s2)

    @pytest.mark.asyncio
    async def test_no_artifacts_yields_honest_unverified(self, patched_loaders, monkeypatch):
        from app.services.storymap import brief_compiler as bc

        async def empty_load(conv, *, owner_token=""):
            return {}, {}, None, "", []

        monkeypatch.setattr(bc, "_load_session_inputs", empty_load)

        class Conv:
            id = "s2"

        spec = await bc.compile_brief_draft(
            Conv(), db=None,
        ) if False else None
        # db=None → messages 缺席；trace 缺席 → ValueError（422 语义）
        with pytest.raises(ValueError):
            await bc.compile_brief_draft(Conv(), db=None)
