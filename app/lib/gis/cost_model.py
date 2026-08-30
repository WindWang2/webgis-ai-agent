"""Cost-aware Algorithm Resolution 的确定性成本模型（ADR-0083）。

规则式 estimator（刻意不用 ML）：每个阈值都有代码库出处，不拍脑袋 ——

- ``HEATMAP_MIN_POINTS = 10``：app/core/config.py 默认值；heatmap_data
  工具的定量护栏（点数过少热力图无统计意义，app/tools/spatial.py）。
- ``INTERACTIVE_FEATURE_CAP = 5_000``：PiToolResponse.details 的
  ~1MiB/回调 载荷上限（tool_dispatch_service.py #798 注释）—— 交互
  回调通道的点数边界。
- ``FETCH_FEATURE_CAP = 20_000``：frontend/lib/mapspec/ref-source-resolver.ts
  的同名常量 —— 前端拒绝挂载超过该点数的 ref，即**原生渲染通道的硬
  上限**；超过即应切换聚合/服务端通道。
- ``DATA_FABRIC_MAX_FEATURES = 50_000``：app/core/config.py —— 数据通道
  保护上限（大规模强制服务端处理）。

ExecutionPolicy（自动推断，用户/LLM 不选）：

    interactive_fast   小数据交互轮次 —— 时延权重最高
    balanced           中等规模默认
    analysis_quality   定量输出能力（deterministic/统计族）—— 精度优先
    export_quality     导出语境 —— 一次性成本可接受、精度仍优先
    large_data         超过前端渲染上限 —— 内存权重最高、偏好服务端卸载

成本分 = Σ(级别 × 策略权重) + 近似惩罚 − 服务端卸载加成。
级别：low=1 / medium=3 / high=9（CostLevel 的序数化）。
打分是 descriptor 声明（cpu/memory/io_cost、approximate、transport）的
纯函数 —— 确定性、可测试、可解释（breakdown 进 evidence）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# ── 规模阈值（出处见模块 docstring）──────────────────────────────────
HEATMAP_MIN_POINTS = 10
INTERACTIVE_FEATURE_CAP = 5_000
FETCH_FEATURE_CAP = 20_000
DATA_FABRIC_MAX_FEATURES = 50_000

ExecutionPolicy = str
EXECUTION_POLICIES: Tuple[str, ...] = (
    "interactive_fast",
    "balanced",
    "analysis_quality",
    "export_quality",
    "large_data",
)

# 策略 → 成本权重（cpu/memory/io：级别乘数；approx：近似算法惩罚；
# server：ASYNC/CELERY 传输（服务端/后台卸载）的减分）。
_POLICY_WEIGHTS: Dict[str, Dict[str, int]] = {
    "interactive_fast": {"cpu": 3, "memory": 1, "io": 4, "approx": 0, "server": 0},
    "balanced": {"cpu": 2, "memory": 2, "io": 2, "approx": 1, "server": 0},
    "analysis_quality": {"cpu": 1, "memory": 2, "io": 1, "approx": 4, "server": 0},
    "export_quality": {"cpu": 1, "memory": 2, "io": 0, "approx": 2, "server": 0},
    "large_data": {"cpu": 2, "memory": 4, "io": 2, "approx": 0, "server": 3},
}

_LEVEL: Dict[str, int] = {"low": 1, "medium": 3, "high": 9}
_SERVER_TRANSPORTS = ("ASYNC", "CELERY")


def infer_execution_policy(
    *,
    feature_count: Optional[int] = None,
    export: bool = False,
    deterministic_output: bool = False,
    policy_hint: str = "",
) -> ExecutionPolicy:
    """自动推断执行策略（显式 hint 优先 —— 未来由 planner 语境传入）。

    判定序（先到先得，均可解释）：
    1. 显式 hint（合法值直通）；
    2. 导出语境 → export_quality；
    3. feature_count 超过前端渲染上限（FETCH_FEATURE_CAP）→ large_data；
    4. 定量输出能力（deterministic）→ analysis_quality；
    5. 小数据（≤ INTERACTIVE_FEATURE_CAP）→ interactive_fast；
    6. 其余 → balanced。
    """
    if policy_hint in EXECUTION_POLICIES:
        return policy_hint
    if export:
        return "export_quality"
    if feature_count is not None and feature_count > FETCH_FEATURE_CAP:
        return "large_data"
    if deterministic_output:
        return "analysis_quality"
    if feature_count is not None and feature_count <= INTERACTIVE_FEATURE_CAP:
        return "interactive_fast"
    return "balanced"


def score_algorithm(
    algo: Any,
    *,
    policy: ExecutionPolicy = "balanced",
) -> Tuple[int, str]:
    """策略加权的成本分（越低越好）+ 可解释 breakdown（进 evidence）。

    纯函数：只读 descriptor 声明字段（cpu/memory/io_cost、approximate、
    preferred_execution_policy）。级别缺失按 medium 处理（保守）。
    """
    w = _POLICY_WEIGHTS.get(policy, _POLICY_WEIGHTS["balanced"])
    cpu = _LEVEL.get(getattr(algo, "cpu_cost", "medium"), 3) * w["cpu"]
    mem = _LEVEL.get(getattr(algo, "memory_cost", "medium"), 3) * w["memory"]
    io = _LEVEL.get(getattr(algo, "io_cost", "medium"), 3) * w["io"]
    approx = w["approx"] if getattr(algo, "approximate", False) else 0
    transport = str(getattr(algo, "preferred_execution_policy", "") or "")
    server = w["server"] if transport in _SERVER_TRANSPORTS else 0
    score = max(0, cpu + mem + io + approx - server)
    breakdown = (
        f"cpu={cpu},mem={mem},io={io}"
        + (f",approx={approx}" if approx else "")
        + (f",server=-{server}" if server else "")
    )
    return score, breakdown


def scale_tier(feature_count: Optional[int]) -> str:
    """规模分层（诊断/测试用；阈值同上）。"""
    if feature_count is None:
        return "unknown"
    if feature_count < HEATMAP_MIN_POINTS:
        return "insufficient"
    if feature_count <= INTERACTIVE_FEATURE_CAP:
        return "interactive"
    if feature_count <= FETCH_FEATURE_CAP:
        return "renderable"
    if feature_count <= DATA_FABRIC_MAX_FEATURES:
        return "aggregate"
    return "server_side"


# ── 运行策略词表（ADR-0088 P6：跨前后端单一 contract）──────────────────
#
# resolver 的裁决（算法/能力级 fallback）已经隐式选择了通道；本词表把
# 「规模 → 通道」的映射显式化为可测试/可披露的纯函数。每个值都有真实
# 代码参照（不虚构数据通道）：
#
#   frontend_native   ref 源内联挂载（ref-source-resolver，≤FETCH_CAP）
#   preaggregated     聚合算法通道（grid_binning/fishnet/h3，≤DATA_FABRIC）
#   server_vector     服务端要素处理（ASYNC/CELERY 传输的大要素集）
#   server_raster     栅格渲染通道（density.visual.heatmap → heatmap_data）
#   vector_tile       HUD 大层 MVT 通道（前端瓦片渲染）
#   raster_tile       服务端栅格瓦片（底图/影像层）
RUNTIME_STRATEGIES: Tuple[str, ...] = (
    "frontend_native",
    "preaggregated",
    "server_vector",
    "server_raster",
    "vector_tile",
    "raster_tile",
)


def resolve_runtime_strategy(
    *,
    feature_count: Optional[int] = None,
    artifact_type: str = "",
) -> str:
    """规模/artifact 语义 → 运行策略（确定性、有出处；诊断/披露/测试用）。

    与 ``infer_execution_policy`` 的阈值同源（FETCH_CAP / DATA_FABRIC），
    但回答的是「渲染/数据通道」而非「成本策略」—— 两者互补，不合并
    （合并会迫使一边表达两套语义）。栅格类 artifact 无论规模都走栅格
    通道（与 heatmap 通道的现状一致）。
    """
    if artifact_type in ("raster_surface", "terrain_surface", "remote_sensing_index"):
        return "server_raster"
    if feature_count is None:
        return "frontend_native"
    if feature_count <= FETCH_FEATURE_CAP:
        return "frontend_native"
    if feature_count <= DATA_FABRIC_MAX_FEATURES:
        return "preaggregated"
    return "server_vector"
