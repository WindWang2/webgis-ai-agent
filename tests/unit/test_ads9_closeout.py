"""ads-v1 DS9 closeout tests (ADR-0179).

Covers: the D1–D4 cross-version compatibility matrix (additive-only
evolution for every contract), bilingual user-message guarantees, the
hardcoded-cleanup gate (gov PLATFORMS zero-revival), the five telemetry
categories mapped onto the D4 fact, and the security review greps
(credential hygiene + SSRF seams) as executable assertions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.data_fabric.contracts import (
    AcquisitionFact,
    AcquisitionPlan,
    D1DatasetDescriptor,
    FallbackDecision,
)
from app.services.data_fabric.user_messages import available_keys, t

REPO = Path(__file__).resolve().parents[2]


# ── D1–D4 cross-version compatibility matrix ─────────────────────────────────


CONTRACT_CASES = [
    # (model, minimal valid payload factory, v1.1 extra field)
    (
        D1DatasetDescriptor,
        lambda: {"id": "ds", "source_type": "ogc_api"},
        "future_supply_field",
    ),
    (
        AcquisitionPlan,
        lambda: {"plan_id": "plan-x", "dataset_key": "src/ds"},
        "future_plan_field",
    ),
    (
        FallbackDecision,
        lambda: {"trigger": "timeout", "from_source": "a", "to_source": "b", "reason": "r"},
        "future_decision_field",
    ),
    (
        AcquisitionFact,
        lambda: {"request_id": "r", "dataset_key": "d"},
        "future_fact_field",
    ),
]


@pytest.mark.parametrize("model,payload_factory,extra_key", CONTRACT_CASES)
def test_contract_upgrade_matrix(model, payload_factory, extra_key):
    """v1 → v1.1: every contract accepts additive fields and preserves them
    (the cross-version compatibility guarantee, one case per contract)."""
    v1_payload = payload_factory()
    v1_instance = model(**v1_payload)
    assert model(**json.loads(v1_instance.model_dump_json())) == v1_instance

    v1_1_payload = {**v1_payload, extra_key: {"added": "in-v1.1"}}
    upgraded = model(**v1_1_payload)
    assert upgraded.model_dump()[extra_key] == {"added": "in-v1.1"}
    # serialisation keeps the field for true cross-version payloads
    assert json.loads(upgraded.model_dump_json())[extra_key] == {"added": "in-v1.1"}


def test_all_four_contracts_present_in_matrix():
    models = [case[0].__name__ for case in CONTRACT_CASES]
    assert set(models) == {"D1DatasetDescriptor", "AcquisitionPlan", "FallbackDecision", "AcquisitionFact"}


# ── bilingual user messages ───────────────────────────────────────────────────


def test_user_messages_both_locales_complete():
    for key in available_keys():
        zh = t(key, "zh")
        en = t(key, "en")
        assert zh and en and zh != key and en != key
        assert zh != en


def test_fallback_source_message_is_actionable():
    msg = t("data_from_fallback_source", "zh", source="overpass_api")
    assert "备用源" in msg and "overpass_api" in msg
    msg_en = t("result_not_comparable", "en")
    assert "not comparable" in msg_en.lower()


def test_unknown_message_key_falls_back_to_key():
    assert t("no_such_key", "en") == "no_such_key"


# ── DS9 hardcode cleanup gate ─────────────────────────────────────────────────


def test_gov_platforms_hardcode_is_gone():
    src = (REPO / "app" / "adapters" / "gov" / "gov_data_adapter.py").read_text(encoding="utf-8")
    assert "data.beijing.gov.cn" not in src, "hardcoded platform URLs must not return"
    # the (empty) deprecated constant may remain as a compat surface — no URLs
    assert 'PLATFORMS = {}' in src or "PLATFORMS" not in src


def test_local_first_hardcoded_chain_retained_by_rule():
    """The hardcoded local chain stays until production equivalence is
    confirmed (task rule: 未确认前保留) — the seam + equivalence test exist."""
    src = (REPO / "app" / "services" / "local_first.py").read_text(encoding="utf-8")
    assert "registry_local_chain" in src
    assert '["local_poi", "local_osm"]' in src  # hardcoded default retained


# ── five telemetry categories mapped onto the D4 fact ────────────────────────


def test_five_telemetry_categories_covered_by_fact():
    fact = AcquisitionFact(
        request_id="r",
        dataset_key="src/ds",
        source_id="src",                     # ① 源选择
        version="rev-1",                     # ③ 版本
        rows=1, bytes=1, latency_ms=1.0,
        retries=1,
        degraded=True,                       # ② 降级（outcome + fallback）
        outcome="degraded",
        fallback=FallbackDecision(trigger="timeout", from_source="a", to_source="src", reason="x"),
        drift="added_column",                # ④ 漂移
        wave="M5",
    )
    payload = fact.model_dump()
    assert payload["source_id"] == "src"                      # ①
    assert payload["fallback"]["trigger"] == "timeout"        # ②
    assert payload["degraded"] is True and payload["outcome"] == "degraded"  # ②
    assert payload["version"] == "rev-1"                      # ③
    assert payload["drift"] == "added_column"                 # ④
    assert all(payload[k] is not None for k in ("rows", "bytes", "latency_ms"))  # ⑤ 成本
    cost_budget = t("cost_budget_exceeded", "zh", metric="rows", value=1, budget=1)
    assert "超预算" in cost_budget                             # ⑤ 的用户面


# ── security review as executable assertions ─────────────────────────────────


def test_security_no_plaintext_credentials_in_registry():
    offenders = []
    for yaml_file in (REPO / "config" / "sources").glob("*.yaml"):
        text = yaml_file.read_text(encoding="utf-8")
        for m in re.finditer(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{8,})", text):
            value = m.group(2)
            if not value.startswith("${"):
                offenders.append(f"{yaml_file.name}: {m.group(0)[:40]}")
    assert offenders == [], f"plaintext credential suspects: {offenders}"


def test_security_stats_adapter_uses_safe_session():
    src = (REPO / "app" / "services" / "data_fabric" / "adapters" / "stats_api_adapter.py").read_text(encoding="utf-8")
    assert "make_safe_session" in src, "stats_api must use the SSRF-safe session seam"
    assert "requests.get(" not in src.replace("session.get(", ""), "raw requests dial is forbidden"


def test_security_local_paths_funnel_through_guard():
    for adapter in ("local_file_adapter.py", "geopackage_adapter.py", "cog_adapter.py"):
        src = (REPO / "app" / "services" / "data_fabric" / "adapters" / adapter).read_text(encoding="utf-8")
        assert "resolve_safe_local_path" in src, f"{adapter} bypasses the local-path guard"
