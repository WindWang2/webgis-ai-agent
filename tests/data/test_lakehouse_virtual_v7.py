"""Lakehouse V7 Wave 10 — Virtual DataObject（零字节复制组合）。

覆盖面（ADR-0119 §5，评审 R0-4/28）：
- 发布：children 引用身份（content root = 有序 ids）、确定性（同 children
  同 id）、零 content blob（byte_size=0, blob_count=0）；
- verify 递归：children 缺失 ≠ verified（virtual_children_missing）——
  绝不让零 blob 假绿；
- 解析预算：菱形 DAG（共享 child 多路径）visited 集合不指数；
- owner：跨 owner child = owner_mismatch；物化 owner 校验强制；
- 物化策略：lazy = 引用清单零复制；inline 预算内升级为真实物化。
"""
from __future__ import annotations

import pytest

from app.services.lakehouse.data_object import (
    DATA_OBJECT_KINDS,
    DataObjectError,
    normalize_owner_scope,
    publish_data_object,
    resolve_data_object,
)
from app.services.lakehouse.virtual_object import (
    MAX_VIRTUAL_NODES,
    VirtualObjectError,
    materialize_virtual_object,
    publish_virtual_object,
    resolve_virtual_object,
    verify_data_object_deep,
)


def _leaf(store, session="sess-v", tag: bytes = b"leaf"):
    identity = publish_data_object(
        {"data.bin": tag}, kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id=session),
        store=store,
    )
    return identity.data_object_id


def test_publish_virtual_deterministic_and_zero_copy(tmp_path):
    from app.services.durable_blob_store import FilesystemBlobStore

    store = FilesystemBlobStore(tmp_path)
    scope = normalize_owner_scope(session_id="sess-v")
    c1, c2 = _leaf(store, tag=b"a"), _leaf(store, tag=b"b")
    v1 = publish_virtual_object(
        [c1, c2], kind_label="temporal_view", owner_scope=scope,
        selection={"time": ["2024-01"]}, store=store,
    )
    v2 = publish_virtual_object(
        [c2, c1], kind_label="temporal_view", owner_scope=scope,
        selection={"time": ["2024-01"]}, store=store,
    )
    assert v1["data_object_id"] == v2["data_object_id"]  # 确定性（顺序无关）
    manifest = resolve_data_object(v1["data_object_id"], store=store)
    assert manifest["kind"] == "virtual"
    assert manifest["content_blobs"] == []  # 零字节复制
    assert manifest["byte_size"] == 0
    assert manifest["payload"]["virtual"]["children"] == sorted([c1, c2])
    # 跨 owner 同 children = 不同逻辑身份。
    v_other = publish_virtual_object(
        [c1, c2], kind_label="temporal_view",
        owner_scope=normalize_owner_scope(session_id="sess-other"),
        selection={"time": ["2024-01"]}, store=store,
    )
    assert v_other["data_object_id"] != v1["data_object_id"]


def test_verify_detects_missing_child(tmp_path):
    from app.services.durable_blob_store import FilesystemBlobStore

    store = FilesystemBlobStore(tmp_path)
    scope = normalize_owner_scope(session_id="sess-v")
    c1 = _leaf(store, tag=b"only")
    v = publish_virtual_object(
        [c1], kind_label="view", owner_scope=scope, store=store,
    )
    assert verify_data_object_deep(v["data_object_id"], store=store) == "verified"
    # child 被 GC（manifest 删除）→ 深度校验诚实缺席（绝不假绿 —— R0-4）。
    store.delete_blob(c1)
    assert verify_data_object_deep(
        v["data_object_id"], store=store
    ) == "virtual_children_missing"
    report = resolve_virtual_object(v["data_object_id"], store=store)
    assert report["state"] == "children_missing"
    assert report["missing"] == [c1]


