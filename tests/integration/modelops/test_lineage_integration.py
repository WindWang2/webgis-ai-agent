"""V3 §F 集成：service 级 lineage 记录 → inspect/history。"""
from __future__ import annotations


def test_service_lineage_flow(service):
    service.record_training_metrics("tiny-landcover-seg", "1.0.0",
                                    metrics={"miou": 0.70}, actor="test")
    service.record_evaluation_ref("tiny-landcover-seg", "1.0.0",
                                  metrics_summary={"miou": 0.68},
                                  dataset_refs=["dataset-ref-1"], actor="test")
    service.set_deployment_state("tiny-landcover-seg", "1.0.0", promote=True,
                                 stage="staging", actor="test")
    history = service.model_history("tiny-landcover-seg")
    entry = history["versions"]["1.0.0"]
    assert entry["deployment_state"] == "staging"
    assert entry["events"][0]["payload"]["metrics"] == {"miou": 0.70}
    # inspect_model 带 lineage 摘要。
    inspect = service.inspect_model("tiny-landcover-seg")
    assert inspect["lineage"]["deployment_state"] == "staging"
    assert inspect["lineage"]["latest_metrics"]["metrics_summary"] == {"miou": 0.68}
