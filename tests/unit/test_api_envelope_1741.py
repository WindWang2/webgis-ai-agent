"""#1741 regression — API 错误面三处修复的回归锁。

1. **限流 429 回 legacy 单体**：``RateLimitMiddleware`` 被限流时改走统一
   错误信封（复用 ``app.core.exception.unified_error_envelope``，与路由内
   ``HTTPException(429)`` 逐字段一致：code=RATE_LIMITED /
   category=resource_exhausted / retryable=true）；显式
   ``x-error-envelope: detail`` 仍可回退旧体（灰度通道不破）。
2. **chat.py 生产 assert**：Pi 分支的 ``assert pi_session_id`` 在
   ``python -O`` 下被整体剥除 —— 改为显式 None 检查 + 结构化错误信封。
   本文件用 AST 锁住「该路由模块不再有裸 assert」。
3. **project.py 按消息子串分类 404/409**：原
   ``404 if "not found" in str(e) else 409`` 把状态码建立在自由文本匹配上
   —— 引用实体的存在性改为路由边界显式查询判定（数据驱动 404），其余
   引擎 ValueError 一律 409。AST 锁 + 行为锁（run 缺失 / 跨项目 /
   不可恢复 / 合并缺侧 / 记录版本绑定不存在的 run）。
"""
import ast
import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── 1. 限流中间件 429 → 统一错误信封 ─────────────────────────────────────


class _DenyAllLimiter:
    async def is_allowed(self, key, max_requests, window_seconds):
        return False


def _rl_scope(path: str = "/api/v1/chat", headers=()):
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": list(headers),
        "query_string": b"",
        "client": ("203.0.113.9", 1234),
        "scheme": "http",
        "server": ("test", 80),
        "root_path": "",
    }


@pytest.fixture()
def _rate_limited_middleware(monkeypatch):
    from app.main import RateLimitMiddleware

    async def _get_rate_limiter():
        return _DenyAllLimiter()

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "get_rate_limiter", _get_rate_limiter)
    return RateLimitMiddleware(app=lambda *a, **k: None, max_requests=1, window_seconds=60)


async def _dispatch_429(mw, headers=()):
    from starlette.requests import Request

    async def call_next(request):  # pragma: no cover - never reached
        raise AssertionError("limiter denied the request; call_next must not run")

    return await mw.dispatch(Request(_rl_scope(headers=headers)), call_next)


@pytest.mark.asyncio
async def test_rate_limit_429_uses_unified_error_envelope(_rate_limited_middleware):
    """被限流的响应体与路由内 HTTPException(429) 同形 —— 不再回 {"detail"}。"""
    resp = await _dispatch_429(_rate_limited_middleware)

    assert resp.status_code == 429
    body = json.loads(resp.body)
    assert "detail" not in body, "429 must not answer with the legacy single-key envelope"
    assert body["code"] == "RATE_LIMITED"
    assert body["success"] is False
    assert body["message"] == "请求过于频繁，请稍后再试"
    assert body["data"] is None
    # 分类字段与路由内 429 一致（classify_exception(HTTPException(429))）。
    assert body["category"] == "resource_exhausted"
    assert body["retryable"] is True


@pytest.mark.asyncio
async def test_rate_limit_429_legacy_header_still_gets_detail(_rate_limited_middleware):
    """灰度通道不破：显式 x-error-envelope: detail 仍回旧体（未迁移客户端）。"""
    resp = await _dispatch_429(
        _rate_limited_middleware, headers=[(b"x-error-envelope", b"detail")]
    )

    assert resp.status_code == 429
    assert json.loads(resp.body) == {"detail": "请求过于频繁，请稍后再试"}


# ─── 2. chat.py：Pi 分支不得再用 assert（-O 下被剥除）──────────────────────


def test_chat_route_has_no_bare_assert():
    """AST 锁：chat 路由模块（生产代码）不得有裸 assert 语句。

    修复前 `if use_pi: assert pi_session_id` 是模块内唯一一处；改为显式
    None 检查 + 结构化 HTTPException detail 后归零。
    """
    tree = ast.parse((_REPO_ROOT / "app/api/routes/chat.py").read_text(encoding="utf-8"))
    offenders = [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)
    ]
    assert not offenders, f"bare assert in app/api/routes/chat.py at lines {offenders}"


def test_chat_pi_branch_guards_session_id_explicitly():
    """Pi 分支用显式 falsy 检查 + 结构化 detail（500 兜底），而非 assert。"""
    src = (_REPO_ROOT / "app/api/routes/chat.py").read_text(encoding="utf-8")
    # 只匹配语句行的 assert（注释里引用旧代码不算）。
    assert not re.search(r"^\s*assert\s+pi_session_id", src, re.MULTILINE)
    assert re.search(r"^\s*if not pi_session_id:", src, re.MULTILINE)
    assert "PI_SESSION_MISSING" in src


# ─── 3. project.py：404/409 不得按异常消息子串分类 ─────────────────────────


def test_project_routes_do_not_classify_status_by_message_text():
    """AST 锁：不再有 `"not found" in str(e)` 这类自由文本状态码分类。"""
    tree = ast.parse(
        (_REPO_ROOT / "app/api/routes/project.py").read_text(encoding="utf-8")
    )
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In):
            continue
        left, right = node.left, node.comparators[0]
        if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
            continue
        if isinstance(right, ast.Call) and getattr(right.func, "id", "") == "str":
            offenders.append((node.lineno, left.value))
    assert not offenders, (
        "status code must not be decided by substring matching an exception "
        f"message: {offenders}"
    )