def test_resolution_budget_bounded_on_diamond(tmp_path):
    """菱形 DAG：共享 child 被多条路径引用 —— visited 集合防指数。"""
    from app.services.durable_blob_store import FilesystemBlobStore

    store = FilesystemBlobStore(tmp_path)
    scope = normalize_owner_scope(session_id="sess-v")
    leaf = _leaf(store, tag=b"shared")

    def _diamond(levels):
        current = [leaf]
        for _depth in range(levels):
            left = publish_virtual_object(
                current, kind_label="l", owner_scope=scope, store=store,
            )["data_object_id"]
            right = publish_virtual_object(
                current, kind_label="r", owner_scope=scope, store=store,
            )["data_object_id"]
            current = [left, right]
        return publish_virtual_object(
            current, kind_label="root", owner_scope=scope, store=store,
        )["data_object_id"]

    # 7 层菱形 + root = 8 虚拟跳（恰在 MAX_VIRTUAL_DEPTH 契约内）。
    root = _diamond(7)
    report = resolve_virtual_object(root, store=store)
    assert report["state"] == "ok"
    # 结构性证据：visited 数 == 实际不同节点数（非路径数 2^7）。
    assert report["visited"] <= 2 ** 7 + 2
    assert report["visited"] < MAX_VIRTUAL_NODES
    # 9 层嵌套 = 超出深度契约 → typed corrupt 状态（有界纪律）。
    deep = _diamond(9)
    deep_report = resolve_virtual_object(deep, store=store)
    assert deep_report["state"] == "child_corrupt"


def test_materialization_lazy_and_inline_and_owner(tmp_path):
    from app.services.durable_blob_store import FilesystemBlobStore

    store = FilesystemBlobStore(tmp_path)
    scope = normalize_owner_scope(session_id="sess-v")
    c1 = _leaf(store, tag=b"x" * 100)
    v_lazy = publish_virtual_object(
        [c1], kind_label="lazy_view", owner_scope=scope,
        materialization="lazy", store=store,
    )
    out = materialize_virtual_object(
        v_lazy["data_object_id"], tmp_path / "lazy",
        owner_session_id="sess-v", store=store,
    )
    assert out["materialized"] == "lazy"
    assert (tmp_path / "lazy" / "VIRTUAL_CHILDREN.json").is_file()
    # inline 预算内 → 真实物化。
    v_inline = publish_virtual_object(
        [c1], kind_label="inline_view", owner_scope=scope,
        materialization="inline", inline_max_bytes=10_000, store=store,
    )
    out2 = materialize_virtual_object(
        v_inline["data_object_id"], tmp_path / "inline",
        owner_session_id="sess-v", store=store,
    )
    assert out2["materialized"] == "inline"
    assert (tmp_path / "inline" / "child_0000" / "data.bin").read_bytes() == b"x" * 100
    # inline 预算外 → 诚实拒绝（不静默降级）。
    v_big = publish_virtual_object(
        [c1], kind_label="big_view", owner_scope=scope,
        materialization="inline", inline_max_bytes=1, store=store,
    )
    out3 = materialize_virtual_object(
        v_big["data_object_id"], tmp_path / "big",
        owner_session_id="sess-v", store=store,
    )
    assert out3["materialized"] == "refused"
    # owner 不符 = typed 拒绝（绝不物化越权内容）。
    with pytest.raises(VirtualObjectError, match="different owner"):
        materialize_virtual_object(
            v_lazy["data_object_id"], tmp_path / "evil",
            owner_session_id="sess-attacker", store=store,
        )


def test_kinds_constant_shared_and_virtual_guard():
    assert "virtual" in DATA_OBJECT_KINDS
    with pytest.raises(DataObjectError, match="children"):
        from app.services.lakehouse.data_object import build_object_manifest

        build_object_manifest(
            kind="virtual", owner_scope=normalize_owner_scope(session_id="s"),
            entries=[], payload={"virtual": {"children": []}},
        )


def test_invalid_child_ids_rejected(tmp_path):
    from app.services.durable_blob_store import FilesystemBlobStore

    store = FilesystemBlobStore(tmp_path)
    with pytest.raises(VirtualObjectError, match="data object ids"):
        publish_virtual_object(
            ["not-an-id"], kind_label="bad",
            owner_scope=normalize_owner_scope(session_id="sess-v"),
            store=store,
        )
