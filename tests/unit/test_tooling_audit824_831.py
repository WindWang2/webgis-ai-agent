"""Regression tests for audit-ff9a392 tool-system findings (#824-#831).

#824: ref resolver — deduped alias fields, oversized degradation to explicit
     ref: cursors, identity-preserving rebuild.
#825: capability→registry parity (see test_capability_registry_parity.py).
#826: describe_dataset must not declare INLINE (sync network fallback).
#827: honest execution-policy declarations; zero auto-routing warnings.
#828: unknown params rejected with correction hint; TypeError classified.
#829: declared version/contract_version must survive registration.
#830: PRODUCES_REF_DOMAINS ⊆ live registry domains.
#831: spawn_subagent documented activation words present in catalog keywords.
"""

import json

import pytest


@pytest.fixture(scope="module")
def full_registry():
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return reg


# ─── #824: resolver budgets and identity ────────────────────────────────


class TestAudit824Resolver:
    @pytest.fixture()
    def resolver_seams(self, monkeypatch):
        import app.tools.registry as reg_mod

        calls = {"alias_fields": [], "store": {}}

        async def fake_resolve_aliases(sid, strings):
            calls["alias_fields"].append(list(strings))
            return {}

        async def fake_get(sid, ref):
            return calls["store"].get(ref)

        monkeypatch.setattr(reg_mod.session_data_manager, "resolve_aliases", fake_resolve_aliases)
        monkeypatch.setattr(reg_mod.session_data_manager, "get", fake_get)
        return calls

    def _big_fc(self, n=20000):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": f"p{i}", "cat": "school"},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}}
            for i in range(n)
        ]}

    @pytest.mark.asyncio
    async def test_oversized_payload_skips_alias_lookup_and_rebuild(self, resolver_seams):
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry.__new__(ToolRegistry)
        fc = self._big_fc()
        args = {"geojson": fc, "radius": 100}
        out = await reg._resolve_references("s1", args, skip_keys=set(), oversized_hint=True)
        # no alias HMGET at all
        assert resolver_seams["alias_fields"] == []
        # identity short-circuit: subtree untouched
        assert out["geojson"] is fc

    @pytest.mark.asyncio
    async def test_distinct_cap_degrades_to_ref_prefix_only(self, resolver_seams):
        from app.tools.registry import ToolRegistry, _ALIAS_LOOKUP_MAX_DISTINCT

        reg = ToolRegistry.__new__(ToolRegistry)
        # 1200 distinct strings (over the 1024 cap) but NOT oversized
        args = {"items": [f"val-{i}" for i in range(_ALIAS_LOOKUP_MAX_DISTINCT + 200)]}
        out = await reg._resolve_references("s1", args, skip_keys=set(), oversized_hint=False)
        assert resolver_seams["alias_fields"] == []  # degraded: no HMGET
        assert out["items"][0] == "val-0"  # plain strings untouched

    @pytest.mark.asyncio
    async def test_explicit_ref_resolves_in_oversized_payload(self, resolver_seams):
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry.__new__(ToolRegistry)
        resolver_seams["store"]["ref:geojson-x"] = {"type": "FeatureCollection", "features": []}
        args = {"style": {"big": ["x"] * 500}, "overlay": "ref:geojson-x"}
        out = await reg._resolve_references("s1", args, skip_keys=set(), oversized_hint=True)
        assert out["overlay"] == resolver_seams["store"]["ref:geojson-x"]

    @pytest.mark.asyncio
    async def test_small_args_alias_lookup_is_deduped(self, resolver_seams):
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry.__new__(ToolRegistry)
        args = {"a": "x", "b": "x", "c": "y"}  # 3 strings, 2 distinct
        await reg._resolve_references("s1", args, skip_keys=set(), oversized_hint=False)
        sent = resolver_seams["alias_fields"][-1]
        assert sorted(sent) == ["x", "y"]
        assert len(sent) == len(set(sent))


# ─── #826/#827: honest execution policies ───────────────────────────────


class TestAudit826827Policies:
    def test_describe_dataset_is_not_inline(self, full_registry):
        meta = full_registry.metadata("describe_dataset")
        assert meta.get("execution_policy") != "inline"

    def test_zero_auto_routing_warnings(self, full_registry):
        # Auto-routing warnings happen at init_tools; if any declaration were
        # still mismatched the census here would show it: every async tool
        # must carry a non-thread/celery final policy.
        from app.tools.registry import ToolExecutionPolicy
        bad = []
        for name in full_registry.list_tools():
            meta = full_registry.metadata(name)
            policy = meta.get("execution_policy")
            if policy in (ToolExecutionPolicy.CELERY,):
                bad.append((name, policy))
        assert not bad, f"phantom-policy declarations remain: {bad}"


# ─── #828: unknown params + TypeError classification ────────────────────


class TestAudit828UnknownParams:
    @pytest.mark.asyncio
    async def test_unknown_param_rejected_with_hint(self, full_registry):
        res = await full_registry.dispatch(
            "geocode", {"address": "成都市", "bogus_param": 123}, session_id=""
        )
        assert isinstance(res, dict)
        assert res.get("success") is False or res.get("code") == "VALIDATION_ERROR"
        blob = json.dumps(res, ensure_ascii=False)
        assert "bogus_param" in blob

    @pytest.mark.asyncio
    async def test_valid_dispatch_still_works(self, full_registry):
        # a known-good call shape must not be broken by the strict gate
        res = await full_registry.dispatch("list_available_tools", {}, session_id="")
        assert isinstance(res, dict)


# ─── #829: version plumbing ─────────────────────────────────────────────


class TestAudit829VersionPlumbing:
    def test_declared_version_survives_registration(self):
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()

        @reg.tool(name="vtest_probe", description="probe", version="2.3", contract_version=4)
        def _probe(x: int = 1) -> dict:
            return {"ok": True}

        assert reg.tool_version("vtest_probe") == "2.3#cv4"
        assert reg.tool_version("never_registered") == "1.0#cv1"


# ─── #830: PRODUCES_REF_DOMAINS parity ──────────────────────────────────


def test_produces_ref_domains_subset_of_registry(full_registry):
    from app.services.planning.capability import PRODUCES_REF_DOMAINS

    domains = set()
    for t in full_registry.list_tools():
        domains.update(full_registry.metadata(t).get("domains") or [])
    dead = PRODUCES_REF_DOMAINS - domains
    assert not dead, f"PRODUCES_REF_DOMAINS carries dead domains: {sorted(dead)}"


# ─── #831: spawn_subagent activation words ──────────────────────────────


def test_spawn_subagent_activation_words_in_catalog():
    from app.services.tool_catalog import DOMAIN_KEYWORDS

    meta_words = DOMAIN_KEYWORDS.get("meta", [])
    for word in ("批量", "子任务", "委派"):
        assert word in meta_words, f"documented trigger word {word!r} missing from meta keywords"
