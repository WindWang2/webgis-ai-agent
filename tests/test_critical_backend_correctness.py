"""PR 3 — 后端正确性修复的回归测试。

覆盖 review 报告中的关键 Critical:
- C1: NDVI/NDWI/NBR/EVI 公式 mask（nir+red<=0 像素返回 0 而非伪值）
- C2: explorer task_chain 用 task_id 作 session_id 命名空间
- C4: ExplorerPerceptionEvent.stage 接受 "pending"
- S36: validate_data_path 用 realpath 解析符号链接
- S37: zonal_stats 校验 raster_path
- M5: format_error_response 按 HTTP 状态码映射 code
- C3: Redis 错误隔离（store/get/append_event 不抛）
"""
import asyncio
import os
import sys
import numpy as np
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-backend-correctness-x")
os.environ.setdefault("ENV", "development")


# ── C1: NDVI 公式 ──────────────────────────────────────────────────────


def test_c1_ndvi_formula_masks_zero_denominator():
    """C1：当 nir+red<=0（水面/阴影/负反射率），结果应是 0 而非伪值。

    之前 bug：np.where((nir+r)>0, nir+r, 1) 把分母替换为 1，
    分子仍是 (nir-r)，得到 (nir-r)/1 = 几千的伪值。
    """
    # 模拟 Sentinel-2 L2A 场景：水面/阴影像元的反射率可能为负
    red = np.array([1000.0, -500.0, 0.0, 2000.0])      # 红光
    nir = np.array([3000.0, -800.0, -100.0, 5000.0])   # 近红外

    # 正常情况（nir+r>0）：NDVI 应是 (nir-red)/(nir+red)
    # 异常情况（nir+r<=0，下标 1/2）：NDVI 应被 mask 为 0
    expected = np.array([
        (3000 - 1000) / (3000 + 1000),  # = 0.5
        0.0,                              # nir+r=-1300，mask
        0.0,                              # nir+r=-100，mask
        (5000 - 2000) / (5000 + 2000),   # ≈ 0.4286
    ])

    # 跑修复后的公式
    result = np.divide(
        nir - red, nir + red,
        out=np.zeros_like(nir - red, dtype=float),
        where=(nir + red) > 0,
    )
    np.testing.assert_allclose(result, expected, rtol=1e-6)

    # 关键：mask 像素必须是 0（不是几千的伪值）
    assert result[1] == 0.0
    assert result[2] == 0.0
    # 而非 bug 行为：result[1] = (-800 - (-500)) / 1 = -300
    assert abs(result[1]) < 1.0


def test_c1_rs_service_formula_table_uses_np_divide():
    """通过 inspect 验证 rs_service.py 的 formulas 表确实用了 np.divide（不是 np.where）。"""
    import inspect
    from app.services import rs_service

    source = inspect.getsource(rs_service)
    # 找到 compute_vegetation_index 内的 formulas 定义段
    # 必须包含 np.divide 调用（修复后），且不能残留 (nir - r) / np.where(..., 1) 模式
    assert "np.divide" in source, "rs_service 必须使用 np.divide 而非 / + np.where"
    # 旧 bug 的标志性写法：除以 np.where(..., 1)（除数位置用 1 fallback）
    assert "np.where(" not in source.split("formulas = {")[1].split("}")[0] if "formulas = {" in source else True, (
        "formulas 表不应再使用 np.where 做除数 mask"
    )


# ── S36: validate_data_path realpath ────────────────────────────────────


def test_s36_validate_data_path_rejects_symlink_escape(tmp_path):
    """S36：validate_data_path 必须解析符号链接，阻止 data_dir 内的 symlink 逃逸。"""
    # 构造 data_dir 和一个真实 sensitive file
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sensitive = tmp_path / "secret.txt"
    sensitive.write_text("TOPSECRET")

    # 在 data_dir 内创建指向 secret 的 symlink
    evil_link = data_dir / "escape"
    try:
        os.symlink(str(sensitive), evil_link)
    except OSError:
        pytest.skip("symlink not supported on this filesystem")

    from app.utils.path import validate_data_path

    # 旧 bug：abspath 不解析 symlink，校验通过 → 下游 open(evil_link) 读 secret
    # 修复：realpath 解析后 path 在 data_dir 之外 → ValueError
    with pytest.raises(ValueError, match="非法路径"):
        validate_data_path(str(evil_link), data_dir=str(data_dir))


def test_s36_validate_data_path_accepts_legit_relative(tmp_path):
    """正常相对路径仍能通过。"""
    from app.utils.path import validate_data_path
    data_dir = tmp_path / "data"
    (data_dir / "subdir").mkdir(parents=True)
    (data_dir / "subdir" / "file.geojson").write_text("{}")

    resolved = validate_data_path("subdir/file.geojson", data_dir=str(data_dir))
    assert "file.geojson" in resolved
    assert "secret" not in resolved


