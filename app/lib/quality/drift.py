"""Contract Drift Gates（ADR-0104 Wave 4）——跨 registry 契约漂移检测。

每个 validator 返回 ``DriftIssue`` 列表。severity 语义：
- ``BLOCKER``：运行期必然破损的契约（悬空引用 / 前端调用不存在的后端路由 /
  capability 无生产者）；
- ``MAJOR``：静默降级风险（声明指向不存在词表 / 科学元数据断链）；
- ``MINOR``：富化债（描述符缺声明，QualityManifest 棘轮已跟踪）。

报告是**派生物**：``scripts/gen_drift_report.py`` 写
``docs/quality/CONTRACT_DRIFT_REPORT.{md,json}``，字节一致性 +
"零 BLOCKER/MAJOR" 由 ``tests/quality/test_contract_drift_gate.py`` 红线。
只读投影：绝不修改任何 registry。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List

from app.lib.quality.discovery import repo_root

SEVERITIES = ("BLOCKER", "MAJOR", "MINOR")

# 前端 API 调用扫描根与正则（frontend 源码内的 /api/... 字符串字面量）
_FRONTEND_SCAN_ROOTS = ("frontend/lib", "frontend/app", "frontend/components", "frontend/hooks")
_FRONTEND_PATH_RE = re.compile(r"/api/v[0-9]+/[A-Za-z0-9_\-./${}()\[\]]*")

_PARAM_RE = re.compile(r"\{[^/{}]+\}")


@dataclass(frozen=True)
class DriftIssue:
    checker: str
    severity: str
    code: str
    subject: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "checker": self.checker,
            "severity": self.severity,
            "code": self.code,
            "subject": self.subject,
            "detail": self.detail,
        }


def _issue(
    checker: str, severity: str, code: str, subject: str, detail: str
) -> DriftIssue:
    return DriftIssue(checker, severity, code, subject, detail)


# ── 1. Tool descriptor ↔ registry/cross-vocabulary ──────────────────────


def check_tool_contract_drift() -> List[DriftIssue]:
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    issues: List[DriftIssue] = []
    cap_ids = set(get_capability_registry().all_ids)
    algo_ids = set(get_algorithm_registry().all_ids)
    reg = ToolRegistry()
    init_tools(reg)
    for name in sorted(reg.descriptors().keys()):
        d = reg.descriptor(name)
        for cap in d.capabilities:
            if cap not in cap_ids:
                issues.append(_issue(
                    "tool_contract", "BLOCKER", "TOOL_CAPABILITY_DANGLING",
                    name, f"capability {cap!r} not in CapabilityRegistry"))
        for algo in d.algorithms:
            if algo not in algo_ids:
                issues.append(_issue(
                    "tool_contract", "BLOCKER", "TOOL_ALGORITHM_DANGLING",
                    name, f"algorithm {algo!r} not in AlgorithmRegistry"))
        if d.fallback_tool:
            try:
                fallback_exists = reg.descriptor(d.fallback_tool) is not None
            except Exception:  # noqa: BLE001
                fallback_exists = False
            if not fallback_exists:
                issues.append(_issue(
                    "tool_contract", "MAJOR", "TOOL_FALLBACK_DANGLING",
                    name, f"fallback_tool {d.fallback_tool!r} not registered"))
    return issues


# ── 2. Algorithm descriptor ↔ implementations / vocabularies ────────────


def check_algorithm_contract_drift() -> List[DriftIssue]:
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.method_references import reference_exists
    from app.lib.gis.uncertainty import UNCERTAINTY_TYPE_VOCABULARY
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    issues: List[DriftIssue] = []
    cap_ids = set(get_capability_registry().all_ids)
    art_ids = set(get_artifact_type_registry().all_ids)
    model_ids = set()
    try:
        from app.lib.cartography.model_library import get_map_model_registry
        model_ids = set(get_map_model_registry().all_ids)
    except Exception:  # noqa: BLE001 — cartography 包缺失时跳过该子检查
        model_ids = set()
    tool_reg = ToolRegistry()
    init_tools(tool_reg)
    algo_reg = get_algorithm_registry()
    root = repo_root()
    for aid in sorted(algo_reg.all_ids):
        algo = algo_reg.get(aid)
        if algo is None:  # pragma: no cover
            continue
        for cap in algo.capabilities:
            if cap not in cap_ids:
                issues.append(_issue(
                    "algorithm_contract", "BLOCKER", "ALGO_CAPABILITY_DANGLING",
                    aid, f"capability {cap!r} not in CapabilityRegistry"))
        for tool_name in algo.tool_candidates:
            try:
                known = tool_reg.descriptor(tool_name) is not None
            except Exception:  # noqa: BLE001
                known = False
            if not known:
                issues.append(_issue(
                    "algorithm_contract", "BLOCKER", "ALGO_TOOL_CANDIDATE_DANGLING",
                    aid, f"tool_candidate {tool_name!r} not registered"))
        for fallback in algo.fallback_algorithms:
            if not algo_reg.has(fallback):
                issues.append(_issue(
                    "algorithm_contract", "BLOCKER", "ALGO_FALLBACK_DANGLING",
                    aid, f"fallback_algorithm {fallback!r} not registered"))
        for key in algo.fallback_semantics:
            if key not in algo.fallback_algorithms:
                issues.append(_issue(
                    "algorithm_contract", "MAJOR", "ALGO_FALLBACK_SEMANTICS_ORPHAN",
                    aid, f"fallback_semantics key {key!r} not in fallback_algorithms"))
        for ref in algo.method_references:
            if not reference_exists(ref):
                issues.append(_issue(
                    "algorithm_contract", "MAJOR", "ALGO_METHOD_REF_DANGLING",
                    aid, f"method_reference {ref!r} unknown"))
        for u in algo.uncertainty_outputs:
            if u not in UNCERTAINTY_TYPE_VOCABULARY:
                issues.append(_issue(
                    "algorithm_contract", "MAJOR", "ALGO_UNCERTAINTY_TERM_UNKNOWN",
                    aid, f"uncertainty_outputs term {u!r} outside vocabulary"))
        for at in [*algo.input_artifact_types, algo.output_artifact_type]:
            if at and at not in art_ids:
                issues.append(_issue(
                    "algorithm_contract", "BLOCKER", "ALGO_ARTIFACT_TYPE_DANGLING",
                    aid, f"artifact type {at!r} not registered"))
        for mid in algo.compatible_map_models:
            if model_ids and mid not in model_ids:
                issues.append(_issue(
                    "algorithm_contract", "MAJOR", "ALGO_MAP_MODEL_DANGLING",
                    aid, f"compatible_map_model {mid!r} not registered"))
        for node in algo.conformance_tests:
            node_id = str(node)
            file_part = node_id.split("::", 1)[0]
            if file_part and not (root / file_part).exists():
                issues.append(_issue(
                    "algorithm_contract", "MAJOR", "ALGO_CONFORMANCE_NODE_MISSING",
                    aid, f"conformance node {node_id!r} points to missing file"))
    return issues


# ── 3. Capability ↔ producers / artifact vocabulary ─────────────────────


def check_capability_contract_drift() -> List[DriftIssue]:
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    issues: List[DriftIssue] = []
    art_ids = set(get_artifact_type_registry().all_ids)
    cap_reg = get_capability_registry()
    algo_reg = get_algorithm_registry()
    producers: Dict[str, int] = {}
    for aid in algo_reg.all_ids:
        algo = algo_reg.get(aid)
        if algo is None:  # pragma: no cover
            continue
        for cap in algo.capabilities:
            producers[cap] = producers.get(cap, 0) + 1
    for cid in sorted(cap_reg.all_ids):
        cap = cap_reg.get(cid)
        if cap is None:  # pragma: no cover
            continue
        for at in [*cap.input_artifact_types, *cap.output_artifact_types]:
            if at not in art_ids:
                issues.append(_issue(
                    "capability_contract", "BLOCKER", "CAP_ARTIFACT_TYPE_DANGLING",
                    cid, f"artifact type {at!r} not registered"))
        for fb in cap.fallback_capabilities:
            if not cap_reg.has(fb):
                issues.append(_issue(
                    "capability_contract", "BLOCKER", "CAP_FALLBACK_DANGLING",
                    cid, f"fallback_capability {fb!r} not registered"))
        for mid in cap.compatible_map_models:
            try:
                from app.lib.cartography.model_library import get_map_model_registry
                if get_map_model_registry().get(mid) is None:
                    issues.append(_issue(
                        "capability_contract", "MAJOR", "CAP_MAP_MODEL_DANGLING",
                        cid, f"compatible_map_model {mid!r} not registered"))
            except Exception:  # noqa: BLE001
                pass
        if str(cap.status) == "native" and producers.get(cid, 0) == 0:
            # 工具直连 capability（无算法层）也是合法生产者：再查一次 tool 侧。
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            tool_reg = ToolRegistry()
            init_tools(tool_reg)
            tool_produced = any(
                cid in d.capabilities for d in tool_reg.descriptors().values()
            )
            if not tool_produced:
                issues.append(_issue(
                    "capability_contract", "BLOCKER", "CAP_NO_PRODUCER",
                    cid, "native capability without algorithm or tool producer"))
    return issues


# ── 4. Map model ↔ component / artifact vocabulary ──────────────────────


def check_map_model_contract_drift() -> List[DriftIssue]:
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.model_library import (
        get_map_model_registry,
        validate_model_library,
    )
    from app.lib.gis.artifacts import get_artifact_type_registry

    issues: List[DriftIssue] = []
    for violation in validate_model_library():
        issues.append(_issue(
            "map_model_contract", "MAJOR", "MAP_MODEL_LIBRARY_VIOLATION",
            "-", str(violation)))
    comp_ids = set(get_component_registry().all_ids)
    art_ids = set(get_artifact_type_registry().all_ids)
    reg = get_map_model_registry()
    for mid in sorted(reg.all_ids):
        model = reg.get(mid)
        if model is None:  # pragma: no cover
            continue
        for at in model.accepted_artifact_types:
            if at not in art_ids:
                issues.append(_issue(
                    "map_model_contract", "BLOCKER", "MAP_MODEL_ARTIFACT_DANGLING",
                    mid, f"accepted_artifact_type {at!r} not registered"))
        for comp in model.recommended_components:
            if comp not in comp_ids:
                issues.append(_issue(
                    "map_model_contract", "MAJOR", "MAP_MODEL_COMPONENT_DANGLING",
                    mid, f"recommended_component {comp!r} not in ComponentRegistry"))
    return issues


# ── 5. Recipe ↔ ontology / workflow task family ─────────────────────────


def check_recipe_contract_drift() -> List[DriftIssue]:
    from app.services.gis_harness.gis_ontology import ONTOLOGY_TASKS
    from app.services.gis_harness.recipes import get_recipe_registry

    issues: List[DriftIssue] = []
    task_ids = {t.task_id for t in ONTOLOGY_TASKS}
    reg = get_recipe_registry()
    for rid in sorted(reg.all_ids):
        recipe = reg.get(rid)
        if recipe is None:  # pragma: no cover
            continue
        for task in getattr(recipe, "ontology_tasks", ()) or ():
            if task not in task_ids:
                issues.append(_issue(
                    "recipe_contract", "MAJOR", "RECIPE_ONTOLOGY_TASK_DANGLING",
                    rid, f"ontology_task {task!r} not in ONTOLOGY_TASKS"))
    return issues


# ── 6. Backend API ↔ frontend client ────────────────────────────────────


def _backend_api_routes() -> Dict[str, set]:
    """path（参数折叠为 *）→ method 集合。直接枚举各 routes 模块的 router。"""
    routes_dir = repo_root() / "app" / "api" / "routes"
    from fastapi import APIRouter

    table: Dict[str, set] = {}
    for py in sorted(routes_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        module_name = f"app.api.routes.{py.stem}"
        try:
            import importlib

            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 — 无法导入的 route 模块是 BLOCKER
            continue
        for attr in vars(module).values():
            if isinstance(attr, APIRouter):
                # 模块级 router 自身 prefix 不在 route.path 里（include 时才
                # 拼接），必须显式补上；嵌套 include 的子前缀已被扁平化。
                prefix = getattr(attr, "prefix", "") or ""
                for route in attr.routes:
                    path = getattr(route, "path", None)
                    if not path:
                        continue
                    methods = getattr(route, "methods", None) or set()
                    full = path if path.startswith(prefix) else prefix + path
                    norm = _PARAM_RE.sub("*", full)
                    if not norm.startswith("/api/"):
                        norm = "/api/v1" + norm
                    table.setdefault(norm, set()).update(
                        m.upper() for m in methods)
    return table


def _frontend_api_calls() -> Dict[str, int]:
    """frontend 源码里出现的 /api/... 调用 path（参数折叠为 *）→ 出现次数。

    清洗规则（按序）：丢问号查询串；模板插值 ``${...}`` 折叠为 ``*``
    （嵌套安全）；去尾部 ``/`` ``.``；去尾部不成对 ``)``（代码行尾噪声）；
    残余未闭合 ``${`` 从截断点起视为参数尾部。注释行（// * /* #开头）跳过。
    """
    root = repo_root()
    counts: Dict[str, int] = {}
    scanned = 0
    for rel_root in _FRONTEND_SCAN_ROOTS:
        base = root / rel_root
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*")):
            if scanned > 4000:
                break
            if py.suffix not in {".ts", ".tsx", ".js", ".jsx"} or not py.is_file():
                continue
            if py.name.endswith(".test.ts") or py.name.endswith(".test.tsx"):
                continue
            scanned += 1
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*", "#")):
                    continue
                for match in _FRONTEND_PATH_RE.findall(line):
                    raw = match.split("?", 1)[0]
                    raw = re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "*", raw)
                    raw = raw.rstrip("/.")
                    while raw.endswith(")") and raw.count(")") > raw.count("("):
                        raw = raw[:-1]
                    if "${" in raw:
                        raw = raw.split("${", 1)[0].rstrip("/")
                    # MapLibre tile URL 的字面量占位 {z}/{x}/{y} 在后端是
                    # 真 path 参数，统一折叠为 *（与 backend 侧口径一致）。
                    raw = _PARAM_RE.sub("*", raw)
                    if not raw or raw == "/api/v1":
                        continue
                    counts[raw] = counts.get(raw, 0) + 1
    return counts


def check_api_frontend_drift() -> List[DriftIssue]:
    backend = _backend_api_routes()
    frontend = _frontend_api_calls()
    issues: List[DriftIssue] = []
    backend_paths = sorted(backend.keys())
    for path, count in sorted(frontend.items()):
        if path in backend:
            continue
        if path.endswith("*"):
            stem = path[:-1]
            if any(p == stem or p.startswith(stem) for p in backend_paths):
                continue
        # 前端 startsWith 前缀字面量（'/api/.../x/'）：后端存在该前缀路由族
        # 即视为一致。
        if any(p.startswith(path + "/") for p in backend_paths):
            continue
        issues.append(_issue(
            "api_frontend", "BLOCKER", "FRONTEND_CALL_NO_BACKEND_ROUTE",
            path, f"frontend calls {path} ×{count} but no backend route matches"))
    return issues


# ── 汇总 / 渲染 ──────────────────────────────────────────────────────────

CHECKERS = (
    check_tool_contract_drift,
    check_algorithm_contract_drift,
    check_capability_contract_drift,
    check_map_model_contract_drift,
    check_recipe_contract_drift,
    check_api_frontend_drift,
)


@dataclass
class DriftReport:
    fingerprint: str
    counts: Dict[str, int]
    issues: List[Dict[str, str]]

    @property
    def blocking(self) -> List[Dict[str, str]]:
        return [i for i in self.issues if i["severity"] in ("BLOCKER", "MAJOR")]


def compile_drift_report() -> DriftReport:
    issues: List[DriftIssue] = []
    for checker in CHECKERS:
        issues.extend(checker())
    issues.sort(key=lambda i: (i.checker, i.severity, i.code, i.subject, i.detail))
    serialized = [i.to_dict() for i in issues]
    counts = {s: sum(1 for i in issues if i.severity == s) for s in SEVERITIES}
    counts["total"] = len(issues)
    payload = json.dumps(
        serialized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return DriftReport(fingerprint=fingerprint, counts=counts, issues=serialized)


def render_json(report: DriftReport) -> str:
    return json.dumps(
        {
            "fingerprint": report.fingerprint,
            "counts": report.counts,
            "issues": report.issues,
        },
        ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def render_markdown(report: DriftReport) -> str:
    lines: List[str] = []
    lines.append("# Contract Drift Report（自动生成）")
    lines.append("")
    lines.append(
        "> 由 `python scripts/gen_drift_report.py` 从各 registry 派生，请勿手改。"
        "红线：BLOCKER/MAJOR 必须清零（tests/quality/test_contract_drift_gate.py）。"
    )
    lines.append("")
    lines.append(f"- 指纹：`{report.fingerprint[:16]}…`")
    lines.append(
        f"- 计数：total={report.counts['total']}，"
        f"BLOCKER={report.counts['BLOCKER']}，"
        f"MAJOR={report.counts['MAJOR']}，"
        f"MINOR={report.counts['MINOR']}"
    )
    lines.append("")
    by_checker: Dict[str, List[Dict[str, str]]] = {}
    for issue in report.issues:
        by_checker.setdefault(issue["checker"], []).append(issue)
    for checker in sorted(by_checker):
        items = by_checker[checker]
        lines.append(f"## {checker}（{len(items)}）")
        lines.append("")
        for issue in items[:50]:
            lines.append(
                f"- **{issue['severity']}** `{issue['code']}` {issue['subject']}"
                f" — {issue['detail']}"
            )
        if len(items) > 50:
            lines.append(f"- …另有 {len(items) - 50} 条，见 CONTRACT_DRIFT_REPORT.json")
        lines.append("")
    if not report.issues:
        lines.append("（无漂移）")
        lines.append("")
    return "\n".join(lines)


def drift_report_fingerprint_stable(report: DriftReport) -> bool:
    again = compile_drift_report()
    return again.fingerprint == report.fingerprint
