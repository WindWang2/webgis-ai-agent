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
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

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


def test_c1_rs_service_formula_masks_negative_reflectance():
    """C1：植被指数公式对负反射率像元（nir+red<=0）必须返回 0，而非伪值。

    直接测试 app.services.rs_service 中导出的 INDEX_FORMULAS 契约，
    验证生产公式的 mask 语义：分母 <=0 的像元取 out 数组的 0。
    """
    import numpy as np
    from app.services.rs.band_math import INDEX_FORMULAS

    _, ndvi_formula = INDEX_FORMULAS["ndvi"]

    # Sentinel-2 L2A：水面/阴影像元反射率可能为负 -> nir+red <= 0
    red = np.array([1000.0, -500.0, 0.0, -100.0])
    nir = np.array([3000.0, -800.0, -100.0, -200.0])

    result = ndvi_formula(red, nir)
    # nir+red <= 0 的位置（下标 1/2/3）必须 mask 为 0
    assert result[1] == 0.0, f"负分母像元应被 mask 为 0，实际 {result[1]}"
    assert result[2] == 0.0
    assert result[3] == 0.0
    # 正常像元（nir+red>0，下标 0）应是真实 NDVI 值
    np.testing.assert_allclose(result[0], (3000 - 1000) / (3000 + 1000), rtol=1e-6)
    # 关键回归断言：mask 像元不能是旧 bug 的几千伪值（(nir-r)/1）
    assert abs(result[1]) < 1.0, "负分母像元返回了伪值（旧 np.where(...,1) bug 回归）"



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
    from fastapi import HTTPException

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
    from app.services.session_data_protocol import set_active_session_store

    captured = {}

    async def fake_store(session_id, data, prefix="data"):
        captured["session_id"] = session_id
        captured["prefix"] = prefix
        return f"ref:{prefix}-abc"

    # _store_ref now routes through get_session_store(); inject via the seam
    # (set_active_session_store) rather than patching the old module singleton.
    fake_manager = MagicMock()
    fake_manager.store = fake_store
    set_active_session_store(fake_manager)
    try:
        ref = task_chain._store_ref({"foo": 1}, task_id="task-xyz", prefix="fetch")
    finally:
        set_active_session_store(None)

    assert ref == "ref:fetch-abc"
    # 必须包含 task_id（之前是硬编码 "explorer"）
    assert "task-xyz" in captured["session_id"], (
        f"session_id 应基于 task_id，实际={captured['session_id']}"
    )


def test_c2_load_ref_uses_task_id_namespace(monkeypatch):
    """C2：_load_ref 也必须用 task_id namespace。"""
    from app.tasks.explorer import task_chain
    from app.services.session_data_protocol import set_active_session_store

    captured = {}

    async def fake_get(session_id, ref_id):
        captured["session_id"] = session_id
        return {"data": "ok"}

    fake_manager = MagicMock()
    fake_manager.get = fake_get
    set_active_session_store(fake_manager)
    try:
        result = task_chain._load_ref("ref:fetch-abc", task_id="task-123")
    finally:
        set_active_session_store(None)

    assert result == {"data": "ok"}
    assert "task-123" in captured["session_id"]


# ── C3: Redis 错误隔离 ─────────────────────────────────────────────────


def _make_manager_with_failing_redis(monkeypatch, fail_method: str):
    """构造一个 RedisSessionDataManager，其 self._r 在指定方法上抛 RedisError。

    绕过 __init__ 的 from_url（需要真 URL）—— 直接 setattr 替换 _r。
    """
    import asyncio
    import redis.asyncio as aioredis
    from app.services.session_data_redis import RedisSessionDataManager

    # 不调用 __init__（避免 from_url），仅设置必要属性
    manager = RedisSessionDataManager.__new__(RedisSessionDataManager)
    manager.capacity = 100
    # _ensure_connected() 现在读 _injected_redis（测试注入字段），__new__ 跳过
    # __init__ 时需要手动置 None，否则 AttributeError。
    manager._injected_redis = None
    # L1 缓存字段（Phase 7）：write 成功路径会调 _l1_invalidate_session，
    # __new__ 绕过 __init__ 时需手动初始化，否则 AttributeError。
    manager._l1 = {}
    manager._l1_order = []
    # 审计 TEST-13：_ensure_connected() 引用 _bound_loop（用 __new__ 跳过 __init__
    # 时不会设置）。本 helper 只在 async 测试里调用，把 _bound_loop 指向当前运行
    # loop 让 _ensure_connected 的复用条件成立，从而保留注入的 _FakeRedis。
    try:
        manager._bound_loop = asyncio.get_running_loop()
    except RuntimeError:
        manager._bound_loop = None

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
# 行为测试：驱动 chat_stream 进入工具派发等待，再模拟客户端断开（GeneratorExit），
# 验证后台 dispatch_task 被显式 cancel，而非泄漏到后台继续跑。


