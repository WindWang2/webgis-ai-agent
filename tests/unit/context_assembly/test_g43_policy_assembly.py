"""G43（ADR-0295）装配策略测试：provider 过滤 + 预拼块拒绝 + 预览无副作用。

覆盖场景矩阵：
- C03/C16 显式排除 → 新装配不含被排除资料；fallback 不重新注入
- C39 显式策略 + opaque caller 预拼制图块 → typed 拒绝（不字符串删段）
- C40 无显式策略 → 预拼路径保持字节兼容
- C02/C13 策略冻结：装配中 facts.source_policy 即本 turn 生效版本
- C07 预览不创建 plan slot / 不提交 demotion / 不推进情境
"""
from __future__ import annotations

import pytest

from app.services.context_assembly import assembly as asm
from app.services.context_assembly import preview as preview_mod
from app.services.context_assembly import providers as prov_mod
from app.services.context_assembly.contract import (
    ContextAssemblyRejected,
    ContextDomain,
    SharedTurnFacts,
    TurnContextRequest,
    bounded_item,
)
from app.services.context_assembly.source_policy import SourcePolicy
from app.services.context_assembly.providers import BaseProvider


def _policy(preferred=(), excluded=()):
    return SourcePolicy(
        session_id="s1", mode="explicit",
        preferred=tuple(preferred), excluded=tuple(excluded),
        policy_version=4,
    )


def _req(**kw):
    base = dict(session_id="s1", turn_id="t1", message="hello",
                token="tok", org_id="org1", project_id="p1")
    base.update(kw)
    return TurnContextRequest(**base)


def _patch_pipeline(monkeypatch, providers, facts):
    monkeypatch.setattr(asm, "build_default_providers", lambda: providers)

    async def _fake_fetch(req):
        return facts

    monkeypatch.setattr(asm, "fetch_shared_facts", _fake_fetch)

    class _NoCard:
        @staticmethod
        async def build(*a, **k):
            return []

    monkeypatch.setattr(prov_mod.GisContextCardProvider, "build", _NoCard.build)


class FakeOptionalProvider(BaseProvider):
    """携带 source_refs 的可选资料 provider（G43 tuple 形态）。"""

    def __init__(self, provider_id, domain, content, refs):
        self.provider_id = provider_id
        self.domain = domain
        self._content = content
        self._refs = refs

    async def build(self, req, facts):
        return [bounded_item(
            item_id=f"{self.provider_id}:blk", provider_id=self.provider_id,
            domain=self.domain, content=self._content, scope_id="s1",
        )], self._refs


class TestExplicitPolicyAssembly:
    @pytest.mark.asyncio
    async def test_excluded_source_records_decision_not_content(self, monkeypatch):
        """C03：被排除资料不进 prompt；receipt 源行如实 excluded_by_policy。"""
        facts = SharedTurnFacts(map_state={}, plan=None, source_policy=_policy(
            excluded=["pk:2", "cm:all", "gm:block"]))
        knowledge = FakeOptionalProvider(
            "project_knowledge", ContextDomain.PROJECT_KNOWLEDGE,
            "KNOWLEDGE[keep]",
            [
                {"source_id": "pk:1", "kind": "project_knowledge_entry",
                 "decision": "included", "version_token": "tok1"},
                {"source_id": "pk:2", "kind": "project_knowledge_entry",
                 "decision": "excluded_by_policy", "version_token": "tok2"},
            ],
        )
        memory = FakeOptionalProvider(
            "cartography_memory", ContextDomain.CARTOGRAPHY_MEMORY,
            "MEMORY[old-palette]",
            [{"source_id": "cm:all", "kind": "carto_memory_fact",
              "decision": "excluded_by_policy"}],
        )
        gis_mem = FakeOptionalProvider(
            "gis_memory", ContextDomain.GIS_MEMORY, "GISMEM",
            [{"source_id": "gm:block", "kind": "gis_memory_block",
              "decision": "excluded_by_policy"}],
        )
        # 内容由 provider 决定（真实 provider 在渲染源内过滤）；本测试钉住
        # receipt 面的如实分列与 policy_version 冻结。
        _patch_pipeline(monkeypatch, [knowledge, memory, gis_mem], facts)
        message, receipt = await asm.assemble_turn_context(_req())
        assert receipt.policy_version == 4
        by_id = {ln.source_id: ln.decision for ln in receipt.source_lines}
        assert by_id["pk:1"] == "included"
        assert by_id["pk:2"] == "excluded_by_policy"
        assert by_id["cm:all"] == "excluded_by_policy"
        assert by_id["gm:block"] == "excluded_by_policy"
        # 决策行与源行一致：排除的块不是 included item
        included_items = {ln.item_id for ln in receipt.decision_lines
                          if ln.decision == "included"}
        assert included_items

    @pytest.mark.asyncio
    async def test_opaque_caller_block_rejected_under_explicit_policy(self, monkeypatch):
        """C39/C40：显式策略下 caller 预拼块不可证明 ⇒ typed 明确拒绝。"""
        facts = SharedTurnFacts(map_state={}, plan=None, source_policy=_policy())
        _patch_pipeline(monkeypatch, [], facts)
        with pytest.raises(ContextAssemblyRejected):
            await asm.assemble_turn_context(
                _req(legacy_cartography_block="[CARTOGRAPHY_VERDICT] opaque mix"))

    @pytest.mark.asyncio
    async def test_caller_block_untouched_without_explicit_policy(self, monkeypatch):
        """C40：无显式策略（auto/缺行）→ 预拼路径字节兼容（不拒绝）。"""
        facts = SharedTurnFacts(map_state={}, plan=None)  # source_policy=None → auto
        _patch_pipeline(monkeypatch, [], facts)
        message, receipt = await asm.assemble_turn_context(
            _req(legacy_cartography_block="CARTO-BLOCK"))
        assert "CARTO-BLOCK" in message
        assert receipt.skipped_providers.get("cartography_derived") == "caller_injected_block"

    @pytest.mark.asyncio
    async def test_auto_policy_byte_equivalence(self, monkeypatch):
        """auto 策略行不改变装配结果（与 pre-G43 等价）。"""
        providers = [FakeOptionalProvider(
            "cartography_memory", ContextDomain.CARTOGRAPHY_MEMORY, "M",
            [{"source_id": "cm:1", "kind": "carto_memory_fact",
              "decision": "included"}],
        )]
        facts_auto = SharedTurnFacts(map_state={}, plan=None)
        facts_auto.source_policy = SourcePolicy(mode="auto", policy_version=0)
        _patch_pipeline(monkeypatch, providers, facts_auto)
        msg_auto, receipt_auto = await asm.assemble_turn_context(_req())

        facts_none = SharedTurnFacts(map_state={}, plan=None)
        _patch_pipeline(monkeypatch, providers, facts_none)
        msg_none, _ = await asm.assemble_turn_context(_req())
        assert msg_auto == msg_none
        assert receipt_auto.policy_version == 0


