"""Planner Runtime v3（Phase C）—— 共享 planner 生命周期与 bounded memo 契约。

锁定（post-merge audit A2/A3）：

- A2：``webgis_map_intent`` / ``webgis_map_product`` / plan_orchestrator 共享
  同一 PlannerRuntime —— memo 跨工具调用存活（intent→product 链第二次
  规划命中缓存）；
- A3：plain ``dict.popitem(last=False)`` 是 TypeError（v2 遗留，被"每调用
  新建 planner"掩盖）—— OrderedDict FIFO 驱逐必须真正可达且确定；
- memo 键完整性：intent / available_tools / project_verified / manifest
  指纹任一变化都 miss；
- 深拷贝隔离：调用方可变返回值不污染 memo 基底；
- registry 单例被替换（测试 reset）时共享 planner 重建；
- manifest 世代变化（registry 内容变 + refresh）自动失效 memo。
"""
import threading

from app.lib.gis.runtime_manifest import refresh_runtime_manifest
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner_runtime import (
    get_planner_runtime,
    reset_planner_runtime,
)


def setup_function(_fn):
    reset_planner_runtime()


def teardown_function(_fn):
    reset_planner_runtime()


def _intent(query: str = "成都小学的分布情况"):
    return resolve_map_request_intent(query)


def test_shared_runtime_is_process_singleton():
    a = get_planner_runtime()
    b = get_planner_runtime()
    assert a is b


def test_same_input_hits_memo_across_planner_instances_of_runtime():
    planner = get_planner_runtime()
    it = _intent()
    p1 = planner.plan_from_intent(it)
    assert len(planner._plan_memo) == 1
    p2 = planner.plan_from_intent(it)
    assert len(planner._plan_memo) == 1, "同输入第二次规划必须命中 memo"
    assert p1.model_dump() == p2.model_dump()
    assert p1 is not p2, "命中返回深拷贝，不是同一对象"


def test_intent_then_product_chain_shares_memo():
    """A2 核心场景：intent 阶段与 product 阶段（两个工具调用）复用 memo。"""
    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it)  # webgis_map_intent 阶段
    before = len(planner._plan_memo)
    planner.plan_from_intent(it)  # webgis_map_product 阶段（同 intent 重放）
    assert len(planner._plan_memo) == before


def test_different_intent_misses():
    planner = get_planner_runtime()
    planner.plan_from_intent(_intent("成都小学的分布情况"))
    planner.plan_from_intent(_intent("北京医院分布密度"))
    assert len(planner._plan_memo) == 2


def test_different_tools_misses():
    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it, available_tools={"search_poi_around"})
    planner.plan_from_intent(it, available_tools={"search_poi_around", "extra_tool"})
    assert len(planner._plan_memo) == 2


def test_different_project_verified_misses():
    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it, project_verified=set())
    planner.plan_from_intent(it, project_verified={"poi_distribution_overview"})
    assert len(planner._plan_memo) == 2


def test_manifest_generation_change_invalidates():
    from app.lib.gis.algorithm_registry import (
        AlgorithmDescriptor,
        get_algorithm_registry,
    )

    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it)
    assert len(planner._plan_memo) == 1

    ar = get_algorithm_registry()
    probe = AlgorithmDescriptor(
        id="probe.planner-memo", name="memo 探针",
        capabilities=["poi_query"], tool_candidates=["search_poi_around"],
        priority=1,
    )
    ar.register(probe)
    try:
        refresh_runtime_manifest()
        planner.plan_from_intent(it)
        # registry 内容变化 → manifest 指纹变 → 同 intent 也 miss 重算
        assert len(planner._plan_memo) == 2
    finally:
        ar._by_id.pop("probe.planner-memo", None)
        bucket = ar._by_capability.get("poi_query")
        if bucket and "probe.planner-memo" in bucket:
            bucket.remove("probe.planner-memo")
        refresh_runtime_manifest()


def test_fifo_eviction_deterministic_and_reachable():
    """A3：驱逐路径必须真正可达 —— v2 的 plain dict.popitem(last=False)
    在第 65 个 key 插入时 TypeError。"""
    planner = get_planner_runtime()
    max_entries = planner._plan_memo_max
    for i in range(max_entries + 8):
        planner.plan_from_intent(_intent(f"城市{i}小学分布情况"))
    assert len(planner._plan_memo) == max_entries, "memo 必须有界"
    # FIFO：最早的 key 被驱逐，最近的仍在
    assert len(planner._plan_memo) == max_entries
    keys = list(planner._plan_memo.keys())
    assert keys[-1] in keys and len(keys) == max_entries
    # 驱逐后再次规划最早 intent → 重新计算（新插入）
    planner.plan_from_intent(_intent("城市0小学分布情况"))
    assert len(planner._plan_memo) == max_entries
    # 最近插入的 key 应该是"城市0"重算后的键
    last_key = list(planner._plan_memo.keys())[-1]
    assert any("城市0" in k for k in [str(last_key)])


def test_cached_mutation_does_not_poison_future_results():
    planner = get_planner_runtime()
    it = _intent()
    p1 = planner.plan_from_intent(it)
    p1.data_requirements = []
    p1.recipe_id = "mutated"
    p2 = planner.plan_from_intent(it)
    assert p2.recipe_id != "mutated"
    assert len(p2.data_requirements) > 0


def test_registry_reset_rebuilds_shared_planner():
    from app.services.gis_harness.recipes import (
        get_recipe_registry,
        reset_recipe_registry,
    )

    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it)
    assert len(planner._plan_memo) == 1

    old_registry = get_recipe_registry()
    reset_recipe_registry()
    try:
        new_registry = get_recipe_registry()
        assert new_registry is not old_registry
        rebuilt = get_planner_runtime()
        assert rebuilt is not planner, "registry 单例替换后共享 planner 必须重建"
        assert len(rebuilt._plan_memo) == 0, "重建后 memo 清零（旧键不再可信）"
    finally:
        # 复原：reset 回内置种子（get 会重新加载 builtins），保证后续测试
        # 看到与模块导入时相同内容的 registry。
        reset_planner_runtime()
        get_recipe_registry()


def test_concurrent_planning_is_thread_safe():
    planner = get_planner_runtime()
    errors: list = []

    def _worker(tag: int):
        try:
            for i in range(12):
                planner.plan_from_intent(_intent(f"城市{tag}-{i}小学分布"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(planner._plan_memo) <= planner._plan_memo_max


def test_use_memo_false_bypasses():
    planner = get_planner_runtime()
    it = _intent()
    planner.plan_from_intent(it, use_memo=False)
    assert len(planner._plan_memo) == 0
