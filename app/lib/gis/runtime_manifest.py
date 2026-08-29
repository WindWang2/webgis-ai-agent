"""Compiled GIS Runtime Manifest — 各注册表的只读编译投影（audit5 #1084）。

目标不是再造一个 Registry：existing registries 仍是唯一事实源，本模块在
启动/惰性首次访问时把它们 **compile** 成一个不可变的查询面 + 内容指纹：

    ToolRegistry / CapabilityRegistry / AlgorithmRegistry / MapModelRegistry
                              ↓ compile
    GISRuntimeManifest（immutable, fingerprinted, O(1)/O(k) lookups）

消除的既病（audit5 #1076/#1084 已零散修补，此处为体系化收口）：
- 每轮 per-turn 反查重建（tool→capability / capability→tools）；
- 跨注册表 dangling reference 只能靠一次性 audit 脚本发现（现在编译期
  即产出 validation_report）；
- 持久化 plan/evidence 无 registry 语义指纹 —— 部署升级后旧计划对新
  registry 语义静默重放（#1084 的核心诉求：manifest_fingerprint 记录进
  plan，重放时可检测 stale）。

指纹纪律：**内容派生**（sorted ids + versions + 候选表），不含时间戳/
进程标识 —— 同内容必同指纹（测试锁定）。registry 变更（动态注册/测试
reset）经 reset_runtime_manifest() 失效，下一次访问重编译出新指纹。
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_manifest: Optional["GISRuntimeManifest"] = None


@dataclass(frozen=True)
class GISRuntimeManifest:
    """不可变编译产物。字段只读（frozen）；重建 = 重新 compile。"""

    manifest_version: int = 1
    # 内容指纹（sha256[:16]）—— 同 registry 内容必同值。
    fingerprint: str = ""
    # 工具面（来自 ToolRegistry）。
    tool_ids: frozenset = frozenset()
    tool_versions: Dict[str, str] = field(default_factory=dict)
    # 能力↔工具 双向投影（来自 AlgorithmRegistry 的派生视图）。
    tool_to_capabilities: Dict[str, str] = field(default_factory=dict)
    capability_to_tools: Dict[str, List[str]] = field(default_factory=dict)
    # 能力→算法（AlgorithmRegistry.by-capability 索引的编译复制）。
    capability_to_algorithms: Dict[str, List[str]] = field(default_factory=dict)
    # 模型/组件 ids（存在性 + 指纹输入）。
    map_model_ids: frozenset = frozenset()
    map_model_aliases: Dict[str, str] = field(default_factory=dict)
    component_ids: frozenset = frozenset()
    recipe_ids: frozenset = frozenset()
    # 编译期跨注册表一致性报告（空 = 健康）。
    validation_issues: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.validation_issues

    def stale(self, recorded_fingerprint: Optional[str]) -> bool:
        """持久化 plan/evidence 记录的指纹是否已落后于当前 registry 语义。

        空指纹（旧数据未记录）不判 stale —— 检测是增量能力，不追溯否定。
        """
        if not recorded_fingerprint:
            return False
        return recorded_fingerprint != self.fingerprint


def compile_runtime_manifest(tool_registry: Optional[Any] = None) -> GISRuntimeManifest:
    """从各注册表现状编译 manifest（只读输入；不修改任何 registry）。"""
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.cartography.model_library import get_map_model_registry
    from app.lib.cartography.component_registry import get_component_registry
    from app.services.gis_harness.recipes import get_recipe_registry

    caps = get_capability_registry()
    algos = get_algorithm_registry()
    models = get_map_model_registry()
    components = get_component_registry()
    recipes = get_recipe_registry()

    # ── 工具面 ────────────────────────────────────────────────────────
    if tool_registry is None:
        # 工具注册表经 bridge 的 setter 注入（app.tools.registry 无自带
        # 单例访问器）。未注入时按空工具面编译（报告会列出候选悬空）——
        # 生产 lifespan 注入后首访问即以完整工具面重编译。
        from app.agent_pi_bridge import get_tool_registry
        try:
            tool_registry = get_tool_registry()
        except Exception:  # noqa: BLE001 - 编译不因工具面未初始化而崩
            tool_registry = None
    if tool_registry is None:
        class _EmptyRegistry:
            def list_tools(self):
                return []

            def all_metadata(self):
                return {}

        tool_registry = _EmptyRegistry()
    try:
        tool_ids = frozenset(tool_registry.list_tools())
        all_meta = tool_registry.all_metadata()
    except Exception:  # noqa: BLE001 - manifest 编译不得因工具面缺失崩溃
        tool_ids = frozenset()
        all_meta = {}
    tool_versions = {
        name: f"{meta.get('version', '1.0')}#cv{int(meta.get('contract_version', 1) or 1)}"
        for name, meta in all_meta.items()
    }

    # ── 能力↔工具投影（复用 AlgorithmRegistry 的已缓存派生视图）─────
    t2c = dict(algos.tool_to_capability())
    cap_tools: Dict[str, List[str]] = {}
    for tool, cap in t2c.items():
        cap_tools.setdefault(cap, []).append(tool)
    for cap in (cap_tools):
        cap_tools[cap] = sorted(cap_tools[cap])

    cap_ids = set(caps.all_ids) if not callable(getattr(caps, "all_ids", None)) else set(caps.all_ids())
    cap_algos: Dict[str, List[str]] = {}
    for cap_id in sorted(cap_ids):
        cap_algos[cap_id] = list(algos.algorithms_for_capability(cap_id))

    # ── 模型/组件/recipe 面 ──────────────────────────────────────────
    model_ids = set(models.all_ids) if not callable(getattr(models, "all_ids", None)) else set(models.all_ids())
    model_aliases = dict(getattr(models, "_alias", {}) or {})
    comp_ids = set(components._by_id.keys())
    recipe_ids = set(recipes._by_id.keys())

    # ── 编译期一致性校验（dangling / orphan / 幽灵候选）───────────────
    issues: List[str] = []
    for tool, cap in t2c.items():
        if tool not in tool_ids:
            issues.append(f"tool_to_capability 指向未注册工具: {tool}")
        if cap not in cap_ids:
            issues.append(f"工具 {tool} 反查到未注册能力: {cap}")
    for cap, tools in cap_tools.items():
        if cap not in cap_ids:
            issues.append(f"capability_to_tools 含未注册能力: {cap}")
        for tool in tools:
            if tool not in tool_ids:
                issues.append(f"能力 {cap} 的工具 {tool} 不在 ToolRegistry")
    for alias, model_id in model_aliases.items():
        if model_id not in model_ids:
            issues.append(f"模型别名 {alias} 悬空指向 {model_id}")
    for algo_id in (algos.all_ids if not callable(getattr(algos, "all_ids", None)) else algos.all_ids()):
        algo = algos.get(algo_id)
        if algo is None:
            continue
        for cap in algo.capabilities:
            if cap not in cap_ids:
                issues.append(f"算法 {algo_id} 引用未注册能力 {cap}")
        for cand in algo.tool_candidates:
            if cand not in tool_ids:
                issues.append(f"算法 {algo_id} 的候选工具 {cand} 未注册")

    # ── 内容指纹（稳定排序 + 版本，无时间/进程分量）─────────────────
    payload_parts: List[str] = [f"v1:tools:{len(tool_ids)}"]
    payload_parts.extend(
        f"{name}:{tool_versions.get(name, '1.0#cv1')}"
        for name in sorted(tool_ids)
    )
    payload_parts.append(f"caps:{len(cap_ids)}")
    payload_parts.extend(sorted(cap_ids))
    payload_parts.append("t2c:" + ",".join(f"{t}={c}" for t, c in sorted(t2c.items())))
    # 算法语义面（#1084 评审修复）：算法 id + priority + capabilities +
    # 候选表必须进入指纹 —— 否则部署改变算法优先级/候选（正是本批
    # #1075 做的事）后 manifest_stale 仍是 False（假保证）。
    algo_ids = list(algos.all_ids) if not callable(getattr(algos, "all_ids", None)) else list(algos.all_ids())
    algo_parts = []
    for aid in algo_ids:
        algo = algos.get(aid)
        if algo is None:
            continue
        algo_parts.append(
            f"{aid}@{algo.priority}:{'|'.join(algo.capabilities)}"
            f"->{','.join(algo.tool_candidates)}"
        )
    payload_parts.append("algos:" + ";".join(sorted(algo_parts)))
    payload_parts.append("models:" + ",".join(sorted(model_ids)))
    payload_parts.append("alias:" + ",".join(f"{a}={m}" for a, m in sorted(model_aliases.items())))
    payload_parts.append("components:" + ",".join(sorted(comp_ids)))
    payload_parts.append("recipes:" + ",".join(sorted(recipe_ids)))
    digest = hashlib.sha256("\n".join(payload_parts).encode("utf-8")).hexdigest()[:16]

    return GISRuntimeManifest(
        manifest_version=1,
        fingerprint=digest,
        tool_ids=tool_ids,
        tool_versions=tool_versions,
        tool_to_capabilities=t2c,
        capability_to_tools=cap_tools,
        capability_to_algorithms=cap_algos,
        map_model_ids=frozenset(model_ids),
        map_model_aliases=model_aliases,
        component_ids=frozenset(comp_ids),
        recipe_ids=frozenset(recipe_ids),
        validation_issues=issues,
    )


def get_runtime_manifest(tool_registry: Optional[Any] = None) -> GISRuntimeManifest:
    """进程级单例（惰性首编译；线程安全）。"""
    global _manifest
    if _manifest is None:
        with _lock:
            if _manifest is None:
                _manifest = compile_runtime_manifest(tool_registry)
    return _manifest


def reset_runtime_manifest() -> None:
    """测试/动态注册后失效缓存 —— 下一次 get 重编译（新指纹）。"""
    global _manifest
    with _lock:
        _manifest = None


def current_manifest_fingerprint() -> str:
    return get_runtime_manifest().fingerprint
