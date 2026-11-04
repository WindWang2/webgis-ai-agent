"""ModelOps receipt contracts exercise the real service and registered callable.

The descriptor describes the callable's returned result, as certification
probes do. Exceptions still propagate to the caller; the registry's separate
error envelope is not a successful receipt from these tools.
"""
from __future__ import annotations

import json

import pytest

from app.extensions_platform.capability_certification import _classify_result_size
from app.lib.modelops.errors import DescriptorError, ModelOpsError
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.service import ModelOpsService
from app.tools.modelops_tools import register_modelops_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def service(tmp_path, monkeypatch):
    import app.services.modelops.service as service_module

    instance = ModelOpsService(ModelOpsSettings(registry_dir=tmp_path / "modelops"))
    monkeypatch.setattr(service_module, "get_modelops_service", lambda: instance)
    return instance


@pytest.fixture(scope="module")
def registry():
    instance = ToolRegistry()
    register_modelops_tools(instance)
    return instance


def _assert_small_result(registry, name, result):
    descriptor = registry.descriptor(name)
    assert descriptor.result_size_policy == "inline_small"
    passed, detail = _classify_result_size(descriptor.result_size_policy, result)
    assert passed, detail
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) < 16 * 1024


@pytest.mark.parametrize(
    "event_type,payload",
    [
        ("training_metrics", {"metrics": {"description": "界" * 60_000}}),
        ("evaluation", {"evaluation_data_object_id": "界" * 60_000}),
        ("promotion", {"stage": "界" * 60_000}),
        ("retirement", {"unused": "界" * 60_000}),
    ],
)
async def test_record_metrics_all_event_receipts_preserve_one_write(
    registry, service, event_type, payload,
):
    """Large legitimate metadata is stored, never echoed or recorded twice."""
    model_id = "model-" + "m" * 100_000
    result = await registry._tools["modelops_record_metrics"](
        model_id=model_id,
        model_version="1.0.0",
        event_type=event_type,
        payload=payload,
        actor="操作者" * 100_000,
    )
    events = service._lineage.load(model_id, "1.0.0")
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == event_type
    assert event["model_id"] == model_id
    assert result == {"recorded": True, "seq": event["seq"], "ts": event["ts"]}
    assert set(result) == {"recorded", "seq", "ts"}
    assert registry.descriptor("modelops_record_metrics").output_semantic_type == "object"
    _assert_small_result(registry, "modelops_record_metrics", result)


async def test_record_metrics_invalid_event_propagates_without_writing(registry, service):
    """The raw callable raises; it does not return an unbounded error dict."""
    invalid_event = "界" * 100_000
    with pytest.raises(ModelOpsError) as error:
        await registry._tools["modelops_record_metrics"](
            model_id="invalid-event", model_version="1", event_type=invalid_event,
        )
    assert invalid_event in str(error.value)
    assert service._lineage.load("invalid-event", "1") == []


@pytest.mark.parametrize(
    "payload",
    [{"metrics": {"description": "界" * 100_000}}, {"metrics": {"api_key": "x"}}],
)
async def test_record_metrics_storage_validation_still_raises(registry, service, payload):
    with pytest.raises(DescriptorError):
        await registry._tools["modelops_record_metrics"](
            model_id="invalid-payload", model_version="1",
            event_type="training_metrics", payload=payload,
        )
    assert service._lineage.load("invalid-payload", "1") == []