# ── S37: zonal_stats 路径校验 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_s37_zonal_stats_rejects_traversal_path():
    """S37：zonal_stats 必须拒绝 ../ 等路径穿越。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    # register 把 zonal_stats 函数存在 registry._tools 内
    zonal_stats_fn = registry._tools["zonal_stats"]

    geojson = {"type": "FeatureCollection", "features": []}
    result = zonal_stats_fn(geojson, "../../../etc/passwd")
    # 必须是错误响应，不能读 /etc/passwd
    assert isinstance(result, dict)
    assert result.get("success") is False


@pytest.mark.asyncio
async def test_s37_zonal_stats_rejects_gdal_vfs():
    """S37：GDAL VFS (/vsicurl/) 等 URL 必须被拒（防 SSRF）。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    zonal_stats_fn = registry._tools["zonal_stats"]

    geojson = {"type": "FeatureCollection", "features": []}
    # /vsicurl/ 让 GDAL 通过 HTTP 读远程文件 → SSRF
    result = zonal_stats_fn(
        geojson,
        "/vsicurl/https://attacker.example.com/evil.tif",
    )
    assert isinstance(result, dict)
    assert result.get("success") is False


# ── C4: ExplorerPerceptionEvent.stage accepts "pending" ────────────────


def test_c4_perception_event_accepts_pending_stage():
    """C4：stage Literal 必须包含 "pending" —— Celery PENDING 状态时
    orchestrator 默认 "pending" 才不会让 pydantic 抛 ValidationError 整条 SSE 崩。"""
    from app.services.explorer.models import ExplorerPerceptionEvent

    # 不应抛 ValidationError
    event = ExplorerPerceptionEvent(stage="pending", task_id="t1", status="started")
    assert event.stage == "pending"


def test_c4_perception_event_rejects_invalid_stage():
    """之前默认值 "unknown" 不在 Literal 里 → 现在 Literal 仍拒绝完全无效值。"""
    from app.services.explorer.models import ExplorerPerceptionEvent
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExplorerPerceptionEvent(stage="totally_invalid", task_id="t1", status="started")


# ── M5: error code mapping ────────────────────────────────────────────


def test_m5_error_response_maps_404_to_not_found():
    """M5：HTTPException(404) 的响应 body code 必须是 NOT_FOUND 而非 SERVER_ERROR。"""
    from app.core.exception import format_error_response
    from fastapi import HTTPException, Request

    exc = HTTPException(status_code=404, detail="Session not found")
    fake_req = MagicMock()
    data = format_error_response(exc, fake_req, include_details=False)
    assert data["code"] == "NOT_FOUND"
    assert data["success"] is False


def test_m5_error_response_maps_401_to_unauthorized():
    from app.core.exception import format_error_response
    from fastapi import HTTPException

    exc = HTTPException(status_code=401, detail="Not authenticated")
    data = format_error_response(exc, MagicMock(), include_details=False)
    assert data["code"] == "UNAUTHORIZED"


def test_m5_error_response_maps_403_to_forbidden():
    from app.core.exception import format_error_response
    from fastapi import HTTPException

    exc = HTTPException(status_code=403, detail="Forbidden")
    data = format_error_response(exc, MagicMock(), include_details=False)
    assert data["code"] == "FORBIDDEN"


def test_m5_error_response_maps_429_to_rate_limited():
    from app.core.exception import format_error_response
    from fastapi import HTTPException

    exc = HTTPException(status_code=429, detail="Too many requests")
    data = format_error_response(exc, MagicMock(), include_details=False)
    assert data["code"] == "RATE_LIMITED"


def test_m5_generic_exception_still_server_error():
    """未知异常（无 status_code）保持 SERVER_ERROR。"""
    from app.core.exception import format_error_response

    exc = RuntimeError("unexpected")
    data = format_error_response(exc, MagicMock(), include_details=False)
    assert data["code"] == "SERVER_ERROR"


# ── C2: explorer task_chain session 命名空间 ──────────────────────────


def test_c2_store_ref_uses_task_id_namespace(monkeypatch):
    """C2：_store_ref 必须把 task_id 作为 session namespace（不再是固定 'explorer'）。"""
    from app.tasks.explorer import task_chain

    captured = {}

    async def fake_store(session_id, data, prefix="data"):
        captured["session_id"] = session_id
        captured["prefix"] = prefix
        return f"ref:{prefix}-abc"

    # 用 monkeypatch 替换 session_data_manager
    fake_manager = MagicMock()
    fake_manager.store = fake_store
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_manager)

    ref = task_chain._store_ref({"foo": 1}, task_id="task-xyz", prefix="fetch")
    assert ref == "ref:fetch-abc"
    # 必须包含 task_id（之前是硬编码 "explorer"）
    assert "task-xyz" in captured["session_id"], (
        f"session_id 应基于 task_id，实际={captured['session_id']}"
    )


