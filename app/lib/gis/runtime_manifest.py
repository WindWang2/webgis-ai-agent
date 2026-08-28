"""Compiled GIS Runtime Manifest —— 运行时能力真相（GIS Harness Runtime v2）。

Phase 3（compile once + cross-registry validation）与 Phase 4（fingerprint
+ stale-plan guard）的载体。此前各 registry 在运行时互相扫描：

- ``plan_from_intent.resolve()`` 之后再 ``resolve_tool_for_capability()`` 重复
  解析（同一 intent 每会话 2-3 次）；
- ``capability_tool_map()`` 每次调用重建（无缓存）；
- ``validate_gis_library`` 只有测试调用 —— 悬空引用（孤儿工具/错绑
  capability/dangling alias）在运行期静默降级；
- plan / evidence 不携带 registry 版本 —— 部署升级后旧计划对新 registry
  语义静默重放（issue #1084）。

本模块在进程启动时把全部 registry 源编译为一份**不可变快照**：

    registry sources → validation → cross-registry graph → compiled manifest

并给出：
- O(1) 反查：``tool_to_capability`` / ``capability_to_tools`` /
  ``capability_to_algorithms`` / ``resolve_map_model``；
- 内容敏感的稳定指纹 ``manifest_fingerprint``（SHA256，descriptor 投影的
  canonical JSON，键排序；tool_candidates 的顺序参与指纹——顺序即解析
  优先级，语义承载）；
- 分类校验议题：``fatal``（启动 fail-fast）/ ``warning`` / ``planned``。

约束：
- 编译是**只读投影**——manifest 绝不反写 registry，registry 仍是注册权威；
- 指纹**不含** compiled_at / issue 文本（只含语义内容），同一 registry 内容
  跨进程/跨重启指纹稳定；
- 刷新：``refresh_runtime_manifest()``（测试/用户模板注册后调用）；
  ``get_runtime_manifest()`` 惰性编译一次并缓存。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 2

# severity 语义：
#  fatal   —— 运行时必需 contract 破损：启动 fail-fast（GIS_MANIFEST_STRICT=0
#             可降级为 warning，供运维逃生）。
#  warning —— 可恢复的漂移（部分候选缺失 / 悬空次要引用 / 重复注册）。
#  planned —— 有意的规划态（planned capability/model 无 native 实现）。
_SEVERITIES = ("fatal", "warning", "planned")


@dataclass(frozen=True)
class ManifestIssue:
    severity: str
    code: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"severity": self.severity, "code": self.code, "detail": self.detail}


@dataclass
class CompiledRuntimeManifest:
    """不可变运行时能力快照（compile once, read everywhere）。"""

    manifest_version: int = MANIFEST_VERSION
    compiled_at: str = ""
    fingerprint: str = ""
    issues: List[ManifestIssue] = field(default_factory=list)

    # ── 目录投影（id/name/status/version 的有界投影，不含大描述体）────
    capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    algorithms: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    map_models: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    templates: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    recipes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    product_templates: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ── cross-registry 图（O(1) 反查）──────────────────────────────────
    tool_to_capability: Dict[str, List[str]] = field(default_factory=dict)
    capability_to_tools: Dict[str, List[str]] = field(default_factory=dict)
    capability_to_algorithms: Dict[str, List[str]] = field(default_factory=dict)
    alias_to_map_model: Dict[str, str] = field(default_factory=dict)

    # ── 访问器 ─────────────────────────────────────────────────────────
    def capability_for_tool(self, tool_name: str) -> List[str]:
        """工具反查 capability（O(1)；无绑定返回空表）。"""
        return self.tool_to_capability.get(tool_name, [])

    def tools_for_capability(self, capability_id: str) -> List[str]:
        """capability 的可用工具（按算法 priority 序，O(1) 读预排序表）。"""
        return self.capability_to_tools.get(capability_id, [])

    def fatal_issues(self) -> List[ManifestIssue]:
        return [i for i in self.issues if i.severity == "fatal"]

    def is_stale_plan(self, stored_fingerprint: Optional[str]) -> bool:
        """#1084：持久 plan/evidence 的 registry 指纹与当前不一致 → stale。

        空存储指纹（历史计划）不判 stale —— 只对显式携带指纹的计划 guard，
        避免升级即全量作废。
        """
        if not stored_fingerprint:
            return False
        return stored_fingerprint != self.fingerprint

    def summary(self) -> Dict[str, Any]:
        by_sev: Dict[str, int] = {s: 0 for s in _SEVERITIES}
        for issue in self.issues:
            by_sev[issue.severity] = by_sev.get(issue.severity, 0) + 1
        return {
            "manifest_version": self.manifest_version,
            "fingerprint": self.fingerprint,
            "counts": {
                "capabilities": len(self.capabilities),
                "algorithms": len(self.algorithms),
                "tools": len(self.tools),
                "map_models": len(self.map_models),
                "components": len(self.components),
                "templates": len(self.templates),
                "recipes": len(self.recipes),
                "product_templates": len(self.product_templates),
            },
            "issues": by_sev,
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _registry_ids(registry: Any) -> List[str]:
    """all_ids 在各 registry 上有 property / method 两种形态，统一读取。"""
    attr = getattr(registry, "all_ids", None)
    if attr is None:
        return []
    return list(attr() if callable(attr) else attr)


def _project_capability(cap) -> Dict[str, Any]:
    return {
        "id": cap.id,
        "status": getattr(cap, "status", "native"),
        "version": getattr(cap, "version", "1.0"),
        "category": getattr(cap, "category", ""),
        "domain": getattr(cap, "domain", ""),
        "fallback_capabilities": sorted(getattr(cap, "fallback_capabilities", None) or []),
    }


def _project_algorithm(algo) -> Dict[str, Any]:
    return {
        "id": algo.id,
        "capabilities": sorted(algo.capabilities or []),
        # 顺序参与指纹：候选顺序 = 解析优先级（语义承载，不排序）。
        "tool_candidates": list(algo.tool_candidates or []),
        "runtime_status": getattr(algo, "runtime_status", "native"),
        "version": getattr(algo, "version", "1.0"),
        "contract_version": getattr(algo, "contract_version", 1),
        "priority": getattr(algo, "priority", 50),
        "fallback_algorithms": sorted(getattr(algo, "fallback_algorithms", None) or []),
    }


def _project_tool(meta: Any) -> Dict[str, Any]:
    return {
        "version": getattr(meta, "version", "1.0") or "1.0",
        "contract_version": getattr(meta, "contract_version", 1) or 1,
        "tier": getattr(meta, "tier", 1) or 1,
        "domains": sorted(getattr(meta, "domains", None) or []),
    }


def compile_runtime_manifest(tool_registry: Optional[Any] = None) -> CompiledRuntimeManifest:
    """从全部 registry 源编译快照 + 校验 + 指纹。

    ``tool_registry`` 缺省时惰性初始化一个进程内注册表（main.lifespan 传入
    真实实例；测试可传替身）。编译对单个 registry 的导入失败容错——该源
    记 ``fatal`` issue（strict 模式启动即拒）。
    """
    from datetime import datetime, timezone

    manifest = CompiledRuntimeManifest(compiled_at=datetime.now(timezone.utc).isoformat())
    issues: List[ManifestIssue] = []

    def _warn(code: str, detail: str) -> None:
        issues.append(ManifestIssue("warning", code, detail))

    def _fatal(code: str, detail: str) -> None:
        issues.append(ManifestIssue("fatal", code, detail))

    def _planned(code: str, detail: str) -> None:
        issues.append(ManifestIssue("planned", code, detail))

    # ── 1. capability / algorithm ─────────────────────────────────────
    cap_ids: set = set()
    algo_ids: set = set()
    try:
        from app.lib.gis.capability_registry import get_capability_registry
        cr = get_capability_registry()
        cap_ids = set(_registry_ids(cr))
        for cid in _registry_ids(cr):
            manifest.capabilities[cid] = _project_capability(cr.get(cid))
    except Exception as e:  # noqa: BLE001
        _fatal("capability_registry_unavailable", str(e))
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        ar = get_algorithm_registry()
        algo_ids = set(_registry_ids(ar))
        for aid in _registry_ids(ar):
            manifest.algorithms[aid] = _project_algorithm(ar.get(aid))
    except Exception as e:  # noqa: BLE001
        _fatal("algorithm_registry_unavailable", str(e))

    # ── 2. tool registry（真实实例优先）──────────────────────────────
    tool_names: set = set()
    try:
        if tool_registry is None:
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry
            tool_registry = ToolRegistry()
            init_tools(tool_registry)
        meta_getter = getattr(tool_registry, "get_metadata", None)
        name_getter = getattr(tool_registry, "list_tool_names", None)
        if name_getter is not None:
            tool_names = set(name_getter())
        else:
            tool_names = set(getattr(tool_registry, "_tools", {}).keys())
        for name in sorted(tool_names):
            meta = meta_getter(name) if callable(meta_getter) else None
            manifest.tools[name] = _project_tool(meta if meta is not None else type("M", (), {})())
    except Exception as e:  # noqa: BLE001
        _fatal("tool_registry_unavailable", str(e))

    # ── 3. cross-registry 图 + 校验（algorithm ↔ tool ↔ capability）────
    for aid, proj in sorted(manifest.algorithms.items()):
        for cap in proj["capabilities"]:
            if cap not in cap_ids:
                _fatal("algorithm_dangling_capability", f"{aid} → capability {cap} 不存在")
        missing_tools = [t for t in proj["tool_candidates"] if t not in tool_names]
        if proj["tool_candidates"] and missing_tools == proj["tool_candidates"]:
            _fatal(
                "algorithm_no_tool",
                f"{aid} 的全部候选工具不存在: {missing_tools}",
            )
        elif missing_tools:
            _warn(
                "algorithm_partial_tools",
                f"{aid} 部分候选工具不存在（fallback 缩窄）: {missing_tools}",
            )
        for fb in proj["fallback_algorithms"]:
            if fb not in algo_ids:
                _warn("algorithm_dangling_fallback", f"{aid} → fallback 算法 {fb} 不存在")

    # 反查图（capability_to_tools 按算法 priority 升序 = 解析优先序）
    for aid, proj in sorted(manifest.algorithms.items(), key=lambda kv: kv[1]["priority"]):
        for cap in proj["capabilities"]:
            manifest.capability_to_algorithms.setdefault(cap, []).append(aid)
            for t in proj["tool_candidates"]:
                if t not in tool_names:
                    continue
                caps_for_tool = manifest.tool_to_capability.setdefault(t, [])
                if cap not in caps_for_tool:
                    caps_for_tool.append(cap)
                tools_for_cap = manifest.capability_to_tools.setdefault(cap, [])
                if t not in tools_for_cap:
                    tools_for_cap.append(t)

    # 网络域工具必须有 capability 归属（R2 parity 的持续防回归）
    for name, proj in sorted(manifest.tools.items()):
        if "network" in proj["domains"] and name not in manifest.tool_to_capability:
            _warn("network_tool_orphan", f"{name} 无 capability 绑定（planner 不可达）")

    # planned capability 无 native 算法 → planned 议题（信息性）
    for cid, proj in sorted(manifest.capabilities.items()):
        if proj["status"] == "planned" and cid not in manifest.capability_to_algorithms:
            _planned("planned_capability", f"{cid} 为 planned 且无算法实现")
        if proj["status"] == "native" and cid not in manifest.capability_to_algorithms:
            _warn("capability_no_algorithm", f"native capability {cid} 无算法绑定")

    # ── 4. map model registry（别名解析）─────────────────────────────
    try:
        from app.lib.cartography.model_library import get_map_model_registry
        ml = get_map_model_registry()
        ml_ids = set(getattr(ml, "_by_id", {}).keys())
        for mid in sorted(ml_ids):
            model = ml.get(mid)
            manifest.map_models[mid] = {
                "runtime_status": getattr(model, "runtime_status", "native"),
                "version": getattr(model, "version", "1.0"),
                "aliases": sorted(getattr(model, "aliases", None) or []),
            }
        alias_map: Dict[str, str] = dict(getattr(ml, "_alias", {}) or {})
        for alias, target in sorted(alias_map.items()):
            if target not in ml_ids:
                _fatal("map_alias_dangling", f"别名 {alias} → 模型 {target} 不存在")
            else:
                manifest.alias_to_map_model[alias] = target
    except Exception as e:  # noqa: BLE001
        _warn("map_model_registry_unavailable", str(e))

    # ── 5. component / template / recipe / product template ──────────
    try:
        from app.lib.cartography.component_registry import get_component_registry
        comp = get_component_registry()
        for cid in _registry_ids(comp):
            d = comp.get(cid)
            manifest.components[cid] = {
                "type": getattr(d, "type", ""),
                "category": str(getattr(d, "category", "")),
                "runtime_status": getattr(d, "runtime_status", "native"),
            }
    except Exception as e:  # noqa: BLE001
        _warn("component_registry_unavailable", str(e))
    try:
        from app.schemas.template_registry import get_template_registry
        tr = get_template_registry()
        for tid in sorted(getattr(tr, "_by_id", {}).keys()):
            t = tr.get(tid)
            manifest.templates[tid] = {
                "kind": str(getattr(t, "kind", "")),
                "version": str(getattr(t, "version", "1")),
            }
    except Exception as e:  # noqa: BLE001
        _warn("template_registry_unavailable", str(e))
    try:
        from app.services.gis_harness.recipes import get_recipe_registry
        rr = get_recipe_registry()
        for rid in _registry_ids(rr):
            r = rr.get(rid)
            caps = sorted(getattr(r, "capabilities", None) or [])
            manifest.recipes[rid] = {"capabilities": caps, "task": getattr(r, "task", "")}
            for c in caps:
                if c not in cap_ids:
                    _fatal("recipe_dangling_capability", f"recipe {rid} → capability {c} 不存在")
    except Exception as e:  # noqa: BLE001
        _warn("recipe_registry_unavailable", str(e))
    try:
        from app.services.gis_harness.product_templates import get_product_template_registry
        ptr = get_product_template_registry()
        for pid in _registry_ids(ptr):
            manifest.product_templates[pid] = {}
    except Exception as e:  # noqa: BLE001
        _warn("product_template_registry_unavailable", str(e))

    # ── 6. 指纹（Phase 4）────────────────────────────────────────────
    fingerprint_payload = {
        "manifest_version": MANIFEST_VERSION,
        "capabilities": manifest.capabilities,
        "algorithms": manifest.algorithms,
        "tools": manifest.tools,
        "map_models": manifest.map_models,
        "alias_to_map_model": manifest.alias_to_map_model,
        "components": manifest.components,
        "templates": manifest.templates,
        "recipes": manifest.recipes,
        "product_templates": manifest.product_templates,
    }
    manifest.fingerprint = hashlib.sha256(
        _canonical(fingerprint_payload).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()
    manifest.issues = issues
    return manifest


_lock = threading.Lock()
_cached_manifest: Optional[CompiledRuntimeManifest] = None


def get_runtime_manifest(refresh: bool = False) -> CompiledRuntimeManifest:
    """进程级单例访问（compile once）。测试/模板注册后传 refresh=True。"""
    global _cached_manifest
    with _lock:
        if _cached_manifest is None or refresh:
            _cached_manifest = compile_runtime_manifest()
        return _cached_manifest


def refresh_runtime_manifest() -> CompiledRuntimeManifest:
    return get_runtime_manifest(refresh=True)


def validate_runtime_manifest_strict(manifest: CompiledRuntimeManifest) -> None:
    """启动 fail-fast 门（lifespan 调用）。

    fatal 议题 → RuntimeError（GIS_MANIFEST_STRICT=0 降级为日志）。
    warning/planned → 日志（可观测，不阻断）。
    """
    import os

    fatal = manifest.fatal_issues()
    for issue in manifest.issues:
        logger.log(
            logging.WARNING if issue.severity == "fatal" else logging.INFO,
            "[runtime-manifest] %s %s: %s", issue.severity, issue.code, issue.detail,
        )
    if fatal and os.environ.get("GIS_MANIFEST_STRICT", "1") != "0":
        raise RuntimeError(
            "Compiled GIS Runtime Manifest has fatal issues: "
            + "; ".join(f"{i.code}: {i.detail}" for i in fatal)
            + " (set GIS_MANIFEST_STRICT=0 to downgrade)"
        )
