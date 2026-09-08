"""ADR-0101 Wave 9: 可靠性语料 + 安全强化 + 大注册表性能。

- 语料执行：app/evaluation/reliability_corpus.py 的 19 类场景经
  simulate_agent_loop 跑在真实 registry 上（无 LLM/无网络）；
- 安全：tier-3 别名绕过、Pi/子代理/重放红线、NaN/Infinity 注入拒绝；
- 性能：200/500/1000 合成描述符下的注册/清单/检索/指纹/校验有界性。
"""
import time

import pytest

from app.evaluation.reliability_corpus import build_reliability_corpus
from app.services.subagent_roles import BudgetExceeded
from app.evaluation.replay import simulate_agent_loop
from app.tools.argument_normalization import TOOL_NAME_ALIASES
from app.tools.descriptor import SideEffectClass
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 语料测试注册表（工具语义与语料场景一一对应）
# ---------------------------------------------------------------------------

@pytest.fixture()
def corpus_registry():
    registry = ToolRegistry()

    def echo(v: int = 0, ref: str = "", geojson: object = None) -> dict:
        return {"success": True, "value": v}

    def make_data(name: str) -> dict:
        return {"success": True, "ref": f"ref:{name}"}

    def big(n: int = 100) -> dict:
        return {"success": True, "features": [{"i": i} for i in range(n)]}

    def boom(p: int = 0) -> dict:
        return {"success": False, "error": "boom"}

    def scan(q: str) -> dict:
        return {"success": True, "hits": 3}

    def wipe(confirm: bool = False) -> dict:
        return {"success": True, "wiped": True}

    def mutate(id: str = "") -> dict:
        return {"success": True, "mutated": id}

    def make_artifact(name: str) -> dict:
        return {"success": True, "artifact_id": name}

    def take_list(items: list) -> dict:
        return {"success": True, "count": len(items)}

    def kebab(radius_px: int = 0) -> dict:
        return {"success": True, "r": radius_px}

    def take_float(x: float = 0.0) -> dict:
        return {"success": True, "x": x}

    for name, fn, kw in [
        ("echo", echo, {"side_effect": "cacheable_read"}),
        ("make_data", make_data, {"side_effect": "state_mutation"}),
        ("big", big, {"cost": "heavy"}),
        ("boom", boom, {}),
        ("scan", scan, {"side_effect": "cacheable_read"}),
        ("wipe", wipe, {"tier": 3, "side_effect": "destructive"}),
        ("mutate", mutate, {"side_effect": "state_mutation"}),
        ("make_artifact", make_artifact, {"side_effect": "artifact_creation"}),
        ("take_list", take_list, {}),
        ("kebab", kebab, {}),
        ("take_float", take_float, {}),
    ]:
        registry.register(name=name, description=name, func=fn, **kw)
    return registry


# ---------------------------------------------------------------------------
# 语料全量执行
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reliability_corpus_full_run(corpus_registry):
    # 语料中的别名-绕过场景依赖 wipe→wipe_alias 别名（生产中该表导入期固定）
    TOOL_NAME_ALIASES["wipe_alias"] = "wipe"
    try:
        corpus = build_reliability_corpus()
        assert len(corpus) >= 19
        failures = []
        for case in corpus:
            report = await simulate_agent_loop(
                corpus_registry, "sess-1", case.script
            )
            if not report.invariants_held:
                failures.append((case.case_id, report.violations))
        assert failures == [], f"corpus failures: {failures}"
    finally:
        TOOL_NAME_ALIASES.pop("wipe_alias", None)


@pytest.mark.asyncio
async def test_corpus_deterministic_replay(corpus_registry):
    """同一语料重跑 → 结果完全一致（确定性门）。"""
    corpus = build_reliability_corpus()
    r1 = [await simulate_agent_loop(corpus_registry, "s", c.script) for c in corpus[:5]]
    r2 = [await simulate_agent_loop(corpus_registry, "s", c.script) for c in corpus[:5]]
    assert [x.as_dict() for x in r1] == [x.as_dict() for x in r2]


def test_corpus_categories_covered():
    categories = {c.category for c in build_reliability_corpus()}
    required = {
        "single_tool_success", "multi_tool_success", "dependent_tools",
        "invalid_args_repair", "alias_normalization", "missing_ref",
        "large_result", "tool_failure", "repeated_failure", "cancellation",
        "destructive_confirmation", "map_mutation", "artifact_production",
        "payload_security",
    }
    assert required <= categories


