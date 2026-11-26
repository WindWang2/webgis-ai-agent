"""WP4 · 任务数据供给工具面行为化 dispatch 测试。

真实依赖：artifact_registry 会话产物账本 + data_catalog 联邦目录
（与 test_behavioral_data_discovery 同款种子纪律）；供给计划存储为进程内
owner 隔离 LRU。覆盖：

- 注册：5 工具在 registry、归类 data_access；
- discover_task_data：observation 种子 → subject 候选；未知角色类型化拒绝；
- compare_task_coverage：bbox 相交（无 footprint）→ declared_only +
  「未核验」披露——bbox 粗筛绝不宣称全覆盖；
- plan_task_supply → 状态查询；requires_confirmation 可见；
- execute_supply_plan：未批准拒绝 / 跨会话不可见 / 需确认列出 pending /
  用户选择 keep_gap 后可执行（local 通道零获取 completed）；
- 存储 owner 隔离：会话 A 的计划对会话 B 不可见（不泄漏存在性）。
"""
import pytest

from app.services.artifact_registry import register_artifact
from app.services.data_catalog.catalog import reset_data_catalog
from app.services.data_lifecycle.service import reset_lifecycle_service
from app.tools.categories import classify_tool
from app.tools.registry import ToolRegistry
from app.tools.task_data_supply_tools import register_task_data_supply_tools


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_task_data_supply_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _reset_catalog_and_ledger():
    reset_data_catalog()
    reset_lifecycle_service()
    yield
    reset_data_catalog()
    reset_lifecycle_service()


async def _seed(session_id: str) -> None:
    await register_artifact(
        session_id, artifact_id="ref:poi", producer_tool="query_osm_poi",
        artifact_type="poi_feature_set",
        descriptor={"bbox": [104.0, 30.0, 105.0, 31.0], "feature_count": 120,
                    "crs": "EPSG:4326"},
        metadata={"field_schema": {"name": {"type": "string"}},
                  "logical_role": "observation", "tags": ["osm", "poi"],
                  "display_name": "成都POI"},
    )


def test_tools_registered_and_categorized(registry):
    for name in ("discover_task_data", "compare_task_coverage",
                 "plan_task_supply", "execute_supply_plan",
                 "get_supply_plan_status"):
        assert name in registry.tool_names(), name
        assert classify_tool(
            name, "app.tools.task_data_supply_tools") == "data_access"


@pytest.mark.asyncio
async def test_discover_rejects_unknown_role(registry):
    out = await registry.dispatch(
        "discover_task_data",
        {"place": "成都", "roles_csv": "school_pois"},
        session_id="tds-a")
    assert out.get("success") is False
    assert out.get("code") == "TASK_SUPPLY_INVALID_ARGS"
    assert "REQUIREMENT_ROLE_UNKNOWN" in (out.get("message") or "")


@pytest.mark.asyncio
async def test_discover_and_compare_declared_only(registry):
    sid = "tds-discover"
    await _seed(sid)
    out = await registry.dispatch(
        "discover_task_data",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject"},
        session_id=sid)
    assert out.get("success") is True, out
    ids = [c["candidate_id"] for c in out.get("candidates") or []]
    assert "ref:poi" in ids
    # 目录条目无 footprint → 声明面候选
    preview = out.get("coverage_preview") or {}
    rows = preview.get("roles") or []
    assert rows and rows[0]["evaluations"]
    ev = rows[0]["evaluations"][0]
    assert ev["verdict"] == "declared_only"
    assert any("未核验" in d for d in ev["disclosures"])

    cmp_out = await registry.dispatch(
        "compare_task_coverage",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject"},
        session_id=sid)
    assert cmp_out.get("success") is True
    cov = cmp_out.get("coverage") or {}
    assert cov.get("roles") and cov["roles"][0]["role"] == "subject"


