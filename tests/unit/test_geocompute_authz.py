"""GeoCompute 数据平面 authz / 身份隔离测试（SEC 评审垂直，ADR-0096）。

覆盖五条防线：
1. 目录项准入谓词镜像（= data_fabric._require_tenant_owned 语义）真值表；
2. QUERY / SOURCE_SCAN 在适配器执行**之前**做准入 —— deny 是类型化
   AUTHORIZATION_DENIED，且注入的 query_catalog_fn 绝不被调用；
3. 节点复用键 owner 域隔离（不同 owner 同指纹绝不共享缓存条目）；
4. REST：execute/runs/drift-check 强制认证（401）、run 读隔离（他人 404）、
   外来 session_id → 404（写 IDOR，镜像 data_fabric 约定）；
5. 失败证据 error_message 去绝对路径 + 截断（类型化 code 不受影响）。

目录访问全部 DB-free：SessionLocal 打桩为返回目录项替身，
``ops.catalog_authorize_fn``（可注入谓词）按用例注入 owned/foreign 行为。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.geocompute import (
    AuthorizationError,
    ExecutionNode,
    ExecutionPlan,
    ExecutionRunStatus,
    GeoExecutionEngine,
    NodeCategory,
    NodeExecutionError,
    ops,
)
from app.services.geocompute.executor import _scrub_error_message, owner_scope_for
from app.services.geocompute import graph

client = TestClient(app)


# --------------------------------------------------------------- stub infra


class _StubQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **kw):
        return self

    def first(self):
        return self._row


class _StubDB:
    """DB-free 会话替身：按 model 名路由 query().filter().first()。"""

    def __init__(self, catalog_item=None, data_source=None):
        self._catalog_item = catalog_item
        self._data_source = data_source

    def query(self, model):
        if model.__name__ in {"DataFabricDataset", "CatalogItemModel"}:
            return _StubQuery(self._catalog_item)
        return _StubQuery(self._data_source)

    def close(self):
        pass


def _item(source_id="src-1"):
    return SimpleNamespace(source_id=source_id)


def _src(org_id=None, owner_id=None):
    return SimpleNamespace(org_id=org_id, owner_id=owner_id)


def _user(uid="u1", org_id=None):
    return {"user_id": uid, "role": "editor", "org_id": org_id}


def _query_node(node_id="q", dataset_id="cat-1"):
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.QUERY,
        parameters={"dataset_id": dataset_id, "query": {"limit": 5}},
    )


def _scan_node(node_id="s", dataset_id="cat-1"):
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.SOURCE_SCAN,
        parameters={"dataset_id": dataset_id},
    )


def _patch_catalog(monkeypatch, *, item, allow, calls):
    """注入 DB-free 目录行 + 可注入谓词 + 捕获型 query_catalog_fn。"""
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _StubDB(catalog_item=item))

    def authorize(db, item_, caller):
        calls["authz"] += 1
        calls["authz_caller"] = caller
        return allow

    monkeypatch.setattr(ops, "catalog_authorize_fn", authorize)

    def fake_query(db, item_id, spec):
        calls["query"] += 1
        calls["query_item_id"] = item_id
        return SimpleNamespace(features=[], total_matching=0, metadata={})

    monkeypatch.setattr(ops, "query_catalog_fn", fake_query)


# ------------------------------------------------- 1. predicate truth table


class TestCatalogAuthorizePredicate:
    """``ops._default_catalog_authorize_fn`` 镜像 data_fabric 租户谓词。"""

    def _authorize(self, src, caller):
        return ops._default_catalog_authorize_fn(_StubDB(data_source=src), _item(), caller)

    def test_anonymous_only_truly_public_rows(self):
        assert self._authorize(_src(None, None), None) is True
        assert self._authorize(_src(None, None), _user("x")) is True  # 无 org → 全局可见
        assert self._authorize(_src(7, None), None) is False
        assert self._authorize(_src(None, "owner"), None) is False

    def test_anonymous_sentinel_normalized_to_none(self):
        # get_current_user_optional 的匿名哨兵与 caller=None 同判。
        assert self._authorize(_src(None, "owner"), {"user_id": "anonymous"}) is False
        assert self._authorize(_src(None, None), {"user_id": "anon"}) is True

    def test_owner_match_allowed_foreign_denied(self):
        assert self._authorize(_src(None, "u1"), _user("u1")) is True
        assert self._authorize(_src(None, "u2"), _user("u1")) is False
        # org 相同但 owner 不同 → 无 org claim 的 caller 不可见（fail closed）
        assert self._authorize(_src(7, "u2"), _user("u1", org_id=None)) is False

    def test_org_claim_scoping(self):
        assert self._authorize(_src(7, "u2"), _user("u1", org_id=7)) is True
        assert self._authorize(_src(8, "u2"), _user("u1", org_id=7)) is False
        assert self._authorize(_src(None, "u1"), _user("u1", org_id=7)) is True  # 本人行

    def test_missing_source_row_denied(self):
        # 路由层对同一输入 404；数据平面 deny。
        assert self._authorize(None, _user("u1")) is False
        assert self._authorize(None, None) is False

    def test_mirror_matches_route_predicate_shape(self):
        # 与 data_fabric._require_tenant_owned 的判定分支一一对应的冒烟：
        # org 非空的行对「无 org claim 的其他用户」永远不可见。
        assert self._authorize(_src(7, "u2"), _user("u9")) is False
        assert self._authorize(_src(None, "u9"), _user("u9")) is True


# --------------------------------------- 2. enforcement before adapter exec


class TestQueryAuthzEnforcement:
    def test_query_denies_anonymous_before_query_fn(self, monkeypatch):
        calls = {"authz": 0, "query": 0, "authz_caller": None}
        _patch_catalog(monkeypatch, item=_item(), allow=False, calls=calls)
        ctx = ops.OperatorContext(run_id="r", node_id="q", caller=None)
        with pytest.raises(AuthorizationError) as ei:
            ops.execute_node(ctx, _query_node(), {})
        assert ei.value.code == "AUTHORIZATION_DENIED"
        assert calls["authz"] == 1, "authz predicate must run exactly once"
        assert calls["query"] == 0, "denied QUERY must never reach the adapter"

    def test_query_allows_owner_and_passes_caller(self, monkeypatch):
        calls = {"authz": 0, "query": 0, "authz_caller": None}
        _patch_catalog(monkeypatch, item=_item(), allow=True, calls=calls)
        ctx = ops.OperatorContext(run_id="r", node_id="q", caller=_user("u1"))
        payload = ops.execute_node(ctx, _query_node(), {})
        assert payload["metadata"]["feature_count"] == 0
        assert calls["authz"] == 1 and calls["query"] == 1
        assert calls["authz_caller"] == _user("u1"), "caller must reach the predicate"
        assert calls["query_item_id"] == "cat-1"

    def test_query_missing_item_typed_not_found(self, monkeypatch):
        calls = {"authz": 0, "query": 0, "authz_caller": None}
        _patch_catalog(monkeypatch, item=None, allow=True, calls=calls)
        ctx = ops.OperatorContext(run_id="r", node_id="q", caller=_user("u1"))
        with pytest.raises(NodeExecutionError, match="not found"):
            ops.execute_node(ctx, _query_node(), {})
        assert calls["query"] == 0

    def test_source_scan_denied_before_descriptor_read(self, monkeypatch):
        calls = {"authz": 0, "query": 0, "authz_caller": None}
        _patch_catalog(monkeypatch, item=_item(), allow=False, calls=calls)
        ctx = ops.OperatorContext(run_id="r", node_id="s", caller=_user("u1"))
        with pytest.raises(AuthorizationError) as ei:
            ops.execute_node(ctx, _scan_node(), {})
        assert ei.value.code == "AUTHORIZATION_DENIED"
        assert calls["authz"] == 1

    def test_source_scan_allows_public_item_for_anonymous(self, monkeypatch):
        calls = {"authz": 0, "query": 0, "authz_caller": None}
        descriptor = {"feature_count": 3, "fields": [{"name": "v", "type": "int"}]}
        item = SimpleNamespace(
            source_id="src-1", descriptor_json=descriptor, name="ds",
            source_type="generic", bbox_json=None, crs="EPSG:4326",
        )
        _patch_catalog(monkeypatch, item=item, allow=True, calls=calls)
        ctx = ops.OperatorContext(run_id="r", node_id="s", caller=None)
        payload = ops.execute_node(ctx, _scan_node(), {})
        assert payload["rows"][0]["feature_count"] == 3
        assert payload["metadata"]["scan"] == "descriptor_only"


# --------------------------------------------------- 3. reuse key isolation


def _filter_node(node_id="f1"):
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.FILTER,
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": [
                {"type": "Feature", "geometry": None,
                 "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i}}
                for i in range(4)
            ],
        },
    )


class TestReuseOwnerIsolation:
    def test_reuse_key_carries_owner_scope(self):
        node = _filter_node()
        k1 = graph.node_reuse_key("fp", node, "u:aaa")
        k2 = graph.node_reuse_key("fp", node, "u:bbb")
        assert k1 != k2
        assert k1.startswith("u:aaa:") and k2.startswith("u:bbb:")

    def test_owner_scope_prefers_user_then_session_then_anonymous(self):
        assert owner_scope_for(_user("alice")).startswith("u:")
        assert owner_scope_for(None, "sess-1").startswith("s:")
        assert owner_scope_for(None, "sess-1").startswith("s:")
        assert owner_scope_for(_user("alice"), "sess-1").startswith("u:")
        assert owner_scope_for(None, None) == "anonymous"
        assert owner_scope_for({"user_id": "anonymous"}) == "anonymous"
        assert owner_scope_for(None, "sess-1") != owner_scope_for(None, "sess-2")

    def test_same_fingerprint_different_owners_recompute(self):
        eng = GeoExecutionEngine(max_workers=1)
        run_a1 = eng.execute_plan(ExecutionPlan(plan_id="pa", nodes=[_filter_node()]),
                                  caller=_user("alice"))
        assert run_a1.evidence["f1"].status == "completed"
        # 同 owner：第二个同指纹计划命中复用
        run_a2 = eng.execute_plan(ExecutionPlan(plan_id="pb", nodes=[_filter_node()]),
                                  caller=_user("alice"))
        assert run_a2.evidence["f1"].status == "reused"
        # 不同 owner：同指纹必须重算（绝不复用他人结果）
        run_b = eng.execute_plan(ExecutionPlan(plan_id="pc", nodes=[_filter_node()]),
                                 caller=_user("bob"))
        assert run_b.status is ExecutionRunStatus.COMPLETED
        assert run_b.evidence["f1"].status == "completed"
        # 匿名域同样与真实用户隔离
        run_anon = eng.execute_plan(ExecutionPlan(plan_id="pd", nodes=[_filter_node()]))
        assert run_anon.evidence["f1"].status == "completed"
        run_anon2 = eng.execute_plan(ExecutionPlan(plan_id="pe", nodes=[_filter_node()]))
        assert run_anon2.evidence["f1"].status == "reused"


# ------------------------------------------------------ 4. REST authz/route


def _auth(user_id="gc-sec-user") -> dict[str, str]:
    from app.core.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user_id, 'role': 'editor'})}"}


def _plan_body():
    return {"plan": {"plan_id": "p-authz", "nodes": [_filter_node("f1").model_dump(mode="json")]}}


class TestRoutesAuthz:
    def test_execute_without_auth_401(self):
        resp = client.post("/api/v1/geocompute/plans/execute", json=_plan_body())
        assert resp.status_code == 401, resp.text

    def test_execute_with_auth_completes(self):
        resp = client.post("/api/v1/geocompute/plans/execute", json=_plan_body(),
                           headers=_auth("gc-runner"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

    def test_validate_stays_optional_auth(self):
        # 契约：纯 CPU 校验保持匿名可用（docstring 记录的理由）。
        resp = client.post("/api/v1/geocompute/plans/validate", json=_plan_body()["plan"])
        assert resp.status_code == 200, resp.text

    def test_run_read_isolated_across_users(self):
        created = client.post("/api/v1/geocompute/plans/execute", json=_plan_body(),
                              headers=_auth("gc-owner"))
        run_id = created.json()["run_id"]

        got = client.get(f"/api/v1/geocompute/runs/{run_id}", headers=_auth("gc-owner"))
        assert got.status_code == 200

        foreign = client.get(f"/api/v1/geocompute/runs/{run_id}", headers=_auth("gc-other"))
        assert foreign.status_code == 404  # 存在性不泄漏（不是 403）

        fsum = client.get(f"/api/v1/geocompute/runs/{run_id}/summary",
                          headers=_auth("gc-other"))
        assert fsum.status_code == 404

        anon = client.get(f"/api/v1/geocompute/runs/{run_id}")
        assert anon.status_code == 401

    def test_get_run_requires_auth(self):
        resp = client.get("/api/v1/geocompute/runs/whatever")
        assert resp.status_code == 401

    def test_drift_check_requires_auth(self):
        resp = client.post("/api/v1/geocompute/plans/drift-check", json={"stored": None})
        assert resp.status_code == 401
        ok = client.post("/api/v1/geocompute/plans/drift-check",
                         json={"stored": None}, headers=_auth())
        assert ok.status_code == 200
        assert ok.json()["state"] == "unknown"

    def test_foreign_session_denied(self):
        from app.core.database import SessionLocal
        from app.models.db_model import Conversation

        sess_id = "gc-authz-foreign-sess"
        db = SessionLocal()
        try:
            db.add(Conversation(id=sess_id, user_id="gc-someone-else"))
            db.commit()
        finally:
            db.close()
        try:
            body = _plan_body()
            body["session_id"] = sess_id
            resp = client.post("/api/v1/geocompute/plans/execute", json=body,
                               headers=_auth("gc-session-user"))
            assert resp.status_code == 404  # 镜像 data_fabric：404 而非 403
        finally:
            db = SessionLocal()
            try:
                row = db.query(Conversation).filter(Conversation.id == sess_id).first()
                if row is not None:
                    db.delete(row)
                    db.commit()
            finally:
                db.close()

    def test_unknown_session_allowed_first_write(self):
        body = _plan_body()
        body["session_id"] = "gc-authz-unknown-sess"
        resp = client.post("/api/v1/geocompute/plans/execute", json=body,
                           headers=_auth("gc-session-user"))
        assert resp.status_code == 200, resp.text


# ----------------------------------------------------- 5. error scrub (MINOR)


class TestErrorScrub:
    def test_scrub_replaces_absolute_paths(self):
        msg = _scrub_error_message("connect failed: /home/kevin/projects/webgis/tmp/boom.txt (2)")
        assert "/home/kevin" not in msg
        assert "<path>" in msg

    def test_scrub_truncates_to_300(self):
        assert len(_scrub_error_message("x" * 5000)) == 300

    def test_scrub_keeps_plain_messages(self):
        assert _scrub_error_message("catalog item 'x' not found") == "catalog item 'x' not found"
        assert _scrub_error_message(None) == ""

    def test_failed_node_evidence_has_no_absolute_paths(self, monkeypatch):
        def leaky(ctx, node, payloads):
            raise RuntimeError(
                "open /home/kevin/projects/webgis/data/secret.gpkg failed"
            )

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, leaky)
        node = ExecutionNode(node_id="leak", category=NodeCategory.SOURCE_SCAN,
                             parameters={"dataset_id": "d"})
        eng = GeoExecutionEngine(max_workers=1)
        run = eng.execute_plan(ExecutionPlan(plan_id="p-scrub", nodes=[node]))
        ev = run.evidence["leak"]
        assert ev.status == "failed"
        assert ev.error_code == "NODE_FAILED", "typed codes stay intact"
        assert "/home/kevin" not in (ev.error_message or "")
        assert "<path>" in (ev.error_message or "")
