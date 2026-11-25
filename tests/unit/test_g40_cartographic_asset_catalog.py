"""G40 制图符号资产目录 DB/服务测试（照 G04 composition DB 测试模式）。

覆盖：模型建表与约束、生命周期（create→new_version→publish→retire→
restore）、scope/IDOR 矩阵（S19：跨 owner 猜 ID 不可读）、内容 digest
对账（S23 服务端面）、S18 引用检查与删除、S27 出口收集冻结、builtin
解析路径。
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — 注册全部模型（含本方向新表）
from app.core.database import Base
from app.lib.cartography.symbol_asset import (
    get_builtin_asset,
)
from app.lib.cartography.svg_import import import_svg_asset
from app.services.cartographic_assets import catalog as svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


GOOD_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M 12 3 L 22 20 L 2 20 Z" fill="#2563eb"/></svg>'
)


def _doc(name="t"):
    return import_svg_asset(GOOD_SVG, kind="point_symbol", name=name)


def test_create_publish_new_version_retire_restore(db):
    asset, v1 = svc.create_asset(db, document=_doc(), creator_id="u1",
                                 publish=True)
    assert asset.id.startswith("cartoasset.")
    assert asset.status == "published" and v1.revision == 1

    v2 = svc.new_version(db, asset.id, document=_doc("v2"), user_id="u1",
                         publish=True)
    assert v2.revision == 2 and asset.current_revision == 2

    svc.retire_asset(db, asset.id, user_id="u1", note="done")
    assert asset.status == "retired"
    # 新绑定拒绝（retired）
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.resolve_version_document(db, asset.id, 2, user_id="u1")
    assert ei.value.reason == "asset_retired"
    # 钉版本重放只读放行
    doc, meta = svc.resolve_version_document(
        db, asset.id, 2, user_id="u1", for_apply=False)
    assert meta["status"] == "retired"

    v3 = svc.restore_asset(db, asset.id, 1, user_id="u1")
    assert v3.revision == 3 and asset.current_revision == 3
    assert asset.status == "published"


def test_s19_scope_matrix_cross_owner_invisible(db):
    asset, _ = svc.create_asset(db, document=_doc(), creator_id="u1",
                                org_id=1, publish=True)
    # 跨 owner 猜 ID：不可见 = asset_not_found（不泄露存在性）
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.resolve_version_document(db, asset.id, 1, user_id="u2", org_id=2)
    assert ei.value.reason == "asset_not_found"
    # 同 org + visibility=org 可见
    asset.visibility = "org"
    db.flush()
    doc, _ = svc.resolve_version_document(db, asset.id, 1, user_id="u2",
                                          org_id=1)
    assert doc is not None
    # 写仍限 creator（可见 ≠ 可写）
    with pytest.raises(svc.CartographicAssetForbiddenError):
        svc.new_version(db, asset.id, document=_doc("x"), user_id="u2",
                        org_id=1)
    # admin 同 org 可写
    v = svc.new_version(db, asset.id, document=_doc("admin"), user_id="u9",
                        org_id=1, role="admin", publish=True)
    assert v.revision == 2


def test_digest_pin_mismatch_rejected(db):
    asset, _ = svc.create_asset(db, document=_doc(), creator_id="u1",
                                publish=True)
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.resolve_version_document(
            db, asset.id, 1, expected_digest="sha256:" + "0" * 64,
            user_id="u1")
    assert ei.value.reason == "asset_content_mismatch"


def test_s18_delete_version_reference_guard(db, tmp_path, monkeypatch):
    asset, v1 = svc.create_asset(db, document=_doc(), creator_id="u1",
                                 publish=True)
    svc.new_version(db, asset.id, document=_doc("v2"), user_id="u1",
                    publish=True)
    # 当前发布版本不可删
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.delete_asset_version(db, asset.id, 2, user_id="u1")
    assert ei.value.reason == "asset_in_use"

    # 旧版本被会话 MapSpec 引用 → 拒删（扫描真实磁盘 mapspec.json）
    store = tmp_path / "mapspecs"
    store.mkdir()
    sid_dir = store / "sess-1"
    sid_dir.mkdir()
    (sid_dir / "mapspec.json").write_text(json.dumps({
        "layers": [{"id": "pts", "symbol_asset": {
            "asset_id": asset.id, "revision": 1,
            "content_digest": v1.content_digest, "kind": "point_symbol"}}],
    }), encoding="utf-8")
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(store))
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.delete_asset_version(db, asset.id, 1, user_id="u1")
    assert ei.value.reason == "asset_in_use"

    # 解除引用（换内容）后可删
    (sid_dir / "mapspec.json").write_text(json.dumps({"layers": []}),
                                          encoding="utf-8")
    out = svc.delete_asset_version(db, asset.id, 1, user_id="u1")
    assert out["deleted"] is True


def test_builtin_resolution_and_revision_guard():
    doc, meta = svc.resolve_version_document(db=None,
                                             asset_id="cartoasset.builtin.triangle",
                                             revision=1)
    assert meta["builtin"] is True and doc.kind == "point_symbol"
    with pytest.raises(svc.CartographicAssetError):
        svc.resolve_version_document(db=None,
                                     asset_id="cartoasset.builtin.triangle",
                                     revision=2)
    with pytest.raises(svc.CartographicAssetError):
        svc.resolve_version_document(db=None, asset_id="cartoasset.builtin.nope",
                                     revision=1)


def test_s27_collect_for_export_freezes_pins(db, tmp_path, monkeypatch):
    asset, v1 = svc.create_asset(db, document=_doc(), creator_id="u1",
                                 publish=True)
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    mapspec = {"layers": [
        {"id": "a", "symbol_asset": {
            "asset_id": asset.id, "revision": 1,
            "content_digest": v1.content_digest, "kind": "point_symbol"}},
        {"id": "b", "symbol_asset": {
            "asset_id": "cartoasset.builtin.triangle", "revision": 1,
            "content_digest": tri.content_digest, "kind": "point_symbol"}},
        {"id": "c", "symbol_asset": {
            "asset_id": "cartoasset.builtin.nope", "revision": 1,
            "content_digest": "sha256:" + "f" * 64, "kind": "point_symbol"}},
    ]}
    # 匿名（user_id=None）：DB 资产不可见 → 失败披露；builtin 可解析
    docs, failures = svc.collect_symbol_asset_documents(db, mapspec)
    assert tri.content_digest in docs
    assert any(f["asset_id"] == asset.id for f in failures)
    assert any(f["asset_id"] == "cartoasset.builtin.nope" for f in failures)
    # 属主身份：全部解析
    docs2, failures2 = svc.collect_symbol_asset_documents(
        db, mapspec, user_id="u1")
    assert v1.content_digest in docs2
    # 属主身份下唯一剩余失败 = 真不存在的 builtin（正确披露）
    assert [f["asset_id"] for f in failures2] == ["cartoasset.builtin.nope"]


def test_list_assets_kind_filter_and_pagination(db):
    for i in range(3):
        svc.create_asset(db, document=_doc(f"p{i}"), creator_id="u1",
                         publish=True)
    tile_svg = (b'<svg viewBox="0 0 12 12">'
                b'<path d="M0 6 L12 6" stroke="#333333" stroke-width="1.5"/></svg>')
    tex = import_svg_asset(tile_svg, kind="area_texture", name="tex")
    svc.create_asset(db, document=tex, creator_id="u1", publish=True)
    items, total = svc.list_assets(db, user_id="u1", org_id=None, kind="point_symbol")
    assert total == 3 and all(i["kind"] == "point_symbol" for i in items)
    page, _ = svc.list_assets(db, user_id="u1", org_id=None, limit=2, offset=0)
    assert len(page) == 2
    # 跨 owner 不可见
    other, total2 = svc.list_assets(db, user_id="u2", org_id=None)
    assert total2 == 0


# ── 评审修复回归（孤儿 blob / rollback / LIMIT / 非对象 mapspec） ─────────

class _RecordingStore:
    """只记录 put_blob 的 BlobStore 替身（孤儿 blob 断言用）。"""

    def __init__(self):
        self.puts = []

    def put_blob(self, key, blob, media_type="json"):
        self.puts.append(key)


def test_rejected_document_never_reaches_blob_store(db, monkeypatch):
    """被拒（digest/kind/字节预算）文档不得先落 BlobStore 留下孤儿 blob。"""
    store = _RecordingStore()
    monkeypatch.setattr(svc, "_content_store", lambda: store)

    bad_digest = _doc().model_copy(
        update={"content_digest": "sha256:" + "0" * 64})
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.create_asset(db, document=bad_digest, creator_id="u1")
    assert ei.value.reason == "asset_document_error"
    assert store.puts == []

    asset, _ = svc.create_asset(db, document=_doc(), creator_id="u1")
    assert len(store.puts) == 1
    store.puts.clear()
    tile_svg = (b'<svg viewBox="0 0 12 12">'
                b'<path d="M0 6 L12 6" stroke="#333333" stroke-width="1.5"/></svg>')
    tex = import_svg_asset(tile_svg, kind="area_texture", name="tex")
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.new_version(db, asset.id, document=tex, user_id="u1")
    assert ei.value.reason == "asset_kind_mismatch"
    assert store.puts == []

    monkeypatch.setattr(svc, "MAX_ASSET_JSON_BYTES", 16)
    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.create_asset(db, document=_doc("oversize"), creator_id="u1")
    assert ei.value.reason == "asset_document_error"
    assert store.puts == []


def test_create_asset_flush_failure_keeps_caller_pending_writes(db, monkeypatch):
    """flush 失败只回滚本资产插入（savepoint），不丢调用方未提交写入。"""
    from app.models.cartographic_symbol_asset import CartographicSymbolAsset

    conflict_hex = "f" * 32
    conflict_id = "cartoasset." + conflict_hex
    db.add(CartographicSymbolAsset(
        id=conflict_id, creator_id="u1", visibility="private",
        name="prior", kind="point_symbol", status="draft",
        latest_revision=0, current_revision=0))

    class _FixedUUID:
        hex = conflict_hex

    monkeypatch.setattr(svc.uuid, "uuid4", lambda: _FixedUUID())
    store = _RecordingStore()
    monkeypatch.setattr(svc, "_content_store", lambda: store)

    with pytest.raises(svc.CartographicAssetError) as ei:
        svc.create_asset(db, document=_doc(), creator_id="u1")
    assert ei.value.reason == "asset_conflict"
    assert db.get(CartographicSymbolAsset, conflict_id) is not None


def test_list_assets_pages_in_sql(db, monkeypatch):
    """分页必须在 SQL 层 LIMIT，不得全量回传后在 Python 切片。"""
    from sqlalchemy import event

    monkeypatch.setattr(svc, "_content_store", lambda: _RecordingStore())
    for i in range(3):
        svc.create_asset(db, document=_doc(f"p{i}"), creator_id="u1",
                         publish=True)
    statements = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", _capture)
    try:
        items, total = svc.list_assets(db, user_id="u1", org_id=None, limit=1)
    finally:
        event.remove(db.bind, "before_cursor_execute", _capture)
    assert total == 3 and len(items) == 1
    asset_selects = [
        s for s in statements
        if "cartographic_symbol_assets" in s
        and s.lstrip().upper().startswith("SELECT")]
    assert any(" LIMIT " in s.upper() for s in asset_selects), asset_selects


@pytest.mark.parametrize("payload", [[1, 2], "not-an-object", 42])
def test_reference_scan_tolerates_non_object_mapspec_json(
        db, tmp_path, monkeypatch, payload):
    """mapspec.json 合法但非对象：引用扫描跳过，删除走 typed 结果而非崩。"""
    asset, _ = svc.create_asset(db, document=_doc(), creator_id="u1",
                                publish=True)
    svc.new_version(db, asset.id, document=_doc("v2"), user_id="u1",
                    publish=True)
    store = tmp_path / "mapspecs"
    store.mkdir()
    sid_dir = store / "sess-bad"
    sid_dir.mkdir()
    (sid_dir / "mapspec.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(store))
    out = svc.delete_asset_version(db, asset.id, 1, user_id="u1")
    assert out["deleted"] is True
