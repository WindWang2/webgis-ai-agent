"""语料 schema 字段 pin 与表级不变量（#1552 测试债清偿，G08 后续）。

既有 gate 覆盖了规模/词表/行为面；本文件把**字段级契约**钉死为回归锁
（schema 即契约：字段增删/词表漂移必须显式改测试）：

- case_matrix：id 七块（M/N/F/S/D/C/P）各自从 1 连续编号且块集精确、
  _TASK_OVERRIDES 键必须命中矩阵 query（override 真实生效）；
- 字段 pin：runner.CaseResult 10 字段、GISBenchmarkCase.group 40 值
  Literal、report._METRIC_ORDER 29 键、retrieval_eval 生效的
  MIN_EVAL_CORPUS_SIZE（源码双定义，pin 生效值）；
- 表级不变量：conformance 60 家族表（family_id 唯一 / group 词表 /
  expected_task+expected_recipe 非空 / 家族 group ∈ 案例 group 词表）、
  methodology 20 案例 id 形状与首段词表、reliability 19 案例
  id 路径形状与类别闭合词表；
- chaos 语料的 pytest node pin 唯一性（当前存在重复 pin → xfail 暴露）。

全部用例零 LLM/DB/网络（纯语料构建）。
"""
from __future__ import annotations

import re

import pytest


class TestCaseMatrixGate:
    def test_id_blocks_exact_and_contiguous(self):
        from app.evaluation.case_matrix import build_matrix_cases

        matrix = build_matrix_cases()
        blocks: dict = {}
        for c in matrix:
            m = re.fullmatch(r"([A-Z])(\d+)", c.id)
            assert m, f"矩阵 id 形状异常: {c.id}"
            blocks.setdefault(m.group(1), []).append(int(m.group(2)))
        # 块集精确（不多不少七块），每块从 1 连续编号
        assert set(blocks) == {"M", "N", "F", "S", "D", "C", "P"}
        for letter, nums in blocks.items():
            assert sorted(nums) == list(range(1, len(nums) + 1)), (
                f"{letter} 块编号不连续: {sorted(nums)[:5]}...")
        assert len(matrix) == sum(len(v) for v in blocks.values()) == 273

    def test_task_overrides_keys_fire_in_matrix(self):
        from app.evaluation.case_matrix import _QUERIES, _TASK_OVERRIDES

        # override 表以 query 为键 —— 键必须是矩阵里真实存在的 query，
        # 否则 override 静默失效（当前 1 条：DEM 地形）。
        queries = {q for fam in _QUERIES.values() for q in fam}
        assert _TASK_OVERRIDES
        for key in _TASK_OVERRIDES:
            assert key in queries, f"override 键未命中矩阵 query: {key!r}"


class TestSchemaFieldPins:
    def test_caseresult_fields_pinned(self):
        from app.evaluation.runner import CaseResult

        assert list(CaseResult.model_fields) == [
            "case_id", "group", "name", "status", "passed", "metrics",
            "plan_evidence", "failures", "skipped_reason", "elapsed_ms",
        ]

    def test_group_literal_pinned(self):
        from app.evaluation.case import GISBenchmarkCase

        # 40 值闭包词表（12 基础 + 16 conformance-* + 1 anti-claim +
        # 5 quality-* + 6 benchmark/轴）——词表增删必须显式改这里。
        assert GISBenchmarkCase.model_fields["group"].annotation.__args__ \
            == (
                "poi", "raster", "network", "od", "repair", "semantics",
                "interpolation", "decision", "negative", "form", "scope",
                "compound",
                "conformance-distribution", "conformance-density",
                "conformance-statistics", "conformance-point-pattern",
                "conformance-interpolation", "conformance-terrain",
                "conformance-hydrology", "conformance-remote-sensing",
                "conformance-sar", "conformance-temporal",
                "conformance-change", "conformance-network",
                "conformance-accessibility", "conformance-equity",
                "conformance-decision", "conformance-contract",
                "anti-claim", "quality-basemap", "quality-science",
                "quality-cartography", "quality-data", "quality-agent",
                "hard-negative", "benchmark-policy", "benchmark-security",
                "benchmark-evidence", "benchmark-mission",
                "cartography-axes",
            )

    def test_metric_order_pinned(self):
        from app.evaluation.report import _METRIC_ORDER

        assert list(_METRIC_ORDER) == [
            "task_correct", "capability_precision", "capability_recall",
            "algorithm_correct", "methodology_honesty_ok",
            "ontology_top1_correct", "recipe_selection_correct",
            "no_false_professional_analysis", "qualification_states_correct",
            "fallback_tier_correct", "planning_deterministic",
            "unnecessary_tool_count", "numerical_correct",
            "artifact_contract_valid", "map_product_complete",
            "render_verified", "tool_call_count", "retry_count",
            "reused_artifact_count", "elapsed_ms", "scope_binding_correct",
            "allowed_tools_ok", "turns_correct", "coreference_binding_ok",
            "policy_mode_correct", "policy_deterministic",
            "injection_contained", "evidence_grounding_correct",
            "evidence_positive_proof_ok",
        ]

    def test_retrieval_eval_min_size_effective_value(self):
        from app.evaluation.retrieval_eval_corpus import (
            MIN_EVAL_CORPUS_SIZE,
        )

        # 源码内 MIN_EVAL_CORPUS_SIZE 被定义两次（300 被 350 覆盖）——
        # pin 生效值；收敛双定义时须同步此 pin。
        assert MIN_EVAL_CORPUS_SIZE == 350