# ---------------------------------------------------------------------------
# 安全强化（§38/§39/§40）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_cannot_bypass_tier3_gate_async(corpus_registry):
    TOOL_NAME_ALIASES["wipe_alias"] = "wipe"
    try:
        result = await corpus_registry.dispatch("wipe_alias", {"confirm": True})
        assert result.get("code") == "TIER3_CONFIRMATION_REQUIRED"
    finally:
        TOOL_NAME_ALIASES.pop("wipe_alias", None)


@pytest.mark.asyncio
async def test_nan_injection_rejected(corpus_registry):
    result = await corpus_registry.dispatch("take_float", {"x": float("nan")})
    assert result.get("code") == "VALIDATION_ERROR"
    assert "NaN" in result.get("message", "")


@pytest.mark.asyncio
async def test_infinity_injection_rejected(corpus_registry):
    result = await corpus_registry.dispatch("take_float", {"x": float("inf")})
    assert result.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_finite_float_still_accepted(corpus_registry):
    result = await corpus_registry.dispatch("take_float", {"x": 1.5})
    assert result.get("success") is True


def test_side_effect_surface_model():
    """副作用类驱动的安全面（replay/retry 决策的词汇层）。"""
    from app.tools.descriptor import ToolDescriptor

    d = ToolDescriptor(name="t", tier=3)
    assert d.replay_safe is False and d.retry_safe is False
    d2 = ToolDescriptor(name="t", side_effect=SideEffectClass.CACHEABLE_READ)
    assert d2.replay_safe is True and d2.retry_safe is True


def test_subagent_and_replay_red_lines_remain():
    """子代理目录 + replay 的破坏性红线回归锁（跨 wave 不变式）。"""
    from app.services.subagent import select_tools_for_subagent

    reg = ToolRegistry()
    reg.register(name="w", description="wipe", func=lambda c: {"success": True},
                 tier=3, side_effect="destructive")
    reg.register(name="safe", description="safe", func=lambda: {"success": True})
    subset = select_tools_for_subagent(reg, extra_tools=["w"])
    assert {s["function"]["name"] for s in subset} == {"safe"}


# ---------------------------------------------------------------------------
# 大注册表可扩展性（§44：200/500/1000 合成描述符）
# ---------------------------------------------------------------------------

def _build_synthetic_registry(n: int) -> ToolRegistry:
    reg = ToolRegistry()
    for i in range(n):
        def fn(x: int = 0, q: str = "") -> dict:
            return {"success": True}

        reg.register(
            name=f"synthetic_tool_{i:05d}",
            description=f"合成工具 {i}：用于大注册表可扩展性压测的关键词检索负载",
            func=fn,
            tier=1 if i % 3 == 0 else 2,
            domains=[f"dom{i % 7}"],
            tags=[f"tag{i % 13}"],
            capabilities=[f"cap.family{i % 5}"],
        )
    return reg


@pytest.mark.parametrize("size", [200, 500, 1000])
def test_large_registry_scalability(size):
    from app.services.chat.tool_retrieval import rank_tools
    from app.tools.policy_audit import audit_registry_policies, error_findings

    t0 = time.perf_counter()
    reg = _build_synthetic_registry(size)
    register_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    schemas = reg.get_schemas()
    manifest_s = time.perf_counter() - t0
    assert len(schemas) == size

    t0 = time.perf_counter()
    fp1 = reg.registry_fingerprint()
    fingerprint_s = time.perf_counter() - t0
    assert fp1

    t0 = time.perf_counter()
    hits = rank_tools(reg, "合成工具 关键词 检索 负载", top_k=8)
    retrieval_s = time.perf_counter() - t0
    assert hits  # 命中非空
    t1 = time.perf_counter()
    rank_tools(reg, "第二次 检索 走缓存索引", top_k=8)
    retrieval_cached_s = time.perf_counter() - t1

    t0 = time.perf_counter()
    assert error_findings(audit_registry_policies(reg)) == []
    audit_s = time.perf_counter() - t0

    # 松散的有界断言（CI 噪音友好；真正的回归由 baselines 体系盯）。
    # 1000 工具预算按 2 核 CI runner + coverage 插桩校准：首轮检索实测
    # 1.66s（首轮含索引构建；预算防的是 ~1000× 量级回归，3s 仍有 1800×
    # 裕量捕获真回归）。
    budget = {
        200: (2.0, 0.2, 0.2, 1.0, 1.0),
        500: (4.0, 0.3, 0.3, 1.5, 1.5),
        1000: (8.0, 0.5, 0.5, 3.0, 3.0),
    }[size]
    register_budget, manifest_budget, fp_budget, ret_budget, audit_budget = budget
    assert register_s < register_budget, f"register {register_s:.2f}s"
    assert manifest_s < manifest_budget, f"manifest {manifest_s:.3f}s"
    assert fingerprint_s < fp_budget, f"fingerprint {fingerprint_s:.3f}s"
    assert retrieval_s < ret_budget, f"retrieval {retrieval_s:.3f}s"
    assert audit_s < audit_budget, f"audit {audit_s:.3f}s"
    # 缓存索引后的第二次检索有绝对预算（review R1：防止 ~1000x 回归漏检）
    assert retrieval_cached_s < 0.05, f"cached retrieval {retrieval_cached_s:.3f}s"


