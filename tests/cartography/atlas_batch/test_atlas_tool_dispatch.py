"""export_atlas 工具真实分发测试：registry 注册面 + dispatch → durable job。

真实链：``ToolRegistry.dispatch``（生产入口，不经 mock）→ 工具体 →
``submit_durable_job``（幂等）→ eager Celery → executor 真实渲染落盘。
"""
import pytest

from .fixtures import district_document

pytestmark = pytest.mark.cartography

weasyprint = pytest.importorskip("weasyprint")


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    return r


@pytest.fixture()
def tool_env(atlas_exports, atlas_job_db, atlas_spec_store, monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    atlas_spec_store["set_document"](district_document(districts=2))
    return atlas_spec_store


async def test_export_atlas_registered_and_dispatches(registry, tool_env):
    schemas = {s.get("function", {}).get("name") for s in registry.get_schemas()}
    assert "export_atlas" in schemas

    result = await registry.dispatch(
        "export_atlas",
        {
            "session_id": "sess-atlas-test",
            "driver": "category",
            "layer_id": "districts",
            "category_property": "district",
            "include_cover": True,
            "atlas_title": "工具图册",
            "classification_scope": "shared",
        },
        session_id="sess-atlas-test",
    )
    # submit 面返回 job 句柄（dispatch 契约的 dict 直返）
    assert result.get("job_id"), result
    assert result.get("status") in ("analysis_task_started",
                                    "analysis_task_reused")
    assert "任务" in result.get("message", "") or "poll" in result.get(
        "poll_hint", "")


async def test_export_atlas_idempotent_double_dispatch(registry, tool_env):
    r1 = await registry.dispatch("export_atlas", {
        "session_id": "sess-atlas-test", "driver": "category",
        "layer_id": "districts", "category_property": "district",
        "atlas_title": "幂等图册"}, session_id="sess-atlas-test")
    r2 = await registry.dispatch("export_atlas", {
        "session_id": "sess-atlas-test", "driver": "category",
        "layer_id": "districts", "category_property": "district",
        "atlas_title": "幂等图册"}, session_id="sess-atlas-test")
    assert r1["job_id"] == r2["job_id"]
    assert r2.get("idempotent_reuse") is True


async def test_export_atlas_typed_error_dict(registry, tool_env):
    result = await registry.dispatch("export_atlas", {
        "session_id": "sess-atlas-test", "driver": "category",
        "layer_id": "districts", "category_property": "district",
        "classification_scope": "local"}, session_id="sess-atlas-test")
    assert result.get("code") == "atlas_local_requires_frames"
    assert result.get("error")


def test_job_params_shape_matches_executor_reader(tool_env):
    """契约锁定：submit 落库的 params 键 = executor._load_job_spec 读取键。"""
    from app.services.atlas_batch.submit import build_atlas_job_spec

    spec = build_atlas_job_spec(**{
        "session_id": "s", "atlas_policy": {
            "driver": "category", "layer_id": "l",
            "category_property": "district", "features_per_page": 10,
            "page_budget": 5, "include_cover": True, "atlas_title": "t"},
        "classification_scope": "shared", "target_dpi": 200,
        "title": "x", "simulated_data": True})
    params = spec.job_params()
    for key in ("spec_version", "session_id", "atlas", "classification_scope",
                "target_dpi", "title", "simulated_data"):
        assert key in params, key