class TestFamilyAndCorpusTables:
    def test_conformance_family_table_invariants(self):
        from app.evaluation.case import GISBenchmarkCase
        from app.evaluation.conformance import CONFORMANCE_FAMILIES

        assert len(CONFORMANCE_FAMILIES) == 60
        family_ids = [f.family_id for f in CONFORMANCE_FAMILIES]
        assert len(set(family_ids)) == 60
        # 家族 group 必须落在案例 group 词表的 conformance-* 子集内
        # （domain → group 是多对一折叠，如 point_pattern →
        # conformance-point-pattern、site_selection → conformance-decision）
        allowed = {g for g in
                   GISBenchmarkCase.model_fields["group"].annotation.__args__
                   if g.startswith("conformance-")}
        for f in CONFORMANCE_FAMILIES:
            assert f.group in allowed, (
                f"{f.family_id}: group {f.group!r} 不在 conformance 词表内")
            assert f.expected_task and f.expected_recipe, f.family_id

    def test_methodology_id_shape_and_segments(self):
        from app.evaluation.methodology_corpus import METHODOLOGY_CORPUS

        assert len(METHODOLOGY_CORPUS) == 20
        segs = set()
        for c in METHODOLOGY_CORPUS:
            m = re.fullmatch(r"[a-z]+\.[a-z0-9_]+", c.case_id)
            assert m, f"methodology id 形状异常: {c.case_id}"
            segs.add(c.case_id.split(".", 1)[0])
        # 首段 = 家族缩写（13 族，20 案例双语分布）
        assert segs == {
            "change", "compose", "dens", "desc", "interp", "mcda", "net",
            "prox", "rs", "stats", "suit", "terrain", "zonal",
        }

    def test_reliability_corpus_id_shape_and_categories(self):
        from app.evaluation.reliability_corpus import build_reliability_corpus

        cases = build_reliability_corpus()
        assert len(cases) == 19
        cats = set()
        for c in cases:
            m = re.fullmatch(r"([a-z]+)/[a-z0-9-]+", c.case_id)
            assert m, f"reliability id 形状异常: {c.case_id}"
            cats.add(m.group(1))
            assert len(c.script) >= 1, c.case_id
        # 类别闭合词表（与 RL-* 登记前缀不同：实际 id 为 category/name）
        assert cats == {
            "basic", "failure", "lifecycle", "noprogress", "repair",
            "security",
        }

    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：chaos 语料 17 条 pin 只有 16 个唯一 "
               "pytest node —— CH-cancel-no-retry 与 CH-pi-bridge-cancellation "
               "指向同一 node（test_durable_context_continuation_v6.py::"
               "test_continuation_unrecoverable_failure_aborts）；"
               "生产去重后转 XPASS",
    )
    def test_chaos_node_pins_unique(self):
        from app.evaluation.chaos_corpus import build_chaos_corpus
        from app.evaluation.chaos_corpus import corpus_node_ids

        nodes = corpus_node_ids()
        assert len(nodes) == len(build_chaos_corpus()) == 17
        dupes = {n for n in nodes if nodes.count(n) > 1}
        assert not dupes, f"重复 node pin: {sorted(dupes)}"
