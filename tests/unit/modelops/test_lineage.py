"""V3 §F：lineage 存储 + service 记录面 的契约测试。"""
from __future__ import annotations

import pytest

from app.lib.modelops.errors import DescriptorError
from app.services.modelops.lineage import ModelLineageStore


def test_append_and_history_roundtrip(tmp_path):
    store = ModelLineageStore(tmp_path / "lineage")
    e1 = store.append("m", "1.0.0", event_type="training_metrics",
                      payload={"metrics": {"miou": 0.71}}, actor="trainer")
    e2 = store.append("m", "1.0.0", event_type="evaluation",
                      payload={"metrics_summary": {"miou": 0.69}})
    assert e1["seq"] == 1 and e2["seq"] == 2
    events = store.load("m", "1.0.0")
    assert [e["event_type"] for e in events] == ["training_metrics", "evaluation"]


def test_unknown_event_type_and_secret_payload_typed(tmp_path):
    store = ModelLineageStore(tmp_path / "lineage")
    with pytest.raises(DescriptorError):
        store.append("m", "1.0.0", event_type="self_promotion")
    with pytest.raises(DescriptorError):
        store.append("m", "1.0.0", event_type="training_metrics",
                     payload={"api_key": "sk-..."})


def test_oversized_payload_typed(tmp_path):
    store = ModelLineageStore(tmp_path / "lineage")
    with pytest.raises(DescriptorError):
        store.append("m", "1.0.0", event_type="training_metrics",
                     payload={"blob": "x" * (300 * 1024)})


def test_latest_metrics_and_deployment_state(tmp_path):
    store = ModelLineageStore(tmp_path / "lineage")
    store.append("m", "1.0.0", event_type="training_metrics",
                 payload={"metrics": {"miou": 0.5}})
    store.append("m", "1.0.0", event_type="evaluation",
                 payload={"metrics_summary": {"miou": 0.48}})
    assert store.latest_metrics("m", "1.0.0")["metrics_summary"] == {"miou": 0.48}
    assert store.deployment_state("m", "1.0.0") == "registered"
    store.append("m", "1.0.0", event_type="promotion", payload={"stage": "staging"})
    assert store.deployment_state("m", "1.0.0") == "staging"
    store.append("m", "1.0.0", event_type="retirement")
    assert store.deployment_state("m", "1.0.0") == "retired"


def test_history_across_versions(tmp_path):
    store = ModelLineageStore(tmp_path / "lineage")
    store.append("m", "1.0.0", event_type="training_metrics", payload={"metrics": {}})
    store.append("m", "1.1.0", event_type="promotion", payload={"stage": "production"})
    history = store.history("m")
    assert {e["model_version"] for e in history} == {"1.0.0", "1.1.0"}
    single = store.history("m", model_version="1.1.0")
    assert len(single) == 1