@pytest.mark.asyncio
async def test_c5_dispatch_task_cancelled_on_disconnect(monkeypatch):
    """C5：SSE 客户端断开时 chat_stream 必须 cancel 正在跑的 dispatch_task。

    之前 bug：dispatch_task 没在客户端断开时 cancel，后台继续跑（Celery 派发、
    GeoJSON 序列化、DB 写入）做无用功且无界增长。本测试驱动生成器进入工具派发
    等待，再模拟客户端断开（aclose 触发 GeneratorExit），验证被派发的工具任务
    收到 CancelledError 而非泄漏到后台继续跑。
    """
    import asyncio
    from app.services.chat_engine import ChatEngine
    from app.services.chat import planner as planner_mod
    from app.services.tool_catalog import ToolCatalog
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register("c5_probe", "probe", func=lambda **_: {})
    engine = ChatEngine(reg, tool_catalog=ToolCatalog(reg))

    # 跳过规划
    async def fake_maybe_plan(self, *a, **k):
        return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))

    # Stub the session/DB side effects so chat_stream reaches the dispatch
    # stage without touching the DB / Redis / a real LLM (mirrors the fixture
    # pattern in test_chat_engine_planning.py). Without these the generator
    # hangs in _get_or_create_session and never reaches the tool-dispatch wait.
    async def fake_get_or_create_session(self, session_id, user_id=None):
        return []
    monkeypatch.setattr(engine, "_get_or_create_session",
                        fake_get_or_create_session.__get__(engine, type(engine)))
    monkeypatch.setattr(engine, "_save_msg_async", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_generate_title", AsyncMock(return_value=None))

    # 主 LLM 第一轮返回一个 tool_call，触发 dispatch
    async def fake_llm_stream(*a, **k):
        yield ("done", {"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "type": "function",
                            "function": {"name": "c5_probe", "arguments": "{}"}}],
        }, "finish_reason": "tool_calls"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    # dispatch_tool 阻塞在一个 Event 上 —— 模拟长时间运行的工具。
    # 若 chat_stream 没正确 cancel，这个任务会永远挂着。
    block_event = asyncio.Event()

    from app.services.tool_dispatch_service import ToolDispatchResult
    async def blocking_dispatch(self, *a, **k):
        await block_event.wait()  # 永不 set，除非被 cancel
        # 返回值永不执行（任务被 cancel），但保持契约诚实：返回判别式结果而非旧 dict。
        return ToolDispatchResult(
            status="ok", llm_payload="{}", slim_event={},
            geojson_ref=None, raw_result={}, error_msg=None,
        )
    monkeypatch.setattr(engine, "_dispatch_tool",
                        blocking_dispatch.__get__(engine, type(engine)))

    # 捕获 chat_stream 内部 create_task 创建的 pipeline_task。
    # chat_stream 在派发工具时调用 asyncio.create_task(
    # self.tool_pipeline.execute_tool_call(...))，pipeline 内部经 dispatch_fn
    # （late-bound 到 engine._dispatch_tool）执行工具。我们 patch
    # asyncio.create_task 记录该任务，再 patch asyncio.wait 让它立即返回
    # （不阻塞 5s），这样生成器进入等待后我们能重新拿到控制权去模拟客户端断开。
    real_create_task = asyncio.create_task
    captured_tasks: list[asyncio.Task] = []

    def capturing_create_task(coro, *a, **k):
        t = real_create_task(coro, *a, **k)
        captured_tasks.append(t)
        return t
    monkeypatch.setattr(asyncio, "create_task", capturing_create_task)

    reached_wait = asyncio.Event()

    async def fast_wait(fs, timeout=None):
        # 标记已进入 dispatch 等待，并立即返回（done 为空，模拟工具仍在跑）
        reached_wait.set()
        return set(), set(fs)
    monkeypatch.setattr(asyncio, "wait", fast_wait)

    # 驱动生成器直到进入 dispatch 等待循环
    gen = engine.chat_stream("probe", session_id="sess-C5")
    producer = asyncio.create_task(_drain_until(gen, reached_wait))
    try:
        await asyncio.wait_for(producer, timeout=2.0)
    except asyncio.TimeoutError:
        pass

    dispatch_tasks = [t for t in captured_tasks if not t.done()]
    assert dispatch_tasks, "未捕获到运行中的 dispatch_task（生成器未到达派发阶段）"
    dispatch_task = dispatch_tasks[0]

    # 模拟客户端断开：aclose 让生成器在当前挂起点收到 GeneratorExit，
    # 触发 except (CancelledError, GeneratorExit) -> dispatch_task.cancel()
    await gen.aclose()
    # 让 cancel 传播到阻塞的 dispatch 协程
    await asyncio.sleep(0.05)

    # 核心断言：dispatch_task 被显式 cancel。旧 bug 下不会被 cancel，
    # 任务永远挂在 block_event.wait() 上（done() 为 False 且不取消）。
    assert dispatch_task.cancelled(), (
        "SSE 客户端断开后 dispatch_task 未被 cancel —— 后台任务会泄漏继续跑"
    )
    # 清理：取消可能残留的任务，避免 pytest-asyncio 报警告
    block_event.set()
    for t in captured_tasks:
        if not t.done():
            t.cancel()
    planner_mod.clear_plan("sess-C5")


async def _drain_until(gen, signal_event: asyncio.Event):
    """排空生成器直到 signal_event 被 set（即进入 dispatch 等待）。"""
    async for _ev in gen:
        if signal_event.is_set():
            break