@pytest.mark.asyncio
async def test_compare_disjoint_task_coarse_filtered(registry):
    sid = "tds-disjoint"
    await _seed(sid)  # 种子在成都
    out = await registry.dispatch(
        "compare_task_coverage",
        {"bbox": "116.0,39.0,117.0,40.0", "roles_csv": "subject"},
        session_id=sid)  # 任务在北京
    assert out.get("success") is True
    # 目录 bbox 粗筛生效：不相交候选在发现层被排除（诚实披露，非静默）
    meta = out.get("discovery_meta") or {}
    assert meta.get("bbox_coarse_filter") is True
    assert not (out.get("candidates") or [])
    ev = (out.get("coverage") or {}).get("roles", [{}])[0]
    assert not (ev.get("evaluations") or [])
    assert ev.get("unknown_reasons")  # 未搜索 ≠ 不存在，有解释


@pytest.mark.asyncio
async def test_plan_store_status_and_owner_isolation(registry):
    sid = "tds-plan"
    await _seed(sid)
    built = await registry.dispatch(
        "plan_task_supply",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject",
         "budget_max_bytes": 10_000_000},
        session_id=sid)
    assert built.get("success") is True, built
    plan = built.get("plan") or {}
    pid = plan["plan_id"]
    assert plan["state"] in ("requires_confirmation", "ready")

    status = await registry.dispatch(
        "get_supply_plan_status", {"plan_id": pid}, session_id=sid)
    assert status.get("success") is True
    assert (status.get("plan") or {}).get("plan_id") == pid

    # 跨会话不可见（不泄漏存在性）
    other = await registry.dispatch(
        "get_supply_plan_status", {"plan_id": pid},
        session_id="tds-plan-other")
    assert other.get("success") is False
    assert other.get("code") == "TASK_SUPPLY_PLAN_NOT_FOUND"


@pytest.mark.asyncio
async def test_execute_requires_approval_and_confirmation(registry):
    sid = "tds-exec"
    await _seed(sid)
    built = await registry.dispatch(
        "plan_task_supply",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject"},
        session_id=sid)
    pid = (built.get("plan") or {})["plan_id"]

    # 未批准 → 类型化拒绝
    no = await registry.dispatch(
        "execute_supply_plan", {"plan_id": pid}, session_id=sid)
    assert no.get("code") == "TASK_SUPPLY_NOT_APPROVED"

    # 需确认（declared_only 有替代方案且未决策）→ 列 pending
    conf = await registry.dispatch(
        "execute_supply_plan", {"plan_id": pid, "approved": True},
        session_id=sid)
    if conf.get("code") == "TASK_SUPPLY_PLAN_NEEDS_CONFIRMATION":
        assert "subject" in (conf.get("pending_roles") or [])

    # 用户选择 keep_gap（保留缺口）→ 计划 ready → 可执行
    rebuilt = await registry.dispatch(
        "plan_task_supply",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject",
         "choices_json": '{"subject": "keep_gap"}'},
        session_id=sid)
    plan2 = rebuilt.get("plan") or {}
    assert plan2["state"] == "ready", plan2["state"]
    pid2 = plan2["plan_id"]
    run = await registry.dispatch(
        "execute_supply_plan",
        {"plan_id": pid2, "approved": True}, session_id=sid)
    assert run.get("success") is True, run
    results = run.get("results") or []
    assert results and results[0]["outcome"] == "completed"
    assert results[0]["receipt_ref"].startswith("ref:poi")


@pytest.mark.asyncio
async def test_execute_cross_session_plan_not_visible(registry):
    sid = "tds-x"
    await _seed(sid)
    built = await registry.dispatch(
        "plan_task_supply",
        {"bbox": "104.0,30.0,105.0,31.0", "roles_csv": "subject"},
        session_id=sid)
    pid = (built.get("plan") or {})["plan_id"]
    out = await registry.dispatch(
        "execute_supply_plan",
        {"plan_id": pid, "approved": True},
        session_id="tds-x-other")
    assert out.get("success") is False
    assert out.get("code") == "TASK_SUPPLY_PLAN_NOT_FOUND"
