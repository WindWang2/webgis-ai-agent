"""#543 安全回归：监测报告/资产工具必须把 UploadRecord 查询限定到调用会话。

矩阵（匿名 / 拥有者 / 非拥有者 / 跨会话）在真实 SQLite 上验证查询语义
（非 mock），确保 session_id 真正参与了 SQL 过滤，而不是只改了接口签名。

背景：`UploadRecord.id` 是顺序自增整数、易枚举；`session_id` 是唯一的归属
令牌。修复前 `generate_monitoring_report._load_assets` 只按 id 查询（session_id
参数是死参数），`manage_analysis_asset` 只按 id 查找 + 条件式会话校验。
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base

import app.models.upload  # noqa: F401  (registers UploadRecord)


@pytest.fixture
def upload_db(tmp_path, monkeypatch):
    """真实 SQLite DB + 把两个工具模块的 db_session 指向它。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'uploads.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    from app.models.upload import UploadRecord

    with Session() as s:
        s.add_all(
            [
                # 他人资产（raster_analysis）—— 必须不可见/不可删
                UploadRecord(
                    id=1, filename="uploads/a/1.geojson", original_name="bob_secret.csv",
                    file_type="vector", format="geojson", geometry_type="raster_analysis",
                    feature_count=1, bbox=[116, 39, 116.5, 39.5], file_size=66_300,
                    session_id="session-BOB",
                ),
                # 本会话资产（raster_analysis）—— 可见/可管理
                UploadRecord(
                    id=2, filename="b/2.geojson", original_name="alice_ok.csv",
                    file_type="vector", format="geojson", geometry_type="raster_analysis",
                    feature_count=1, bbox=[117, 40, 117.5, 40.5], file_size=10_000,
                    session_id="session-ALICE",
                ),
                # 本会话但非 raster_analysis —— 报告工具不可引用、管理工具不可动
                UploadRecord(
                    id=3, filename="c/3.geojson", original_name="plain_upload.csv",
                    file_type="vector", format="geojson", geometry_type="Point",
                    feature_count=1, bbox=[118, 41, 118.5, 41.5], file_size=5_000,
                    session_id="session-ALICE",
                ),
                # NULL session 旧匿名记录（raster_analysis）—— 必须拒绝
                UploadRecord(
                    id=4, filename="d/4.geojson", original_name="anonymous.tif",
                    file_type="raster", format="geotiff", geometry_type="raster_analysis",
                    feature_count=0, bbox=None, file_size=20_000,
                    session_id=None,
                ),
            ]
        )
        s.commit()

    def make_session_ctx():
        @contextmanager
        def ctx():
            session = Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        return ctx

    monkeypatch.setattr("app.tools.monitoring_report.db_session", make_session_ctx())
    monkeypatch.setattr("app.tools.nature_resources.db_session", make_session_ctx())

    yield Session
    engine.dispose()


# ── generate_monitoring_report._load_assets ─────────────────────────────


def test_load_assets_cross_session_returns_empty(upload_db):
    """跨会话：ALICE 请求 BOB 的资产必须返回空，而不是泄露元数据。"""
    from app.tools.monitoring_report import _load_assets

    assert _load_assets([1], session_id="session-ALICE") == []
    assert _load_assets([2], session_id="session-BOB") == []


def test_load_assets_owner_returns_record(upload_db):
    """拥有者：本会话资产可正常加载（报告引用合法资产仍要工作）。"""
    from app.tools.monitoring_report import _load_assets

    assets = _load_assets([2], session_id="session-ALICE")
    assert len(assets) == 1
    assert assets[0]["id"] == 2
    assert assets[0]["name"] == "alice_ok.csv"


def test_load_assets_anonymous_returns_empty(upload_db):
    """匿名（无 session_id）：拒绝而非返回全局资产（与 list_uploaded_data 同语义）。"""
    from app.tools.monitoring_report import _load_assets

    assert _load_assets([1, 2]) == []


def test_load_assets_filters_non_raster_analysis(upload_db):
    """契约过滤：非 raster_analysis 上传（即使同会话）不能被报告引用。"""
    from app.tools.monitoring_report import _load_assets

    assert _load_assets([3], session_id="session-ALICE") == []


