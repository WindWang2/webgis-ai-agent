"""Analysis Reuse（V2 P10）单元测试 —— 键确定性 + 复用规则逐条落地。

对应任务 §28/§29/§30 与 Scenario G/H/I：
- G（same analysis twice）→ 第二次命中，不重算；
- H（changed parameter）→ cache miss；
- I（input ref 变化 / 不同输入 ref）→ miss；
- 失败产物不注册 → 天然不可复用；
- 状态/时效/descriptor 存活门：superseded、expired（探测缺失）、超龄 → miss；
- 输入形状指纹守卫：输入 ref descriptor 变化 → miss（in-place overwrite 防御）；
- 键对 ref 内容寻址的边界：键 = ref id + 参数，inline 与 ref 形态键不同（记录在案）。
"""
import time
import uuid

import pytest

from app.lib.gis.analysis_reuse import (
    ANALYSIS_REUSE_MAX_AGE_S,
    compute_analysis_key,
    find_reusable_artifact,
    snapshot_input_shapes,
)
from app.services.artifact_registry import (
    A_SUPERSEDED,
    get_artifact,
    list_artifacts,
    register_tool_artifact,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"reuse-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _fc(n=5):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }


async def _produce(sid: str, *, tool: str, args: dict, n: int = 5) -> str:
    """模拟一次成功分析：存输入 ref + 存产物 ref + 带指纹注册。"""
    input_ref = await session_data_manager.store(sid, _fc(n), prefix="geojson")
    out_ref = await session_data_manager.store(
        sid, _fc(n + 1000), prefix="geojson"
    )
    args = dict(args)
    args["geojson"] = input_ref
    key = compute_analysis_key(tool, args)
    shapes = await snapshot_input_shapes(sid, args)
    await register_tool_artifact(
        sid, out_ref, tool=tool,
        analysis_key=key, input_shapes=shapes,
    )
    return out_ref


# ── 键确定性 ───────────────────────────────────────────────────────────

def test_key_deterministic_and_param_sensitive():
    a1 = compute_analysis_key("h3_binning", {"geojson": "ref:geojson-a", "resolution": 9})
    a2 = compute_analysis_key("h3_binning", {"geojson": "ref:geojson-a", "resolution": 9})
    assert a1 == a2
    # 参数变化 → 键变（Scenario H 前提）
    assert compute_analysis_key("h3_binning", {"geojson": "ref:geojson-a", "resolution": 8}) != a1
    # 输入 ref 变化 → 键变（Scenario I 前提）
    assert compute_analysis_key("h3_binning", {"geojson": "ref:geojson-b", "resolution": 9}) != a1
    # 工具变化 → 键变
    assert compute_analysis_key("kde_contours", {"geojson": "ref:geojson-a"}) != \
        compute_analysis_key("buffer_analysis", {"geojson": "ref:geojson-a"})


def test_key_unkeyable_inputs():
    assert compute_analysis_key("", {"a": 1}) is None
    # 不可 JSON 序列化
    assert compute_analysis_key("t", {"f": object()}) is None


# ── Scenario G：same analysis twice → 命中 ─────────────────────────────

async def test_reuse_hit_same_tool_args(clean_session):
    sid = clean_session
    out1 = await _produce(sid, tool="h3_binning", args={"resolution": 9})
    # 重建与生产时完全一致的 args（ref 回填）
    records = {r.artifact_id: r for r in await list_artifacts(sid)}
    shapes_of_output = next(
        r.metadata["input_shapes"] for r in records.values()
        if r.metadata.get("input_shapes")
    )
    input_ref = next(iter(shapes_of_output))
    args = {"resolution": 9, "geojson": input_ref}
    key = compute_analysis_key("h3_binning", args)
    shapes = await snapshot_input_shapes(sid, args)
    hit = await find_reusable_artifact(sid, analysis_key=key, input_shapes=shapes)
    assert hit is not None
    assert hit["artifact_id"] == out1
    assert hit["feature_count"] is not None


# ── Scenario H/I：miss 路径 ────────────────────────────────────────────

async def test_reuse_miss_on_param_change(clean_session):
    sid = clean_session
    await _produce(sid, tool="buffer_analysis", args={"distance": 100})
    # buffer 100m → 200m：键不同，找不到该 key 的记录
    hit = await find_reusable_artifact(
        sid, analysis_key=compute_analysis_key("buffer_analysis", {"distance": 200}),
        input_shapes={},
    )
    assert hit is None


async def test_reuse_miss_on_input_shape_change(clean_session):
    """输入 ref 被 in-place 覆写（形状真变）→ 与生产时记录不符 → 拒绝复用。

    #1720：旧守卫是恒真的 —— 期望值取自调用方传入的「复核时刻现状快照」，
    与刚读到的当前 descriptor 比对永远相等，调用前发生的覆写发现不了。修正后
    期望值取自生产时录入的 metadata["input_shapes"]，与当前 descriptor 比对。
    这里用真实 overwrite 改形状（descriptor 随覆写重算，#1113）→ 失配 → miss。"""
    sid = clean_session
    input_ref = await session_data_manager.store(sid, _fc(5), prefix="geojson")
    out_ref = await session_data_manager.store(sid, _fc(9), prefix="geojson")
    args = {"geojson": input_ref}
    key = compute_analysis_key("h3_binning", args)
    shapes = await snapshot_input_shapes(sid, args)
    await register_tool_artifact(
        sid, out_ref, tool="h3_binning", analysis_key=key, input_shapes=shapes
    )
    # 复核前输入被覆写：5 点 → 50 点（形状真的变了；调用方快照也照实反映）
    await session_data_manager.overwrite(sid, input_ref, _fc(50))
    hit = await find_reusable_artifact(
        sid,
        analysis_key=key,
        input_shapes=await snapshot_input_shapes(sid, args),
    )
    assert hit is None  # 输入变 → 不可复用


async def test_reuse_shape_guard_reads_recorded_not_caller(clean_session):
    """#1720：形状期望值取自生产时录入，不取自调用方现状快照。

    恒真守卫的判据：调用方声称的形状与生产时记录、当前 descriptor 都不一致
    时（生产上不可能 —— 快照本就派生自 descriptor），判定只由
    「生产时记录 vs 当前 descriptor」决定。此处记录与现状一致（5 点）→ 命中；
    旧逻辑会用调用方的 50 点口径误判 miss。"""
    sid = clean_session
    input_ref = await session_data_manager.store(sid, _fc(5), prefix="geojson")
    out_ref = await session_data_manager.store(sid, _fc(9), prefix="geojson")
    args = {"geojson": input_ref}
    key = compute_analysis_key("h3_binning", args)
    shapes = await snapshot_input_shapes(sid, args)
    await register_tool_artifact(
        sid, out_ref, tool="h3_binning", analysis_key=key, input_shapes=shapes
    )
    hit = await find_reusable_artifact(
        sid,
        analysis_key=key,
        input_shapes={input_ref: {"feature_count": 50, "geometry_types": ["Point"]}},
    )
    assert hit is not None  # 记录与现状一致 → 可复用（调用方口径不是证据）
    assert hit["artifact_id"] == out_ref


async def test_reuse_miss_on_unknown_input_shape(clean_session):
    """生产时输入形状未知（descriptor 缺失）→ 不参与复核也不放行。"""
    sid = clean_session
    out_ref = await session_data_manager.store(sid, _fc(9), prefix="geojson")
    key = compute_analysis_key("h3_binning", {"geojson": "ref:geojson-ghost"})
    await register_tool_artifact(
        sid, out_ref, tool="h3_binning",
        analysis_key=key, input_shapes={"ref:geojson-ghost": {"feature_count": 5, "geometry_types": ["Point"]}},
    )
    # 复核时 ghost ref descriptor 拿不到 → miss
    hit = await find_reusable_artifact(
        sid, analysis_key=key,
        input_shapes={"ref:geojson-ghost": {"feature_count": 5, "geometry_types": ["Point"]}},
    )
    assert hit is None


# ── 状态与时效门 ───────────────────────────────────────────────────────

async def test_reuse_miss_when_superseded(clean_session):
    sid = clean_session
    out1 = await _produce(sid, tool="h3_binning", args={"resolution": 9})
    rec = await get_artifact(sid, out1)
    rec.status = A_SUPERSEDED
    # 直接经记录层写回状态（list_artifacts 每次重载，需持久化）
    from app.services.artifact_registry import _load_records, _save_records

    records = await _load_records(sid, session_data_manager)
    records[out1].status = A_SUPERSEDED
    await _save_records(sid, records, session_data_manager)
    args = await _args_for(sid, out1, "h3_binning")
    hit = await find_reusable_artifact(
        sid, analysis_key=compute_analysis_key("h3_binning", args),
        input_shapes=await snapshot_input_shapes(sid, args),
    )
    assert hit is None


async def test_reuse_miss_when_output_expired(clean_session):
    sid = clean_session
    out1 = await _produce(sid, tool="h3_binning", args={"resolution": 9})
    await session_data_manager.delete_ref(sid, out1)
    args = await _args_for(sid, out1, "h3_binning")
    hit = await find_reusable_artifact(
        sid, analysis_key=compute_analysis_key("h3_binning", args),
        input_shapes=await snapshot_input_shapes(sid, args),
    )
    assert hit is None


async def test_reuse_miss_when_too_old(clean_session):
    sid = clean_session
    out1 = await _produce(sid, tool="h3_binning", args={"resolution": 9})
    args = await _args_for(sid, out1, "h3_binning")
    hit = await find_reusable_artifact(
        sid, analysis_key=compute_analysis_key("h3_binning", args),
        input_shapes=await snapshot_input_shapes(sid, args),
        now=time.time() + ANALYSIS_REUSE_MAX_AGE_S + 60,
    )
    assert hit is None


async def test_reuse_none_without_key(clean_session):
    assert await find_reusable_artifact(clean_session, analysis_key=None, input_shapes=None) is None


# ── 失败产物不可复用 ───────────────────────────────────────────────────

async def test_failed_run_never_registered(clean_session):
    """失败调用不注册产物（dispatch 只在成功路径登记）→ 无记录可复用。

    _produce 模拟一次成功分析：输入 ref 存 store 但**不**注册（输入不是
    产物），只有产物 ref 注册 —— 账本里恰好 1 条记录。失败尝试连 register
    调用都没有，账本不会出现失败记录。"""
    sid = clean_session
    await _produce(sid, tool="h3_binning", args={"resolution": 9})
    records = await list_artifacts(sid)
    assert len(records) == 1  # 仅产物 ref（输入 ref 不入账本）


# ── 工具函数 ───────────────────────────────────────────────────────────

async def _args_for(sid: str, out_ref: str, tool: str) -> dict:
    """从产物记录的 input_shapes 还原 args（测试辅助）。"""
    rec = await get_artifact(sid, out_ref)
    input_ref = next(iter(rec.metadata["input_shapes"].keys()))
    return {"geojson": input_ref, "resolution": 9} if tool == "h3_binning" else {"geojson": input_ref}
