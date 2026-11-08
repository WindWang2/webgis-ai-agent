"""G10 Pi 工具链集成测试（PI 层：真实 ToolRegistry + 真实 dispatch + 真实领域计算）。

说明：本测试走 ``PiToolRequest → dispatch_tool → ToolDispatchService →
ToolRegistry → 刷新工具 → ProjectDataRefreshService`` 的真实调用链，
工具返回值来自真实差异/影响/合并算法，**不是 stub 工具返回值**。
LLM 边界未接 provider（无需付费模型）；完整「真实 Pi RPC + stub LLM」
车道见 ``tests/integration/test_pi_stub_llm_journeys.py``（opt-in，另列未验证）。
"""
from __future__ import annotations

import copy

import pytest

from app.agent_pi_bridge import (
    PiToolRequest,
    dispatch_tool,
    set_tool_registry,
)
from app.services.project_runtime.refresh.execution import InMemoryMapSpecPort
from app.services.project_runtime.refresh.service import ProjectDataRefreshService
from app.services.project_runtime.refresh.sources import StaticPayloadProvider
from app.services.project_runtime.refresh.store import (
    RefreshStore,
    reset_refresh_store,
)
from app.tools.project_data_refresh_tools import (
    RefreshToolArgs,
    register_project_data_refresh_tools,
    set_refresh_service_factory,
)
from app.tools.registry import ToolRegistry

PID = "proj-g10-pi"
SID = "sess-g10-pi"
OWNER = "g10-pi-owner"
DSID = "ds-g10-pi"
REF = "ref-g10-pi"


def _feature(fid: str, n: int) -> dict:
    return {"type": "Feature", "id": fid,
            "properties": {"name": fid, "students": n},
            "geometry": "POINT(1 1)"}


def _payload(version: int, feats: list) -> dict:
    return {"descriptor": {
        "content_hash": f"h-v{version}", "content_revision": version,
        "crs": "EPSG:4326", "bbox": [103.9, 30.5, 104.2, 30.8],
        "field_schema": {"name": {"type": "str"},
                         "students": {"type": "int", "unit": "person"}},
        "field_schema_complete": True, "estimated_bytes": 1024,
        "feature_count": len(feats)},
        "payload": {"type": "FeatureCollection", "features": feats}}


def _mapspec() -> dict:
    return {"title": "学校分布", "legend": {"position": "bottom-left"},
            "layers": [{"id": "L1", "source": "ref://schools", "type": "circle",
                        "paint": {"circle-color": "#3388ff"}, "visible": True}],
            "components": [],
            "workbench": {"lockedLayerIds": [], "lockedComponentIds": []}}