def test_project_routes_map_engine_valueerror_to_conflict():
    """余下引擎 ValueError 一律 409（_engine_conflict），不再猜状态码。"""
    from app.api.routes.project import _engine_conflict

    exc = _engine_conflict(ValueError("cannot resume run r1 with status 'completed'"))
    assert exc.status_code == 409
    assert exc.detail == "cannot resume run r1 with status 'completed'"


# ─── 3b. 行为锁：run / map-product 引用实体的 404 与冲突 409 ──────────────

client = TestClient(__import__("app.main", fromlist=["app"]).app)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    import app.models.db_model  # noqa: F401 — register metadata
    import app.models.project  # noqa: F401

    # 引擎调用前路由会取共享 registry —— 未 init_tools 的进程里它直接抛
    # PiRpcError（同 tests/unit/test_workflow_api.py 的接法）。
    class _FakeRegistry:
        def tool_version(self, name):
            return "1.0#cv1"

        async def dispatch(self, name, args, session_id=None):
            return {"success": True, "ref_id": f"ref:{name}", "feature_count": 1}

    import app.api.routes.project as route_mod
    monkeypatch.setattr(route_mod, "get_tool_registry", lambda: _FakeRegistry())

    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        from app.models.db_model import User

        db.merge(User(id="wf-1741", username="wf-1741", email="wf-1741@example.com",
                      password_hash="not-a-real-hash", role="viewer", is_active=True))
        db.commit()
    yield


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _auth_headers():
    from app.core.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"sub": "wf-1741", "username": "wf-1741", "role": "viewer"})}


def _make_project(name: str) -> str:
    res = client.post("/api/v1/projects", json={"name": name}, headers=_auth_headers())
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _insert_run(project_id: str, workflow_id: str, status: str = "completed") -> str:
    from app.models.project import WorkflowRun

    run_id = _unique("run")
    with SessionLocal() as db:
        db.add(WorkflowRun(
            id=run_id, workflow_id=workflow_id, project_id=project_id,
            status=status, completed_steps=[], graph_snapshot={"steps": []},
        ))
        db.commit()
    return run_id


def _make_workflow(project_id: str, name: str) -> str:
    res = client.post(f"/api/v1/projects/{project_id}/workflows", json={
        "name": name, "graph_spec": {"steps": [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        ]},
    }, headers=_auth_headers())
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_replay_unknown_run_is_404_not_409():
    """run 不存在 → 404（此前靠 "not found" 子串才落到 404）。"""
    project_id = _make_project(_unique("proj"))
    resp = client.post(
        f"/api/v1/projects/{project_id}/runs/{_unique('missing')}/replay",
        json={"mode": "exact"}, headers=_auth_headers())
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "NOT_FOUND"
    assert "not found" in body["message"]


def test_replay_run_from_another_project_is_404():
    """跨项目 run → 404（不泄露存在性；原实现落 409）。"""
    owner_project = _make_project(_unique("proj"))
    other_project = _make_project(_unique("proj"))
    workflow_id = _make_workflow(owner_project, _unique("wf"))
    run_id = _insert_run(owner_project, workflow_id)

    resp = client.post(
        f"/api/v1/projects/{other_project}/runs/{run_id}/replay",
        json={"mode": "exact"}, headers=_auth_headers())
    assert resp.status_code == 404, resp.text
    assert "not found in project" in resp.json()["message"]


def test_resume_of_completed_run_is_conflict_409():
    """状态机拒绝 → 409（引擎 ValueError 的诚实映射，非"猜"出来的）。"""
    project_id = _make_project(_unique("proj"))
    workflow_id = _make_workflow(project_id, _unique("wf"))
    run_id = _insert_run(project_id, workflow_id, status="completed")

    resp = client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/resume",
        json={"allow_rerun": False}, headers=_auth_headers())
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "CONFLICT"
    assert "cannot resume" in body["message"]


def test_merge_with_missing_version_is_404():
    """合并缺侧 → 404，且 detail 指出缺失的版本号（在侧存在）。"""
    project_id = _make_project(_unique("proj"))
    res = client.post(f"/api/v1/projects/{project_id}/map-products",
                      json={"mapspec_fingerprint": "fp-1741"},
                      headers=_auth_headers())
    assert res.status_code == 201, res.text
    from_version_no = res.json()["version_no"]

    resp = client.post(
        f"/api/v1/projects/{project_id}/map-products/merge",
        json={"from_version_no": from_version_no, "to_version_no": 9999},
        headers=_auth_headers())
    assert resp.status_code == 404, resp.text
    assert "9999" in resp.json()["message"]


def test_record_version_with_unknown_run_is_404():
    """记录版本绑定不存在的 run → 404（project-scoped 显式校验）。"""
    project_id = _make_project(_unique("proj"))
    resp = client.post(
        f"/api/v1/projects/{project_id}/map-products",
        json={"workflow_run_id": _unique("norun"), "mapspec_fingerprint": "fp-x"},
        headers=_auth_headers())
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "NOT_FOUND"