def test_load_assets_mixed_ids_only_returns_owned(upload_db):
    """混合 id 列表：只返回本会话的 raster_analysis 资产。"""
    from app.tools.monitoring_report import _load_assets

    assets = _load_assets([1, 2, 3, 4], session_id="session-ALICE")
    assert [a["id"] for a in assets] == [2]


def test_generate_report_rejects_more_than_100_assets(upload_db, tmp_path, monkeypatch):
    """资产数量上限：>100 直接拒绝，防止 LLM 借工具枚举全库。"""
    import app.tools.monitoring_report as mr_mod
    from app.tools.monitoring_report import register_monitoring_report_tools
    from app.tools.registry import ToolRegistry

    monkeypatch.setattr(mr_mod, "REPORT_OUTPUT_DIR", str(tmp_path / "reports"))
    registry = ToolRegistry()
    register_monitoring_report_tools(registry)
    fn = registry._tools["generate_monitoring_report"]

    result = fn(
        title="t", region_name="r", period="p",
        analysis_assets=list(range(101)), session_id="session-ALICE",
    )
    assert "error" in result
    assert "上限" in result["error"]


# ── manage_analysis_asset ───────────────────────────────────────────────


def _nature_tool_fn():
    from app.tools.nature_resources import register_nature_resource_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_nature_resource_tools(registry)
    return registry._tools["manage_analysis_asset"]


def test_manage_asset_cross_session_delete_refused(upload_db):
    """跨会话删除：BOB 删 ALICE 的资产必须被拒绝。"""
    fn = _nature_tool_fn()
    result = fn(asset_id=2, action="delete", session_id="session-BOB")
    assert "error" in result
    assert "不属于当前会话" in result["error"]


def test_manage_asset_delete_refused_for_non_raster(upload_db):
    """越权类型：同会话的普通 upload（Point）不能通过资产工具删除。"""
    fn = _nature_tool_fn()
    result = fn(asset_id=3, action="delete", session_id="session-ALICE")
    assert "error" in result
    assert "未找到对应的分析资产记录" in result["error"]


def test_manage_asset_delete_refused_for_null_session_record(upload_db):
    """NULL-session 旧匿名记录：任何会话都不能通过工具管理（须走上传路由）。"""
    fn = _nature_tool_fn()
    result = fn(asset_id=4, action="delete", session_id="session-ALICE")
    assert "error" in result
    assert "未关联会话" in result["error"]
    # 匿名上下文同样拒绝
    result2 = fn(asset_id=4, action="delete")
    assert "error" in result2


def test_manage_asset_anonymous_owner_rename_refused(upload_db):
    """匿名（无 session_id）：即使资产真实存在也不能操作。"""
    fn = _nature_tool_fn()
    result = fn(asset_id=2, action="rename", new_name="x", session_id=None)
    assert "error" in result
    assert "不属于当前会话" in result["error"]


def test_manage_asset_owner_rename_allowed(upload_db):
    """拥有者 + raster_analysis：rename 正常放行（合法同会话行为不被破坏）。"""
    fn = _nature_tool_fn()
    result = fn(asset_id=2, action="rename", new_name="春季NDVI", session_id="session-ALICE")
    assert result.get("success") is True


def test_manage_asset_owner_delete_removes_file_and_row(upload_db, tmp_path, monkeypatch):
    """拥有者 + raster_analysis：delete 放行，物理文件与 DB 行一并删除。"""
    from app.core.config import settings
    from app.models.upload import UploadRecord

    # record.filename 是相对路径 'b/2.geojson'；把 DATA_DIR 指到 tmp 并落一个真实文件
    data_dir = tmp_path / "data"
    (data_dir / "b").mkdir(parents=True)
    (data_dir / "b" / "2.geojson").write_text("{}")
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir))

    fn = _nature_tool_fn()
    result = fn(asset_id=2, action="delete", session_id="session-ALICE")
    assert result.get("success") is True
    assert not (data_dir / "b" / "2.geojson").exists(), "物理文件应被删除"

    with upload_db() as s:
        assert s.get(UploadRecord, 2) is None, "DB 行应被删除"
