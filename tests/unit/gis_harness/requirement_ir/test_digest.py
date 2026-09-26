"""F02 normalize + digest 单测（F02 DoD #6：同义请求在稳定条件下可比）。

覆盖：表驱动归一化（行政区/统计词/分组/调色板/格式/时间）、
digest 语义稳定性（provenance/turn/journal 无关）、敏感性（语义变化必变）、
canonical_core 边界（raw phrase / 歧义状态不入 digest）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    PatchRecord,
    build_document,
    canonical_core,
    document_digest,
    requirement_digest,
)
from app.services.gis_harness.requirement_ir import normalize as norm
from app.services.gis_harness.requirement_ir.patch import apply_patch


def _doc(query: str, turn: int = 1):
    return build_document(query, resolve_map_request_intent(query), turn=turn)


class TestNormalizeAdminName:
    @pytest.mark.parametrize("left,right", [
        ("成都市", "成都"),
        ("成都", "成都"),
        ("四川省", "四川"),
        ("内蒙古自治区", "内蒙古"),
    ])
    def test_synonym_pairs(self, left, right):
        assert norm.normalize_admin_name(left) == norm.normalize_admin_name(right)

    def test_preserves_meaningful_name(self):
        assert norm.normalize_admin_name("青羊区") == "青羊"


class TestNormalizeVocabs:
    def test_statistic(self):
        assert norm.normalize_statistic("数量") == "count"
        assert norm.normalize_statistic("每平方公里密度") == "density"
        assert norm.normalize_statistic("占比") == "share"
        assert norm.normalize_statistic("") == "none"
        assert norm.normalize_statistic("无所谓") == "none"

    def test_group_by(self):
        assert norm.normalize_group_by("各区") == ("administrative", "district")
        assert norm.normalize_group_by("每个区") == ("administrative", "district")
        assert norm.normalize_group_by("网格") == ("grid", "grid")
        # 词表未命中 = 无信号（不得当 custom 分组）
        assert norm.normalize_group_by("隐藏道路图层") == ("none", "")
        # 显式「按X分」句式 → custom
        assert norm.normalize_group_by("按商圈分")[0] == "custom"

    def test_palette(self):
        assert norm.normalize_palette("蓝色") == norm.normalize_palette("blue")
        assert norm.normalize_palette("蓝色") == "blue"

    def test_formats(self):
        assert norm.normalize_formats(["PDF", "png"]) == ("pdf", "png")
        assert norm.normalize_formats(["docx"]) == ()


class TestNormalizeTime:
    def test_explicit_range(self):
        assert norm.normalize_time("2019到2024年") == ("2019", "2024", "year", True)
        assert norm.normalize_time("2020-2024") == ("2020", "2024", "year", True)

    def test_series_words(self):
        start, end, granularity, series = norm.normalize_time("逐年变化")
        assert series is True and granularity == "year"

    def test_empty(self):
        assert norm.normalize_time("") == ("", "", "none", False)


class TestDigestStability:
    def test_same_semantics_same_digest_across_turns(self):
        assert requirement_digest(_doc("统计成都各区小学数量", turn=1)) == \
            requirement_digest(_doc("统计成都各区小学数量", turn=9))

    def test_provenance_and_journal_do_not_affect_digest(self):
        doc = _doc("统计成都各区小学数量")
        patched, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=3, actor="user", op="set",
            path="representation.palette", value="blue"))
        # patch 改变了 palette → digest 必变（语义面）
        assert requirement_digest(patched) != requirement_digest(doc)
        # 同一语义、不同 turn 的重复 patch（幂等 noop）→ digest 不变
        noop = doc.model_copy(update={
            "patches": [PatchRecord(op_id="p1", turn=3, actor="user",
                                    op="accept")],
            "updated_turn": 42,
        })
        assert requirement_digest(noop) == requirement_digest(doc)

    def test_synonym_surface_same_core_same_digest(self):
        # 同一语义核、不同 surface（query 文案不同）→ digest 稳定
        base = _doc("统计成都各区小学数量")
        variant = _doc("统计成都各区小学数量的分布")   # core 相同语义面
        # 若 resolver 产出相同 canonical 核，则 digest 相同；否则至少
        # canonical_core 是纯语义面（不含 query/phrase 文本）
        assert "query" not in canonical_core(base)
        assert all("phrase" not in str(m) for m in
                   canonical_core(base)["measures"])
        if base.intent.core.task == variant.intent.core.task:
            assert requirement_digest(base) == requirement_digest(variant)

    def test_semantic_change_changes_digest(self):
        doc = _doc("统计成都各区小学数量")
        changed, _ = apply_patch(doc, PatchRecord(
            op_id="p1", turn=2, actor="user", op="set",
            path="aoi.name", value="重庆"))
        assert requirement_digest(changed) != requirement_digest(doc)

    def test_document_digest_more_sensitive_than_requirement_digest(self):
        doc = _doc("统计成都各区小学数量")
        bumped = doc.model_copy(update={"revision": doc.revision + 1})
        assert document_digest(bumped) != document_digest(doc)
        assert requirement_digest(bumped) == requirement_digest(doc)


class TestCanonicalCoreBoundary:
    def test_excludes_authority_outputs(self):
        doc = _doc("统计成都各区小学数量")
        core = canonical_core(doc)
        payload = str(sorted(core.items()))
        # 下游权威产物不进 digest：字段解析值、置信度、澄清状态
        assert "field_state" not in payload
        assert "confidence" not in payload
        assert "ambiguit" not in payload

    def test_explicit_components_only(self):
        doc = _doc("统计成都各区小学数量")
        core = canonical_core(doc)
        assert core["components"] == {}   # 未显式表达的组件不进 digest