def test_surface_augment_hot_path_bounded():
    """review R1 MAJOR 回归锁：augment 每轮运行，compress=none 时绝不重新
    序列化 schema（走 registry 注册期字节缓存）。"""
    from app.services.tool_surface_v2 import SurfaceRequest, ToolSurfaceProjector

    reg = _build_synthetic_registry(200)

    class FullCatalog:
        def __init__(self, reg_):
            self.registry = reg_

        def select_schemas(self, user_message, **kwargs):
            return self.registry.get_schemas()

        @staticmethod
        def detect_domains(text):
            return set()

    proj = ToolSurfaceProjector(reg, FullCatalog(reg))
    t0 = time.perf_counter()
    r = proj.project(SurfaceRequest(user_message="负载 关键词", retrieval=False))
    elapsed = time.perf_counter() - t0
    assert len(r.schemas) == 200
    assert r.bytes_used > 0
    assert elapsed < 0.15, f"augment at 200 tools took {elapsed:.3f}s"


def test_large_registry_fingerprint_invalidation_correct():
    reg = _build_synthetic_registry(300)
    fp1 = reg.registry_fingerprint()
    reg.register(name="late_addition", description="新工具", func=lambda: {"s": True})
    assert reg.registry_fingerprint() != fp1


# ---------------------------------------------------------------------------
# review R2: 直扫器 + 管道预算穿透回归锁
# ---------------------------------------------------------------------------

def test_nonfinite_scanner_deep_and_incomplete_detection():
    from app.tools.registry import _find_nonfinite_numbers

    # 深层 NaN（旧 4096 节点静默截断之下）现在可检出
    # 树规模 ≈ 1 + 5 + 100 + 100 + 5000 + 5000 ≈ 10.4k 次访问：
    # 高于旧 4096 静默截断点（回归意义），低于 40k 扫描预算（不触发 incomplete）
    deep = {"a": [
        {"b": [{"c": [{"d": 0.0} for _ in range(50)]} for _ in range(20)]}
        for _ in range(5)
    ]}
    found, incomplete = _find_nonfinite_numbers(deep)
    assert found == [] and incomplete is False
    deep["a"][-1]["b"][-1]["c"][-1]["d"] = float("nan")
    found2, incomplete2 = _find_nonfinite_numbers(deep)
    assert found2 and incomplete2 is False

    # 预算耗尽 → incomplete=True（绝不静默宣称干净）
    huge = {"k{}".format(i): [float(i)] * 10 for i in range(2000)}
    _, incomplete2 = _find_nonfinite_numbers(huge, max_nodes=100)
    assert incomplete2 is True


@pytest.mark.asyncio
async def test_pipeline_lets_budget_exceeded_escape(corpus_registry):
    """review R2 回归锁：管道对 BudgetExceeded 显式再抛（BaseException）。"""
    from app.services.chat.tool_pipeline import ToolExecutionPipeline

    async def _raise(_tc, _sid, _sent):
        raise BudgetExceeded("tool_calls 41 > 40")

    pipeline = ToolExecutionPipeline(registry=corpus_registry, dispatch_fn=_raise)
    tc = {"id": "c1", "function": {"name": "echo", "arguments": "{}"}}
    with pytest.raises(BudgetExceeded):
        await pipeline.execute_tool_call(tc, "sess-1", task_id=None)
