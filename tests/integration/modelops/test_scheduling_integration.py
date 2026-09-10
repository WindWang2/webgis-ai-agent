"""V3 §E 集成：service 级 warm pool + VRAM 账本观测（无 GPU 机器同样有效）。"""
from __future__ import annotations


def test_service_warm_pool_pin_and_status(service):
    # 钉一个种子模型（CPU；确定性 tiny provider，加载零成本）。
    state = service.pin_warm_pool("tiny-landcover-seg")
    assert state["pinned"] is True
    status = service.warm_pool_status()
    assert status["pinned"]["tiny-landcover-seg"]["pinned"] is True
    assert "vram_ledger" in status
    # 账本观测面完整（无 GPU 机器：devices 为空是合法的诚实结果）。
    assert isinstance(status["vram_ledger"]["devices"], list)
    assert "used" in status["vram_ledger"]


def test_service_run_with_ledger_records_reservation(service, synthetic_raster):
    from app.services.modelops.engine import InferenceRequest

    service.pin_warm_pool("tiny-landcover-seg")
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-sched"},
        )
    )
    assert result.status in ("completed", "reused")
    manifest = result.manifest
    assert manifest["device_plan"]["device"] == "cpu"
    assert manifest["device_plan"]["device_index"] == 0
    # 推理完成后预订已归还（无泄漏）。
    assert service.warm_pool_status()["vram_ledger"]["used"].get("cpu:0", 0) == 0
