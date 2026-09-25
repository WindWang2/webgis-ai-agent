"""环境指纹 v2 契约（ADR-0214 D2，WP5）：封闭白名单 + drift 分类 + digest 记忆化。"""
from __future__ import annotations

import pytest

from app.lib.harness.replay.drift import (
    ENV_SCHEMA_VERSION,
    capability_registry_digest,
    collect_env_fingerprint,
    env_drift,
)

pytestmark = pytest.mark.cartography


# ── collect_env_fingerprint ──────────────────────────────────────────────────

def test_env_fingerprint_shape_and_whitelist():
    env = collect_env_fingerprint()
    assert env["env_schema_version"] == ENV_SCHEMA_VERSION
    # 封闭段：flag 键域是封闭白名单（任意 env 变量不得混入）。
    flags = env["runtime_flags"]
    assert set(flags) == {
        "GIS_CAPABILITY_DISPATCH_BIND", "GOVERNOR_TOOL_SURFACE",
        "GIS_ANALYSIS_REUSE", "SPATIAL_GUARDRAILS",
        "HARNESS_REPLAY_RECORD", "CARTO_VISUAL_JUDGE",
    }
    assert all(isinstance(v, bool) for v in flags.values())
    # 默认态：闸全开（HARNESS_REPLAY_RECORD / CARTO_VISUAL_JUDGE 默认关）。
    assert flags["GIS_CAPABILITY_DISPATCH_BIND"] is True
    assert flags["HARNESS_REPLAY_RECORD"] is False
    assert env["registry_digest"], "进程图存在时 digest 非空"
    assert set(env["policy_versions"]) == {
        "capability_resolution", "capability_dispatch_bind", "plan_aggregation",
    }


def test_env_fingerprint_deterministic():
    assert collect_env_fingerprint() == collect_env_fingerprint()


def test_env_fingerprint_reflects_flag_overrides(monkeypatch):
    monkeypatch.setenv("GIS_ANALYSIS_REUSE", "0")
    monkeypatch.setenv("HARNESS_REPLAY_RECORD", "1")
    env = collect_env_fingerprint()
    assert env["runtime_flags"]["GIS_ANALYSIS_REUSE"] is False
    assert env["runtime_flags"]["HARNESS_REPLAY_RECORD"] is True


def test_env_fingerprint_never_carries_arbitrary_env(monkeypatch):
    monkeypatch.setenv("SOME_SECRET_TOKEN", "sk-should-never-appear")
    import json

    assert "sk-should-never-appear" not in json.dumps(collect_env_fingerprint())


# ── env_drift 分类 ───────────────────────────────────────────────────────────

def test_env_drift_identical_is_empty():
    env = collect_env_fingerprint()
    assert env_drift(env, dict(env)) == []


def test_env_drift_policy_change_is_behavioral():
    base = collect_env_fingerprint()
    current = dict(base)
    current["policy_versions"] = {
        **base["policy_versions"], "capability_resolution": "capability_resolution.v2",
    }
    drifts = env_drift(base, current)
    assert len(drifts) == 1
    assert drifts[0]["kind"] == "policy_versions"
    assert drifts[0]["behavioral"] is True
    assert drifts[0]["changed_keys"] == ["capability_resolution"]


def test_env_drift_flag_flip_is_behavioral():
    base = collect_env_fingerprint()
    current = {**base, "runtime_flags":
               {**base["runtime_flags"], "SPATIAL_GUARDRAILS": False}}
    drifts = env_drift(base, current)
    assert [d["kind"] for d in drifts] == ["runtime_flags"]
    assert drifts[0]["changed_keys"] == ["SPATIAL_GUARDRAILS"]


def test_env_drift_platform_is_not_behavioral():
    base = collect_env_fingerprint()
    current = {**base, "python_version": "9.9.9"}
    drifts = env_drift(base, current)
    assert drifts[0]["kind"] == "python_version"
    assert drifts[0]["behavioral"] is False


def test_env_drift_multiple_sections_classified():
    base = collect_env_fingerprint()
    current = {
        **base,
        "registry_digest": "different",
        "sources": {**base["sources"], "capability_registry": "beef"},
        "budgets_digest": "different",
        "platform": "other",
    }
    kinds = {d["kind"] for d in env_drift(base, current)}
    assert kinds == {"registry_digest", "sources", "budgets_digest", "platform"}


def test_env_drift_honest_absence():
    # schema 版本不一致（v1 录制件）→ 不制造假漂移。
    v1 = {"registry_digest": "aaa"}
    v2 = collect_env_fingerprint()
    assert env_drift(v1, v2) == []
    assert env_drift(None, v2) == []
    assert env_drift(v2, "x") == []


# ── digest 记忆化 ────────────────────────────────────────────────────────────

def test_registry_digest_memoized_per_graph(monkeypatch):
    from app.lib.harness.replay import drift as drift_module
    from app.services.gis_harness.capability_graph import (
        get_capability_graph,
        reset_capability_graph,
    )

    calls = {"n": 0}
    real_compute = drift_module._compute_registry_digest

    def counting(graph):
        calls["n"] += 1
        return real_compute(graph)

    monkeypatch.setattr(drift_module, "_compute_registry_digest", counting)
    try:
        reset_capability_graph()
        first = capability_registry_digest()
        second = capability_registry_digest()
        assert first == second and first
        assert calls["n"] == 1, "同实例（指纹未变）必须命中记忆化"
        # 图重建（新实例）→ 记忆化失效，重算一次。
        reset_capability_graph()
        capability_registry_digest()
        assert calls["n"] == 2
    finally:
        reset_capability_graph()


def test_explicit_graph_bypasses_memo():
    class _Stub:
        def nodes_by_kind(self, kind):
            return []

    assert capability_registry_digest(graph=_Stub())
