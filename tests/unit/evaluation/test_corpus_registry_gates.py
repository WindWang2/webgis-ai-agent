"""语料注册/漂移 gate 层（#1552 测试债清偿，G08 后续）。

test_consumption_contract.py 锁定了 manifest 形状与 18 条目计数，
test_v2_case_manifest.py 锁定了 benchmark 8 语料的跨语料 id 唯一性；
本文件补齐**注册层自身的漂移 gate**（语料即基准，注册表即契约）：

- 注册完整性：每个语料模块 + golden 语料都应在 manifest 受管
  （当前缺 4 个 → xfail 暴露，生产侧补登记后转 XPASS 强制摘除）；
- id 链完整性：任一注册语料的每个案例都应有非空 id
  （runtime_situation 的 3,456 案例只有 ``runtime_id``，
  不在 index 的 id→case_id→scenario_id 链上 → xfail 暴露）；
- version_hash 排序稳定性：runtime 语料哈希不得依赖构建顺序
  （id 链空键 → 当前顺序敏感 → xfail 暴露）；
- 跨全部 18 注册语料的 id 全局唯一（既有 gate 只覆盖 benchmark 8 语料）；
- 前缀影子检测：语料 X 的实际 id 不得以另一语料 Y 的登记前缀开头
  （retrieval_eval 的 EV-/EV6- 与 evidence_grounding 登记的 EV- 构成
  结构性碰撞向量 → xfail 暴露）；
- 每语料实际 id 首段 pin（17 语料回归锁 + runtime 预期前缀 xfail）；
- 冻结基线：manifest 全量深比对（count/version_hash/prefix/group）
  + golden 语料内容哈希 pin + golden id 集合精确 {G1..G33}；
- 交叉引用：golden fixture_aliases ⊆ FIXTURE_BUILDERS∪{ndvi_pair}
  （scripted 案例不得走 quantities 路由）、scenarios 的 contract_cases
  必须落在 workflow contract 语料内、scenario_corpus 双路径等价。

全部用例零 LLM/DB/网络（纯语料构建；首次 manifest 调用后由
``index._BUILDER_CACHE`` 摊平成本）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.evaluation import index

_BASELINES = Path(__file__).parent / "baselines"
_GOLDEN_HASH_BASELINE = "cdba715259ab10ba63b83ea70cd71e92"

#: 注册目标态：每个语料模块 + golden 语料都应受 manifest 管理。
#: 当前 index._ensure_builtin_registrations 只登记 18 条（缺
#: golden / chaos / closed_loop / retrieval 四个语料模块）。
_EXPECTED_REGISTRATION_NAMES = {
    "anti_claim_plan", "workflow_contract", "golden_matrix",
    "conformance", "evidence_grounding", "failure_taxonomy",
    "goal_satisfaction", "hard_negative", "methodology",
    "mission_scenario", "cartography_axes", "quality_scenario",
    "reliability", "retrieval_eval", "runtime_situation",
    "security_injection", "skill_policy", "scenario",
    "golden", "chaos", "closed_loop", "retrieval",
}

#: 每语料实际 id 的首段词表 pin（2026-10-02 实测锁定）。
#: 首段 = id 开头的连续字母（"M001"→M、"EV6-D1"→EV6、
#: "desc.schools_zh"→desc、"basic/x-y"→basic）。
_PREFIX_ALLOWLIST = {
    "anti_claim_plan": {"AC"},
    "cartography_axes": {"CARTX"},
    "conformance": {"CF"},
    "evidence_grounding": {"EV"},
    "failure_taxonomy": {"FL"},
    "goal_satisfaction": {"GC"},
    "golden_matrix": {"C", "D", "F", "M", "N", "P", "S"},
    "hard_negative": {"HN"},
    "methodology": {"change", "compose", "dens", "desc", "interp",
                    "mcda", "net", "prox", "rs", "stats", "suit",
                    "terrain", "zonal"},
    "mission_scenario": {"MSN"},
    "quality_scenario": {"QS"},
    "reliability": {"basic", "failure", "lifecycle", "noprogress",
                    "repair", "security"},
    "retrieval_eval": {"EV", "EV6"},
    "scenario": {"SC"},
    "security_injection": {"SEC"},
    "skill_policy": {"SP"},
    "workflow_contract": {"WC"},
}


def _id_of(case) -> str:
    """与 index.py 相同的 id 链（id → case_id → scenario_id）。"""
    return str(getattr(case, "id", "") or getattr(case, "case_id", "")
               or getattr(case, "scenario_id", ""))


def _first_segment(cid: str) -> str:
    m = re.match(r"[A-Za-z]+", cid)
    return m.group(0) if m else "<none>"


def _all_case_ids() -> dict:
    """name → [全部案例 id]（18 注册语料，含非 benchmark 语料）。"""
    out = {}
    for reg in index._all_registrations():
        out[reg.name] = [_id_of(c) for c in index.corpus_cases(reg)]
    return out


class TestRegistrationCompleteness:
    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：chaos/closed_loop/retrieval 语料模块与 "
               "golden_cases 未登记进 index 注册表，无 manifest 计数与 "
               "version_hash 漂移跟踪；生产补登记后本 gate 转 XPASS",
    )
    def test_every_corpus_module_registered(self):
        names = {r.name for r in index._all_registrations()}
        missing = _EXPECTED_REGISTRATION_NAMES - names
        assert not missing, f"未登记语料: {sorted(missing)}"


class TestIdChainAndHash:
    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：runtime_situation 的 3,456 案例 id 为 "
               "runtime_id，不在 index 的 id 链上（链提取全空）；"
               "生产把 runtime_id 纳入 id 链后转 XPASS",
    )
    def test_id_chain_nonempty_for_all_registered_cases(self):
        for name, ids in _all_case_ids().items():
            empty = [i for i in ids if not i]
            assert not empty, (
                f"语料 {name} 有 {len(empty)} 个案例 id 链提取为空")

    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：runtime 语料 id 链全空 → version_hash "
               "排序键恒为 ''（稳定排序 = 构建顺序敏感），同语料不同顺序"
               "哈希不同；id 链修复后转 XPASS",
    )
    def test_runtime_version_hash_order_independent(self):
        from app.evaluation.runtime_corpus import build_runtime_corpus

        cases = build_runtime_corpus()
        assert index.corpus_version_hash(cases) \
            == index.corpus_version_hash(list(reversed(cases)))

    def test_cross_corpus_id_uniqueness_all_registrations(self):
        # 既有 gate（test_v2_case_manifest）只覆盖 benchmark 8 语料；
        # 这里推广到全部 18 条注册（非空 id 全局唯一，2026-10-02 实测
        # 0 碰撞）。空 id 由 id-chain xfail gate 单独锁定（runtime
        # 语料 3,456 个空链 id 不在本 gate 计数内）。
        flat = [i for ids in _all_case_ids().values() for i in ids if i]
        assert len(flat) == len(set(flat))

    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：retrieval_eval 的 306 个 EV-* id 以 "
               "evidence_grounding 登记的 EV- 前缀开头 —— 现有 manifest "
               "前缀检查（按登记串精确比对）与 benchmark-only 唯一性检查"
               "都探测不到该碰撞向量；生产重命名 id 或改登记前缀后转 XPASS",
    )
    def test_no_cross_prefix_shadowing(self):
        all_ids = _all_case_ids()
        for reg in index._all_registrations():
            for other in index._all_registrations():
                if other.name == reg.name:
                    continue
                shadow = [i for i in all_ids[reg.name]
                          if i.startswith(other.prefix)]
                assert not shadow, (
                    f"语料 {reg.name} 有 {len(shadow)} 个 id 以语料 "
                    f"{other.name} 的登记前缀 {other.prefix!r} 开头: "
                    f"{sorted(shadow)[:3]}")

    @pytest.mark.parametrize("name", sorted(_PREFIX_ALLOWLIST))
    def test_prefix_allowlist_matches_actual_ids(self, name):
        segs = {_first_segment(i) for i in _all_case_ids()[name]}
        assert segs <= _PREFIX_ALLOWLIST[name], (
            f"语料 {name} 出现未登记首段: {sorted(segs - _PREFIX_ALLOWLIST[name])}")

    @pytest.mark.xfail(
        strict=True,
        reason="生产缺口（#1552）：runtime 案例 id 链为空（首段 <none>）；"
               "生产把 runtime_id（RUNTIME-*）纳入 id 链后转 XPASS",
    )
    def test_runtime_ids_carry_expected_prefix(self):
        from app.evaluation.runtime_corpus import build_runtime_corpus

        segs = {_first_segment(_id_of(c))
                for c in build_runtime_corpus()}
        assert segs == {"RUNTIME"}, f"实际首段: {sorted(segs)}"


class TestPinnedBaselines:
    def test_manifest_matches_pinned_baseline(self):
        # 冻结基线（write_manifest 同构 JSON；跨进程确定性已验证）——
        # 语料任何内容/数量漂移都会在此报警，迫使基线显式更新。
        baseline = json.loads(
            (_BASELINES / "corpus_manifest.json").read_text(encoding="utf-8"))
        assert index.corpus_manifest() == baseline

    def test_golden_corpus_hash_pinned(self):
        # golden 语料是唯一 execute-tier 语料且不在注册表内 ——
        # 单独 pin 内容哈希，作为它的漂移锁。
        from app.evaluation.golden_cases import GOLDEN_CASES

        assert index.corpus_version_hash(list(GOLDEN_CASES)) \
            == _GOLDEN_HASH_BASELINE

    def test_golden_id_set_exact(self):
        from app.evaluation.golden_cases import GOLDEN_CASES

        assert len(GOLDEN_CASES) == 33
        assert {c.id for c in GOLDEN_CASES} == {f"G{i}" for i in range(1, 34)}


class TestGoldenScenarioWiring:
    def test_golden_fixture_alias_resolution(self):
        from app.evaluation.fixtures import FIXTURE_BUILDERS
        from app.evaluation.golden_cases import GOLDEN_CASES

        for c in GOLDEN_CASES:
            aliases = set(c.fixture_aliases or [])
            # 全量别名必须可解析（ndvi_pair 走 quantities 路由，
            # 刻意不在 FIXTURE_BUILDERS 内）
            assert aliases <= set(FIXTURE_BUILDERS) | {"ndvi_pair"}, c.id
            # scripted（execute-tier）案例只能走 FIXTURE_BUILDERS
            if c.script:
                assert aliases <= set(FIXTURE_BUILDERS), c.id

    def test_scenarios_contract_cases_wired(self):
        from app.evaluation.anti_claim import build_workflow_contract_cases
        from app.evaluation.scenarios import build_scenarios

        scs = build_scenarios()
        assert len(scs) == 7
        assert len({s.scenario_id for s in scs}) == 7
        wc_ids = {c.case_id for c in build_workflow_contract_cases()}
        for s in scs:
            for cc in s.contract_cases:
                assert cc.case_id in wc_ids, (
                    f"场景 {s.scenario_id} 引用了不存在的契约案例 "
                    f"{cc.case_id!r}")

    def test_scenario_corpus_dual_path_and_coverage(self):
        from app.evaluation.scenario_corpus import (
            MIN_CORPUS_SIZE,
            build_scenario_corpus,
            coverage_report,
        )

        def t5(cases):
            return sorted(
                (c.case_id, c.query, c.family, c.expected, c.lang)
                for c in cases)

        # 注册表校验开关不得改变语料内容（index 注册用 False，
        # 测试侧用 True —— 双路径等价是隐含契约）
        assert t5(build_scenario_corpus()) \
            == t5(build_scenario_corpus(registry_validate=False))
        report = coverage_report()
        assert report["registry_missing"] == []
        assert report["total"] >= MIN_CORPUS_SIZE
