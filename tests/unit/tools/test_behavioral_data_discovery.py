"""行为化 dispatch 测试 —— 数据发现 4 工具（session 域目录/账本/血缘）。

真实依赖：app/services/artifact_registry 的会话产物账本 + data_catalog 的
联邦目录 + lifecycle service 契约视图。用 ``register_artifact`` 种子数据，
``reset_data_catalog`` / ``reset_lifecycle_service`` 隔离进程级缓存。
"""
import pytest

from app.services.artifact_registry import register_artifact
from app.services.data_catalog.catalog import reset_data_catalog
from app.services.data_lifecycle.service import reset_lifecycle_service
from app.tools.data_discovery import register_data_discovery_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_data_discovery_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _reset_catalog_and_ledger():
    reset_data_catalog()
    reset_lifecycle_service()
    yield
    reset_data_catalog()
    reset_lifecycle_service()


async def _seed(session_id: str) -> None:
    """目录/账本种子：observation 来源 → analysis 结果（有血缘边）。"""
    await register_artifact(
        session_id, artifact_id="ref:poi", producer_tool="query_osm_poi",
        artifact_type="poi_feature_set",
        descriptor={"bbox": [116.0, 39.0, 117.0, 40.0], "feature_count": 120,
                    "crs": "EPSG:4326"},
        metadata={"field_schema": {"name": {"type": "string"}},
                  "logical_role": "observation", "tags": ["osm", "poi"],
                  "display_name": "北京POI"},
    )
    await register_artifact(
        session_id, artifact_id="ref:kde", producer_tool="kde",
        artifact_type="density_surface", inputs=["ref:poi"],
        descriptor={"bbox": [116.0, 39.0, 117.0, 40.0], "feature_count": 500},
    )


@pytest.mark.asyncio
async def test_search_datasets_behavioral(registry):
    sid = "bdds-search"
    # validation：LLM 幻觉参数（bbox 不在声明 schema） → 显式拒绝并列出合法参数
    bad = await registry.dispatch("search_datasets", {
        "session_id": sid, "bbox": [1.0, 2.0, 3.0]})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"
    assert "bbox" in (bad.get("message") or "")

    # error path（空会话语义）：无数据 → 诚实空结果。
    # 注意（W10 顺序卫生暴露）：data_catalog 会列出**全局** fabric 连接
    # （其他测试文件注册且不清理），空断言不能假设全局注册表为空 ——
    # 只断言无会话私有条目泄漏 + 若有条目必为全局 fabric 作用域。
    empty = await registry.dispatch("search_datasets", {"session_id": "bdds-none"})
    assert empty.get("success") is True
    entries = empty.get("datasets") or []
    assert all(e.get("scope") == "fabric" for e in entries), (
        f"空会话不得泄漏非全局条目: {entries}")
    assert (empty.get("count") or 0) == len(entries)

    # happy path：按角色检索种子目录 → 命中 observation 条目
    await _seed(sid)
    out = await registry.dispatch("search_datasets", {
        "session_id": sid, "role": "observation", "keyword": "POI"})
    assert out.get("success") is True, out
    assert out.get("count") == 1
    entries = out.get("datasets") or []
    assert entries and entries[0].get("id") == "ref:poi"


@pytest.mark.asyncio
async def test_describe_artifact_behavioral(registry):
    sid = "bdds-desc"
    # validation：缺 ref_id → 校验错误
    bad = await registry.dispatch("describe_artifact", {"session_id": sid})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：账本中不存在的产物 → NOT_FOUND
    missing = await registry.dispatch("describe_artifact", {
        "session_id": sid, "ref_id": "ref:ghost"})
    assert isinstance(missing, dict) and missing.get("success") is False
    assert missing.get("code") == "NOT_FOUND"
    assert "ref:ghost" in (missing.get("error") or "")

    # happy path：账本产物 → V3 契约视图 + 质量提示
    await _seed(sid)
    out = await registry.dispatch("describe_artifact", {
        "session_id": sid, "ref_id": "ref:poi"})
    assert out.get("success") is True, out
    artifact = out.get("artifact") or {}
    assert artifact.get("artifact_id") == "ref:poi"
    assert artifact.get("subtype") == "poi_feature_set"
    assert artifact.get("produced_by") == "query_osm_poi"
    assert artifact.get("role") == "observation"
    assert "quality_hint" in out


@pytest.mark.asyncio
async def test_find_artifacts_by_role_behavioral(registry):
    sid = "bdds-role"
    # validation：缺 role → 校验错误
    bad = await registry.dispatch("find_artifacts_by_role", {"session_id": sid})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path（空会话语义）：无账本 → 空清单
    empty = await registry.dispatch(
        "find_artifacts_by_role", {"session_id": "bdds-none", "role": "source"})
    assert empty.get("success") is True
    assert empty.get("count") == 0

    # happy path：按 logical_role 过滤 → 只返回 observation 条目
    await _seed(sid)
    out = await registry.dispatch(
        "find_artifacts_by_role", {"session_id": sid, "role": "observation"})
    assert out.get("success") is True, out
    ids = [d.get("id") for d in out.get("datasets") or []]
    assert ids == ["ref:poi"]
    assert out.get("count") == 1


@pytest.mark.asyncio
async def test_get_lineage_behavioral(registry):
    sid = "bdds-lin"
    # validation：缺 ref_id → 校验错误
    bad = await registry.dispatch("get_lineage", {"session_id": sid})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # error path：账本中不存在的产物 → NOT_FOUND
    missing = await registry.dispatch("get_lineage", {
        "session_id": sid, "ref_id": "ref:ghost"})
    assert isinstance(missing, dict) and missing.get("success") is False
    assert missing.get("code") == "NOT_FOUND"

    # happy path：ref:kde ← ref:poi 血缘边可见
    await _seed(sid)
    out = await registry.dispatch("get_lineage", {
        "session_id": sid, "ref_id": "ref:kde", "depth": 3})
    assert out.get("success") is True, out
    lineage = out.get("lineage") or {}
    node_ids = {
        n.get("artifact_id") or n.get("id")
        for n in lineage.get("nodes") or []
    }
    assert "ref:kde" in node_ids and "ref:poi" in node_ids
