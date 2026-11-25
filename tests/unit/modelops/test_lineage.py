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


def test_seq_survives_store_reopen(tmp_path):
    """重启恢复：新 store 实例 append 不重置 seq（部署状态推导不倒转）。"""
    store = ModelLineageStore(tmp_path / "lineage")
    store.append("m", "1.0.0", event_type="promotion", payload={"stage": "production"})
    store.append("m", "1.0.0", event_type="retirement")
    reopened = ModelLineageStore(tmp_path / "lineage")
    event = reopened.append("m", "1.0.0", event_type="provenance_note", payload={})
    assert event["seq"] == 3  # 不与历史 seq 碰撞
    assert reopened.deployment_state("m", "1.0.0") == "retired"


def _write_raw_lineage(tmp_path, lines):
    root = tmp_path / "lineage"
    root.mkdir(parents=True, exist_ok=True)
    (root / "m@1.0.0.jsonl").write_text(lines, encoding="utf-8")
    return root


def test_load_skips_valid_non_object_json_lines(tmp_path):
    """合法但非对象的 JSON 行（[]/42/"s"/null）不得击穿 load() 的坏行容忍。"""
    root = _write_raw_lineage(
        tmp_path, '[]\n42\n"s"\nnull\n{"seq": 7, "event_type": "promotion"}\n'
    )
    store = ModelLineageStore(root)
    events = store.load("m", "1.0.0")
    assert [e["seq"] for e in events] == [7]


def test_append_seq_recovery_skips_valid_non_object_json_lines(tmp_path):
    """seq 恢复扫描同样不得被非对象行打断（否则该版本 append 永久失败）。"""
    root = _write_raw_lineage(tmp_path, '[]\n42\n"s"\nnull\n')
    store = ModelLineageStore(root)
    event = store.append("m", "1.0.0", event_type="provenance_note", payload={})
    assert event["seq"] == 1
