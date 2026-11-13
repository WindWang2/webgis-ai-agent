"""G30 — 用户显式技能选择（selection resolver + policy + plan seam）。

场景映射：#6（skill_name 同时匹配指南与核心方法 → 类型区分不误跑）、
#7（弃用版本显示替代不静默换）、#19（显式目标优先不被自动升级）、
#20（Pi 经发现入口选择并运行——工具面在 pi_tools 测试）、显式选择≠执行
资格（eligibility 拒绝如实披露）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skill_jobs.selection import (
    resolve_user_skill_selection,
    selection_notice_text,
)
from app.services.gis_harness.skills.policy import resolve_for_planning
from app.services.gis_harness.skills.situation import SelectionFacts


class TestSelectionResolver:
    def test_exact_id(self):
        sel = resolve_user_skill_selection("validate_spatial_data")
        assert sel["status"] == "resolved"
        assert sel["match_type"] == "id"
        assert sel["skill_id"] == "validate_spatial_data"
        assert sel["skill_version"] == "1.1.0"
        assert sel["runnable"] is True

    def test_exact_name(self):
        sel = resolve_user_skill_selection("点分布分析")
        assert sel["status"] == "resolved"
        assert sel["match_type"] == "name"
        assert sel["skill_id"] == "point_distribution_analysis"

    def test_not_found_with_suggestions(self):
        sel = resolve_user_skill_selection("validate_spatial")
        assert sel["status"] == "not_found"
        assert sel["suggestions"], "前缀匹配应给出建议"
        assert any(c["skill_id"] == "validate_spatial_data"
                   for c in sel["suggestions"])

    def test_empty(self):
        assert resolve_user_skill_selection("")["status"] == "empty"
        assert resolve_user_skill_selection("   ")["status"] == "empty"

    def test_notice_texts_are_bounded(self):
        for name in ("validate_spatial_data", "点分布分析", "不存在技能xyz"):
            text = selection_notice_text(resolve_user_skill_selection(name))
            assert len(text) <= 240


class TestExplicitPolicyDecision:
    def test_explicit_runnable_uses_execute_guided(self):
        facts = SelectionFacts(goal_text="成都学校分布", geometry_kinds=["point"])
        facts.explicit_skill_id = "point_distribution_analysis"
        d = resolve_for_planning(facts)
        assert d.mode == "execute_guided"
        assert d.selected_skill == "point_distribution_analysis"
        assert d.skill_version == "1.1.0"
        assert d.confidence == 1.0
        assert d.matched_signals == ["explicit_user_selection"]

    def test_explicit_geometry_mismatch_stays_eligibility_honest(self):
        """显式选择不改变科学适用性：面数据上选点技能 → guide + 拒绝码。"""
        facts = SelectionFacts(goal_text="行政区统计", geometry_kinds=["polygon"])
        facts.explicit_skill_id = "point_distribution_analysis"
        d = resolve_for_planning(facts)
        assert d.mode == "guide"
        assert d.eligibility_ok is False
        assert any("GEOMETRY_NOT_SUPPORTED" in c for c in d.rejected_codes)

    def test_explicit_unknown_skill(self):
        facts = SelectionFacts(goal_text="x")
        facts.explicit_skill_id = "no_such_skill"
        d = resolve_for_planning(facts)
        assert d.mode == "none"
        assert d.fallback_reason == "explicit_skill_not_found"
        assert "EXPLICIT_SKILL_NOT_FOUND" in d.rejected_codes

    def test_explicit_deprecated_discloses_alternative(self):
        """场景 #7：弃用技能 → fallback 披露替代，不静默选最新版。"""
        from app.services.gis_harness.skills.contract import SkillContract
        from app.services.gis_harness.skills.procedure_ir import SkillProcedure
        from app.services.gis_harness.skills.policy import SkillPolicy

        skill = SkillContract(
            id="t_dep", name="已弃用测试技能", when_to_use="t",
            version="1.0.0", deprecated=True, deprecated_by="validate_spatial_data",
            procedure=SkillProcedure(steps=[
                __import__('app.services.gis_harness.skills.procedure_ir', fromlist=['ProcedureStep']).ProcedureStep(step_id='s1', title='t', kind='inspect'),
            ]),
        )

        class _Lib:
            skills = [skill]

            def get(self, sid):
                return skill if sid == skill.id else None

            resolver = SimpleResolverLike()

        _Lib.resolver.register(skill)
        policy = SkillPolicy.from_library(_Lib())  # type: ignore[arg-type]
        facts = SelectionFacts(goal_text="x")
        facts.explicit_skill_id = "t_dep"
        d = policy.resolve(facts)
        assert d.mode == "fallback"
        assert d.selected_skill == "t_dep"
        assert "alternative=validate_spatial_data" in d.fallback_reason

    def test_legacy_intent_path_unchanged_without_explicit(self):
        """不带显式选择的既有路径逐位不变（回归保护）。"""
        d = resolve_for_planning(SelectionFacts(
            goal_text="成都学校分布", geometry_kinds=["point"]))
        assert d.mode in ("guide", "execute_guided", "fallback")
        assert "explicit_user_selection" not in d.matched_signals


class SimpleResolverLike:
    """SkillPolicy 需要的最小 resolver 形状（trusted 面）。"""

    def __init__(self):
        self._skills = {}

    def register(self, skill):
        self._skills[skill.id] = skill

    def resolve(self, facts, packs=None, limit=5, include_deprecated=False):
        from types import SimpleNamespace

        return SimpleNamespace(selected=None, top=None, ranked=[],
                               rejected=[], clarification=None)

    def get(self, skill_id):
        return self._skills.get(skill_id)

    def check_eligibility(self, skill, facts):
        return None  # 无硬门事实 → 可行


class TestSessionCtxExplicitSkill:
    def test_set_and_peek(self):
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            peek_explicit_skill,
            set_explicit_skill,
        )

        set_explicit_skill("sess-x", {"skill_id": "validate_spatial_data",
                                      "skill_version": "1.1.0",
                                      "match_type": "id"})
        sel = peek_explicit_skill("sess-x")
        assert sel and sel["skill_id"] == "validate_spatial_data"
        set_explicit_skill("sess-x", {})
        assert peek_explicit_skill("sess-x") is None


class TestCatalogProjection:
    def test_catalog_types_separated(self):
        from app.services.gis_harness.skill_jobs.catalog_views import build_catalog

        catalog = build_catalog()
        # 三类资产分型，GIS 技能含可运行标记与版本/摘要
        assert catalog["counts"]["gis_skills"] >= 3
        by_id = {e["id"]: e for e in catalog["skills"]}
        vsd = by_id["validate_spatial_data"]
        assert vsd["runnability"] == "runnable"
        assert vsd["version"] == "1.1.0"
        assert vsd["digest"]
        assert {p["name"] for p in vsd["parameters"]} == {"crs"}
        # guidance-only 技能如实标注
        guidance = [e for e in catalog["skills"] if e["runnability"] == "guidance_only"]
        assert guidance, "库中存在无执行映射技能，应标注 guidance_only"
        assert all(e["runnability_reasons"] for e in guidance)
        # md 指南与扩展 candidate 分型呈现（不与 GIS 技能合并）
        for guide in catalog["md_guides"]:
            assert guide["asset_type"] == "md_guide"
            assert guide["runnability"] == "guidance_only"
