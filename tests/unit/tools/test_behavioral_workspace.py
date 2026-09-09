"""行为化 dispatch 测试 —— 工作空间快照 3 工具（Workspace V4 Wave 2）。

真实依赖：session_data_manager 的会话载荷 + artifact_registry 账本 +
磁盘快照目录（BASE_STORAGE_DIR → tmp）。环境搭建与 tests/data/
test_workspace_v4.py 同款（DATA_DIR / content_store_root / BASE_STORAGE_DIR
全部隔离到 tmp_path）。
"""
import pytest

from app.services.artifact_registry import register_artifact
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import (
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)
from app.tools.registry import ToolRegistry
from app.tools.workspace_tools import register_workspace_tools

_FC = {
    "type": "FeatureCollection",
    "features": [
        {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
         "properties": {"name": "a"}},
    ],
}


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_workspace_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _reset_service():
    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """DATA_DIR → tmp；内容库根 → tmp 子目录（快照物化不污染仓库）。"""
    from app.core.config import settings
    from app.services import project_artifact_promotion as pap

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    monkeypatch.setattr(pap, "content_store_root", lambda: root / "project_artifacts")
    return root


@pytest.fixture()
def session_base(tmp_path, monkeypatch):
    """MapSpec 存储根 → tmp（快照文件落盘位置，调用时解析）。"""
    from app.services.mapspec import store as mapspec_store_module

    base = tmp_path / "webgis-agent"
    base.mkdir()
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", base)
    return base


async def _seed(session_id: str) -> str:
    """一个账本产物 + 存活载荷；返回 ref。"""
    ref = await session_data_manager.store(session_id, _FC, prefix="geojson")
    await register_artifact(session_id, artifact_id=ref,
                            producer_tool="query_osm_poi",
                            artifact_type="poi_feature_set")
    return ref


@pytest.mark.asyncio
async def test_save_workspace_snapshot_behavioral(registry, data_dir, session_base):
    sid = "bdws-save"
    # validation：缺会话 → NO_SESSION
    bad = await registry.dispatch("save_workspace_snapshot", {"label": "x"})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "NO_SESSION"

    # validation：非法 materialize 枚举 → INVALID_MATERIALIZE
    invalid = await registry.dispatch("save_workspace_snapshot", {
        "session_id": sid, "label": "x", "materialize": "everything"})
    assert isinstance(invalid, dict) and invalid.get("success") is False
    assert invalid.get("code") == "INVALID_MATERIALIZE"

    # happy path：materialize=claimed → 快照 id + 账本产物计数 + 持久指针
    ref = await _seed(sid)
    out = await registry.dispatch("save_workspace_snapshot", {
        "session_id": sid, "label": "before-merge", "materialize": "claimed"})
    assert out.get("success") is True, out
    assert isinstance(out.get("snapshot_id"), str) and out["snapshot_id"]
    assert out.get("label") == "before-merge"
    assert out.get("artifacts") == 1
    assert out.get("materialize") == "claimed"
    assert ref not in (out.get("materialize_skipped") or [])

    # 落盘真实发生：list 能看到该快照
    listed = await registry.dispatch(
        "list_workspace_snapshots", {"session_id": sid})
    assert listed.get("success") is True
    ids = [s.get("snapshot_id") for s in listed.get("snapshots") or []]
    assert out["snapshot_id"] in ids


@pytest.mark.asyncio
async def test_list_workspace_snapshots_behavioral(registry, data_dir, session_base):
    # validation/空语义：缺会话 → 诚实空清单（success=True + 提示信息）
    empty = await registry.dispatch("list_workspace_snapshots", {})
    assert empty.get("success") is True, empty
    assert empty.get("count") == 0 and empty.get("snapshots") == []
    assert (empty.get("message") or "").strip()

    sid = "bdws-list"
    # error path（空会话语义）：无快照会话 → count 0
    none = await registry.dispatch("list_workspace_snapshots", {"session_id": sid})
    assert none.get("success") is True and none.get("count") == 0

    # happy path：保存两次（不同标签）→ 两条有界摘要
    await _seed(sid)
    svc = get_workspace_snapshot_service()
    first = await svc.save_snapshot(sid, label="snap-a", materialize="none")
    second = await svc.save_snapshot(sid, label="snap-b", materialize="none")
    assert first is not None and second is not None
    out = await registry.dispatch("list_workspace_snapshots", {"session_id": sid})
    assert out.get("success") is True
    assert out.get("count") == 2
    labels = {s.get("label") for s in out.get("snapshots") or []}
    assert labels == {"snap-a", "snap-b"}


@pytest.mark.asyncio
async def test_restore_workspace_snapshot_behavioral(registry, data_dir, session_base):
    sid = "bdws-restore"
    # validation：缺 snapshot_id → 校验错误
    bad = await registry.dispatch("restore_workspace_snapshot", {"session_id": sid})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    # validation：非法 mode → INVALID_MODE
    invalid = await registry.dispatch("restore_workspace_snapshot", {
        "session_id": sid, "snapshot_id": "snap-x", "mode": "hard_reset"})
    assert isinstance(invalid, dict) and invalid.get("success") is False
    assert invalid.get("code") == "INVALID_MODE"

    # error path：不存在的快照 id + verify 模式 → 不报错，但核查报告诚实
    # 披露 exists=False / restorable=False（verify 的契约是"只出报告"）。
    # （语义备注：success=True + exists=False 组合略显宽松，但信息不撒谎。）
    missing = await registry.dispatch("restore_workspace_snapshot", {
        "session_id": sid, "snapshot_id": "snap-ghost", "mode": "verify"})
    assert isinstance(missing, dict) and missing.get("success") is True
    verification = missing.get("verification") or {}
    assert verification.get("exists") is False
    assert verification.get("restorable") is False

    # happy path：verify 模式核查真实快照 → 报告含产物核查结论
    await _seed(sid)
    saved = await get_workspace_snapshot_service().save_snapshot(
        sid, label="check-me", materialize="claimed")
    assert saved is not None
    out = await registry.dispatch("restore_workspace_snapshot", {
        "session_id": sid, "snapshot_id": saved.snapshot_id, "mode": "verify"})
    assert out.get("success") is True, out
    verification = out.get("verification") or {}
    assert verification.get("snapshot_id") == saved.snapshot_id
    assert verification.get("exists") is True
    assert verification.get("integrity_ok") is True
    assert verification.get("restorable") is True