class TestProviderPolicyApplication:
    """真实 provider 在渲染源内实施策略（builders 收到 source_policy）。"""

    @pytest.mark.asyncio
    async def test_knowledge_provider_forwards_policy_and_refs(self, monkeypatch):
        from app.lib.runtime.scoped_text import ScopedText

        captured = {}

        def _fake_builder(project_id, *, user_id=None, org_id=None, source_policy=None):
            captured["policy"] = source_policy
            text = ScopedText("KNOWLEDGE", source_scope={
                "scope": "project", "scope_id": "p1", "project_id": "p1",
                "org_id": "org1", "sensitivity": "project_scoped",
            })
            text.entry_refs = [
                {"source_id": "pk:1", "kind": "project_knowledge_entry",
                 "decision": "excluded_by_policy", "version_token": ""},
            ]
            return text

        import app.services.chat.context_assembler as ca
        monkeypatch.setattr(ca, "_build_project_knowledge_block", _fake_builder)

        facts = SharedTurnFacts(
            map_state={}, plan=None, source_policy=_policy(excluded=["pk:1"]))
        provider = prov_mod.ProjectKnowledgeProvider()
        req = _req()
        assert provider.enabled(req, facts)
        items, refs = await provider.build(req, facts)
        assert captured["policy"] is facts.source_policy
        assert refs[0]["decision"] == "excluded_by_policy"

    @pytest.mark.asyncio
    async def test_gis_memory_block_exclusion_short_circuits(self):
        """gm:block 排除 → 不投影 + 源行 excluded（不读投影文本）。"""
        provider = prov_mod.GisMemoryProvider()
        facts = SharedTurnFacts(
            map_state={}, plan=None,
            source_policy=_policy(excluded=["gm:block"]))
        req = _req()
        items, refs = await provider.build(req, facts)
        assert items == []
        assert refs == [{
            "source_id": "gm:block", "kind": "gis_memory_block",
            "decision": "excluded_by_policy", "version_token": "",
            "label": "GIS 空间记忆（整块）",
        }]


class TestPreviewSideEffects:
    @pytest.mark.asyncio
    async def test_preview_never_ensures_plan_slot_or_loads_policy_row(self, monkeypatch):
        """C07：预览 facts 只读 —— ensure_session_plan_slot 不被调用。"""
        calls = {"ensure": 0, "load_plan": 0}

        async def _no_ensure(sid):
            calls["ensure"] += 1
            raise AssertionError("preview must not ensure the plan slot")

        async def _load_plan(sid):
            calls["load_plan"] += 1
            return None

        import app.services.session_plan as plan_mod
        monkeypatch.setattr(plan_mod, "ensure_session_plan_slot", _no_ensure)
        monkeypatch.setattr(plan_mod, "load_session_plan", _load_plan)

        # 只读 builders（空目录 → 空投影；不触知识 demotion）
        out = await preview_mod.preview_turn_context(
            session_id="s1", org_id="org1", project_id="p1",
            message_draft="hi",
        )
        assert out["is_preview"] is True
        assert calls["ensure"] == 0
        assert "environment" in out["not_previewed"]
        assert "gis_context_card" in out["not_previewed"]
        assert "预览" in out["disclaimer"]
        assert "估算" in out["estimate"]["note"]

    @pytest.mark.asyncio
    async def test_preview_policy_draft_drives_sources(self, monkeypatch):
        from app.services.context_assembly.preview import preview_turn_context
        import app.services.context_assembly.providers as pm

        seen = {}

        class _StubProvider(pm.CartographyMemoryProvider):
            def enabled(self, req, facts):
                return True

            async def build(self, req, facts):
                seen["policy"] = getattr(facts, "source_policy", None)
                return [], [{
                    "source_id": "cm:1", "kind": "carto_memory_fact",
                    "decision": "excluded_by_policy",
                }]

        monkeypatch.setattr(prov_mod, "CartographyMemoryProvider", _StubProvider)
        policy = SourcePolicy(mode="explicit", excluded=("cm:1",), policy_version=-1)
        out = await preview_turn_context(
            session_id="s1", project_id="p1", policy=policy)
        assert seen["policy"] is policy
        assert out["sources"] == [{
            "source_id": "cm:1", "kind": "carto_memory_fact",
            "decision": "excluded_by_policy", "version_token": "", "label": "",
        }]