F1 = [_feature("s1", 100), _feature("s2", 200), _feature("s3", 300)]


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.database import Base, Engine, SessionLocal

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    reset_refresh_store()
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as s:
        from app.models.db_model import Conversation, User
        from app.models.project import Project, ProjectDataset

        s.merge(User(id=OWNER, username=OWNER, email=f"{OWNER}@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.merge(Project(id=PID, name="pi", owner_id=OWNER, status="active"))
        s.merge(ProjectDataset(id=DSID, project_id=PID, name="schools",
                               source_type="upload", source_ref=REF,
                               version_fingerprint="fp-v1", crs="EPSG:4326"))
        s.merge(Conversation(id=SID, user_id=OWNER, title="seed"))
        s.commit()

    svc = ProjectDataRefreshService(
        store=RefreshStore(),
        port=InMemoryMapSpecPort(copy.deepcopy(_mapspec()), revision=3),
        payload_provider=StaticPayloadProvider({REF: _payload(1, F1)}),
    )
    _setup.svc = svc
    set_refresh_service_factory(lambda: svc)
    registry = ToolRegistry()
    register_project_data_refresh_tools(registry)
    set_tool_registry(registry)
    _setup.registry = registry
    yield
    set_refresh_service_factory(None)
    set_tool_registry(ToolRegistry())
    reset_refresh_store()


def _names() -> set[str]:
    # registry.list_tools() 返回工具名字符串列表（既有契约）
    return {str(t) for t in _setup.registry.list_tools()}


async def _call(tool_name: str, args: dict) -> dict:
    """真实 ToolDispatchService 调度（每次新的 executed_tools 集合，避免
    既有「同参重复拦截」纪律吞掉第二次检查；不绕过任何调度/资格阶段）。
    ``raw_result`` 是工具真实返回的**原始类型化 dict**，不是 LLM 文本投影。
    """
    from app.services.tool_dispatch_service import ToolDispatchService

    tc = {"id": "tc-g10-" + tool_name[-12:],
          "function": {"name": tool_name, "arguments": args}}
    dispatcher = ToolDispatchService(registry=_setup.registry)
    res = await dispatcher.dispatch(tc, SID, set(), None)
    raw = res.raw_result
    if isinstance(raw, dict):
        return raw
    return {"raw": str(raw)[:400], "dispatch_status": str(res.status)}


def test_tools_are_registered_on_real_registry():
    names = _names()
    expected = {
        "webgis_check_project_source_updates",
        "webgis_explain_stale_map_product",
        "webgis_preview_project_data_refresh",
        "webgis_execute_project_data_refresh",
        "webgis_adopt_project_data_refresh",
        "webgis_discard_project_data_refresh",
        "webgis_rollback_project_data_refresh",
        "webgis_project_refresh_status",
    }
    assert expected <= names


def test_model_cannot_inject_owner_or_approval_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RefreshToolArgs(project_id=PID, owner_id="attacker", approved=True)


@pytest.mark.asyncio
async def test_pi_bridge_entry_reaches_real_refresh_tool():
    """PiTools HTTP 回调适配器入口可达：真实 PiToolRequest → dispatch_tool
    → ToolDispatchService → 刷新工具（不是 mock 的执行器）。"""
    req = PiToolRequest(
        toolCallId="tc-g10-bridge", name="webgis_execute",
        arguments={"toolName": "webgis_check_project_source_updates",
                   "arguments": {"project_id": PID}},
        sessionId=SID,
    )
    resp = await dispatch_tool(req)
    assert not resp.isError
    text = resp.content[0]["text"] if resp.content else ""
    assert text
    # 类型化回执经 LLM 投影后仍带关键字段
    assert "sources_total" in text or "baseline" in text or "g10" in text


@pytest.mark.asyncio
async def test_o10_o11_pi_chain_check_preview_execute_adopt_status():
    svc: ProjectDataRefreshService = _setup.svc

    # 1) 检查更新（首次建立基线）
    r = await _call("webgis_check_project_source_updates", {"project_id": PID})
    assert r.get("sources_total") == 1, r
    assert r.get("tool_execution") == "ok"
    assert r.get("map_observation") == "unverified"
    assert r.get("version_adoption") == "unchanged"

    # 2) 来源变化（+2/-1），再检查
    feats = F1[:-1] + [_feature("s4", 400), _feature("s5", 500)]
    svc.payload_provider = StaticPayloadProvider({REF: _payload(2, feats)})
    r = await _call("webgis_check_project_source_updates", {"project_id": PID})
    assert r.get("changed") == 1 and r.get("stale") is True

    # 3) 预览：精确差异数字来自真实 diff 算法，不是编造
    r = await _call("webgis_preview_project_data_refresh", {"project_id": PID})
    assert r.get("success") is True, r
    pid = r["proposal_id"]
    counts = r["diffs"]["items"][0]["counts"]
    assert counts == {"added": 2, "removed": 1,
                      "attribute_changed": 0, "geometry_changed": 0}, r
    assert r["preview_is_read_only"] is True
    assert r["map_observation"] == "unverified"

    # 4) 解释过期（为什么更新）
    r = await _call("webgis_explain_stale_map_product",
                    {"project_id": PID, "proposal_id": pid})
    assert r.get("stale") is True and r.get("reasons"), r

    # 5) 执行隔离候选
    r = await _call("webgis_execute_project_data_refresh",
                    {"project_id": PID, "proposal_id": pid})
    assert r.get("success") is True, r
    assert r.get("candidate_status") == "succeeded"
    assert r.get("version_adoption") == "unchanged"   # 执行 ≠ 采纳

    # 6) 状态查询（刷新后恢复）
    r = await _call("webgis_project_refresh_status",
                    {"project_id": PID, "proposal_id": pid})
    assert r.get("recoverable") is True

    # 7) 采纳：三态分离，不伪造 renderer ACK
    r = await _call("webgis_adopt_project_data_refresh",
                    {"project_id": PID, "proposal_id": pid})
    assert r.get("status") == "adopted", r
    assert r.get("revision_after") == 4
    assert r.get("tool_execution") == "ok"
    assert r.get("task_execution") == "ok"
    assert r.get("version_adoption") == "adopted"
    assert r.get("map_observation") == "unverified"

    # 8) 回退
    r = await _call("webgis_rollback_project_data_refresh",
                    {"project_id": PID, "proposal_id": pid})
    assert r.get("status") == "rolled_back", r
    assert r.get("revision_after") == 5


@pytest.mark.asyncio
async def test_o11_failed_tool_call_discloses_failure_not_fake_success():
    r = await _call("webgis_adopt_project_data_refresh",
                    {"project_id": PID, "proposal_id": "g10p-does-not-exist"})
    assert r.get("success") is False
    assert r.get("tool_execution") == "failed"
    assert r.get("version_adoption") == "unchanged"
    assert r.get("map_observation") == "unverified"