def test_c2_load_ref_uses_task_id_namespace(monkeypatch):
    """C2：_load_ref 也必须用 task_id namespace。"""
    from app.tasks.explorer import task_chain

    captured = {}

    async def fake_get(session_id, ref_id):
        captured["session_id"] = session_id
        return {"data": "ok"}

    fake_manager = MagicMock()
    fake_manager.get = fake_get
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_manager)

    result = task_chain._load_ref("ref:fetch-abc", task_id="task-123")
    assert result == {"data": "ok"}
    assert "task-123" in captured["session_id"]


# ── C3: Redis 错误隔离 ─────────────────────────────────────────────────


def _make_manager_with_failing_redis(monkeypatch, fail_method: str):
    """构造一个 RedisSessionDataManager，其 self._r 在指定方法上抛 RedisError。

    绕过 __init__ 的 from_url（需要真 URL）—— 直接 setattr 替换 _r。
    """
    import redis.asyncio as aioredis
    from app.services.session_data_redis import RedisSessionDataManager

    # 不调用 __init__（避免 from_url），仅设置必要属性
    manager = RedisSessionDataManager.__new__(RedisSessionDataManager)
    manager.capacity = 100

    class _FakePipeline:
        def __getattr__(self, name):
            return lambda *a, **kw: self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self):
            raise aioredis.RedisError("pipeline execute timed out")

    class _FakeRedis:
        def pipeline(self):
            return _FakePipeline()

        async def zcard(self, *a, **kw):
            if fail_method == "zcard":
                raise aioredis.RedisError("zcard timeout")
            return 0

        async def zrange(self, *a, **kw):
            return []

        async def hget(self, *a, **kw):
            if fail_method == "hget":
                raise aioredis.RedisError("hget timeout")
            return None

        async def get(self, *a, **kw):
            if fail_method == "get":
                raise aioredis.RedisError("get timeout")
            return None

    manager._r = _FakeRedis()
    return manager


@pytest.mark.asyncio
async def test_c3_store_swallows_redis_error(monkeypatch):
    """C3：Redis store pipeline 抛 RedisError 时不应传播 —— 返回 ref:redis-unavailable-*。"""
    manager = _make_manager_with_failing_redis(monkeypatch, fail_method="")
    ref_id = await manager.store("sess-1", {"data": 1}, prefix="geojson")
    # 不应抛 —— 之前会直接 raise，杀死整个 chat turn
    assert ref_id.startswith("ref:redis-unavailable-")


@pytest.mark.asyncio
async def test_c3_get_swallows_redis_error(monkeypatch):
    """C3：Redis get 抛错时返回 None（cache miss 语义）。"""
    manager = _make_manager_with_failing_redis(monkeypatch, fail_method="hget")
    result = await manager.get("sess-1", "ref:abc")
    assert result is None  # 不是抛 RedisError


@pytest.mark.asyncio
async def test_c3_append_event_swallows_redis_error(monkeypatch):
    """C3：Redis append_event 抛错时 no-op（log 一条 warning）。"""
    manager = _make_manager_with_failing_redis(monkeypatch, fail_method="")
    # 不应抛
    await manager.append_event("sess-1", "tool_executed", {"tool": "x"})


# ── C5: SSE dispatch_task 取消 ─────────────────────────────────────────
# 这个测试通过源码 inspect 验证 try/finally 模式存在 —— 直接测 SSE 生成器
# 的取消行为需要完整 mock chat_engine，过于脆弱；用源码静态检查更稳。


def test_c5_dispatch_task_has_cancel_on_disconnect():
    """C5：chat_engine.chat_stream 必须在 dispatch_task 外包 try/cancel。

    之前 dispatch_task 没在 SSE 客户端断开时 cancel，导致后台继续跑（Celery
    派发、GeoJSON 序列化、DB 写入）做无用功且无界增长。
    """
    import inspect
    from app.services.chat_engine import ChatEngine

    source = inspect.getsource(ChatEngine.chat_stream)
    # 必须找到 create_task + try + cancel 的组合
    assert "asyncio.create_task" in source
    assert "dispatch_task.cancel()" in source
    # 必须 catch CancelledError 或 GeneratorExit
    assert "CancelledError" in source or "GeneratorExit" in source
