"""G17 场景 21/22：标准 Pi 工具经真实 dispatcher 的闭环 + 与浏览器同一服务。

工具测试纪律：不直调工具函数 —— 全部经 registry.dispatch（生产入口同款）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_geojson_bytes, point_feature  # noqa: E402

from app.core.config import settings
from app.services.data_import.service import DataImportService
from app.services.session_data import session_data_manager
from app.tools.data_import_tools import register_data_import_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.heavy

SESSION = "sess-g17-tools-0001"
SESSION_OTHER = "sess-g17-tools-other"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    # 服务单例与工具共享：重置到本测试实例
    import app.services.data_import.service as svc_mod

    svc = DataImportService()
    monkeypatch.setattr(svc_mod, "_service", svc)
    return svc


@pytest.fixture()
def registry(env):
    reg = ToolRegistry()
    register_data_import_tools(reg)
    return reg


GEO = make_geojson_bytes(
    [point_feature({"city": "北京", "code": "00123"}, 116.4, 39.9)]
)


async def test_tool_closed_loop_via_real_dispatcher(env, registry):
    """场景 21：检查→计划→预览→提交→读取，全部经 registry.dispatch。"""
    state = await env.create_batch(SESSION, "user:t", [("cap.geojson", GEO)])

    # 1. 检查（列批次 + 成员详情）
    r1 = await registry.dispatch("import_batch_inspect", {"session_id": SESSION}, session_id=SESSION)
    assert r1["success"] is True and any(b["batch_id"] == state.batch_id for b in r1["batches"])
    r1b = await registry.dispatch(
        "import_batch_inspect", {"batch_id": state.batch_id, "session_id": SESSION}, session_id=SESSION,
    )
    assert r1b["success"] is True
    assert r1b["members"][0]["feature_count"] == 1

    key = r1b["members"][0]["member_key"]

    # 2. 计划
    r2 = await registry.dispatch(
        "import_plan_configure",
        {"batch_id": state.batch_id, "member_configs": {key: {}},
         "selected": [key], "session_id": SESSION}, session_id=SESSION,
    )
    assert r2["success"] is True and r2["plan_version"] == 1

    # 3. 预览（LLM 侧收敛：样本 ≤5）
    r3 = await registry.dispatch(
        "import_batch_preview",
        {"batch_id": state.batch_id, "member_key": key, "session_id": SESSION}, session_id=SESSION,
    )
    assert r3["success"] is True
    assert r3["previews"][0]["sample_rows"][0]["code"] == "00123"
    assert len(r3["previews"][0]["sample_rows"]) <= 5

    # 4. 提交（真实 dispatcher；produced ref 可解析）
    r4 = await registry.dispatch(
        "import_batch_commit", {"batch_id": state.batch_id, "session_id": SESSION}, session_id=SESSION,
    )
    assert r4["success"] is True and r4["status"] == "completed"
    ref = r4["produced_refs"][0]
    assert ref.startswith("ref:upload-")
    data = await session_data_manager.get(SESSION, ref)
    assert data is not None and data["geojson"]["features"][0]["properties"]["code"] == "00123"

    # 5. 收据（与浏览器入口同一收据事实源）
    r5 = await registry.dispatch(
        "import_batch_receipt", {"batch_id": state.batch_id, "session_id": SESSION}, session_id=SESSION,
    )
    assert r5["success"] is True
    assert r5["receipts"][key]["output_ref"] == ref
    service_receipts = await env.get_receipts(state.batch_id, SESSION)
    assert service_receipts["receipts"][key]["output_ref"] == ref

    # 幂等重试（同计划复用）
    r6 = await registry.dispatch(
        "import_batch_commit", {"batch_id": state.batch_id, "session_id": SESSION}, session_id=SESSION,
    )
    assert r6["produced_refs"][0] == ref


async def test_tool_ownership_enforced(env, registry):
    """他人会话不可读/不可提交；runtime 冲突会话 → 拒绝。"""
    state = await env.create_batch(SESSION, "user:t", [("o.geojson", GEO)])
    r = await registry.dispatch(
        "import_batch_inspect",
        {"batch_id": state.batch_id, "session_id": SESSION_OTHER},
        session_id=SESSION_OTHER,
    )
    assert r["success"] is False and r["code"] == "NOT_FOUND"

    r2 = await registry.dispatch(
        "import_plan_configure",
        {"batch_id": state.batch_id, "member_configs": {"x": {}}, "session_id": SESSION_OTHER},
        session_id=SESSION_OTHER,
    )
    assert r2["success"] is False and r2["code"] == "NOT_FOUND"

    # 不存在的批次 → typed NOT_FOUND（KeyError 包装）
    r3 = await registry.dispatch(
        "import_batch_inspect", {"batch_id": "ib-" + "0" * 32, "session_id": SESSION},
        session_id=SESSION,
    )
    assert r3["success"] is False and r3["code"] == "NOT_FOUND"


async def test_tool_runtime_context_conflict_denied(env, registry):
    """runtime 已验证会话优先于 LLM 传参（提示注入防御：参数被剥离）。"""
    from app.lib.runtime.context import bind_runtime_context

    state = await env.create_batch(SESSION, "user:t", [("c.geojson", GEO)])
    with bind_runtime_context(session_id=SESSION):
        # LLM 传了别人会话 id —— dispatcher 剥离未验证参数；工具侧
        # _resolve_session_id 以 runtime 已验证 SESSION 为准。
        r = await registry.dispatch(
            "import_batch_inspect",
            {"batch_id": state.batch_id, "session_id": SESSION_OTHER},
        )
        assert r["success"] is True
        assert r["batch_id"] == state.batch_id      # 已验证会话胜出，注入参数被剥离


async def test_cross_entry_same_batch_same_receipts(env, registry):
    """场景 21 补充：浏览器（HTTP API 形态）与 Pi 工具作用于同一批次 ——
    同一导入计划（plan_hash）与同一提交收据（batch.json 单一事实源）。"""
    from app.services.data_import.service import get_data_import_service

    state = await env.create_batch(SESSION, "user:t", [("x.geojson", GEO)])
    key = state.members[0].member_key

    # 「浏览器」入口：service（= API 路由调用的同一 facade）保存计划并预览
    await env.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])
    await env.preview_members(state.batch_id, SESSION, [key], row_limit=10)
    via_service = await get_data_import_service().get_receipts(state.batch_id, SESSION)
    plan_hash_service = (await env.get_batch(state.batch_id, SESSION)).plan_hashes[key]

    # 「Pi 工具」入口：同一批次经真实 dispatcher 提交并读收据
    r = await registry.dispatch(
        "import_batch_commit",
        {"batch_id": state.batch_id, "member_keys": [key], "session_id": SESSION},
        session_id=SESSION,
    )
    assert r["success"] is True and r["status"] == "completed"
    r2 = await registry.dispatch(
        "import_batch_receipt", {"batch_id": state.batch_id, "session_id": SESSION},
        session_id=SESSION,
    )
    receipt_tool = r2["receipts"][key]
    assert receipt_tool["plan_hash"] == plan_hash_service
    # 同一收据源：提交前 service 视角无收据，提交后两视角一致
    assert via_service["receipts"] == {}
    r3 = await get_data_import_service().get_receipts(state.batch_id, SESSION)
    assert r3["receipts"][key]["output_ref"] == receipt_tool["output_ref"]
