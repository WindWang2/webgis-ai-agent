"""Quality Manifest —— 质量清单派生编译器（ADR-0104）。

QualityManifest 是**投影**，不是事实源：

    registries（唯一事实源） + tests 引用索引 → introspection → manifest

内容：
- tools / algorithms / capabilities / artifact_types / recipes 目录投影，
  每行携带静态测试引用（``discovery.discover_test_references``）；
- 派生 findings（registered-but-untested、descriptor 富化缺口、
  capability 无生产者、artifact 类型无测试等）；
- 描述符富化覆盖率闸（ADR-0103 语义；``scripts/check_tool_descriptor_coverage.py``
  自 ADR-0104 起是本模块的薄壳，历史 import 面 ``GATE_THRESHOLDS`` /
  ``collect()`` / ``gate()`` 保持不变）；
- 内容敏感稳定指纹（canonical JSON，sha256；不含时间戳）。

约束：
- 只读投影——绝不反写 registry；
- 确定性——同一提交两次编译字节一致（排序、有界、无时间戳）；
- 生成物（docs/quality/QUALITY_MANIFEST.md、quality-manifest.json）禁止手改，
  字节一致性由 tests/quality/test_quality_manifest_gate.py 红线保护；
- 静态引用 ≠ 行为覆盖：findings 是"待人工/自动复核的线索"，不是缺陷判定。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.lib.quality.behavioral import behavior_level, discover_behavioral_dispatch
from app.lib.quality.discovery import discover_test_references, repo_root

MANIFEST_VERSION = 2

#: findings 棘轮 / waiver 配置文件（提交物；由 tests/quality 闸保护）。
FINDINGS_BASELINE_PATH = "docs/quality/findings-baseline.json"
WAIVERS_PATH = "docs/quality/waivers.json"

# findings 数量边界：MD 渲染时每个 code 最多列出的条目数（JSON 永远全量）。
MD_FINDINGS_LIMIT = 50

# ── ADR-0103 描述符富化闸 ────────────────────────────────────────────────
# 语义：富化只许前进，不许回退。阈值为当前基线的整型百分比下限；
# 新工具加入或字段回填使覆盖率上升后，可由后续 ADR 显式上调。
GATE_FIELDS: Tuple[str, ...] = (
    "side_effect",
    "tags",
    "latency_class",
    "memory_class",
    "capabilities",
)
GATE_THRESHOLDS: Dict[str, int] = {
    # 2026-09 基线棘轮：279 个可执行工具实测 side_effect/tags=83%、
    # capabilities=60%、latency/memory=100%。阈值钉在基线（100% 类留 5%
    # 新工具余量），任何批量回退即红；上调需后续 ADR。
    "side_effect": 83,
    "tags": 83,
    "latency_class": 95,
    "memory_class": 95,
    "capabilities": 60,
}

# findings 词表（稳定 code；消费方包括闸测试与 Wave 20 质量报告）
FINDING_CODES: Tuple[str, ...] = (
    "TOOL_UNTESTED",
    "TOOL_DESTRUCTIVE_UNTESTED",
    "TOOL_DESCRIPTOR_INCOMPLETE",
    "CAPABILITY_NO_PRODUCER",
    "CAPABILITY_NO_CONFORMANCE",
    "ALGO_NO_CONFORMANCE",
    "ALGO_HEAVY_NO_VARIANTS",
    "ALGO_SEED_POLICY_CONFLICT",
    "ALGO_PRODUCTION_NO_UNCERTAINTY",
    "ARTIFACT_TYPE_UNTESTED",
)

_SEVERITIES: Tuple[str, ...] = ("high", "medium", "low")


@dataclass(frozen=True)
class QualityFinding:
    """一条派生发现（code 稳定、subject 是 registry 标识、detail 人类可读）。

    V2：``surface`` 定位到 manifest 分区，``domain`` 定位到业务域，
    让每条 findings 可路由到 owner（.agent-work/quality-v2/01-architecture）。
    """

    code: str
    severity: str
    subject: str
    detail: str
    surface: str = ""
    domain: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "subject": self.subject,
            "detail": self.detail,
            "surface": self.surface,
            "domain": self.domain,
        }


@dataclass
class QualityManifest:
    """编译后的质量清单（全部字段均为可 JSON 序列化的 plain data）。"""

    manifest_version: int = MANIFEST_VERSION
    fingerprint: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    gate_report: Dict[str, Any] = field(default_factory=dict)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    algorithms: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    artifact_types: List[Dict[str, Any]] = field(default_factory=list)
    recipes: Dict[str, Any] = field(default_factory=dict)
    findings: List[Dict[str, str]] = field(default_factory=list)
    #: 行为化覆盖统计（V2）：{"dispatch": n, "mention": n, "none": n}
    behavioral: Dict[str, int] = field(default_factory=dict)
    #: findings 棘轮判定（不进指纹：waiver expiry 有日期语义）
    ratchet_report: Dict[str, Any] = field(default_factory=dict)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _collect_registry_names() -> Dict[str, List[str]]:
    """一次性收集全部需要做测试引用发现的标识符（按 section 分组）。"""
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    cap_reg = get_capability_registry()
    algo_reg = get_algorithm_registry()
    art_reg = get_artifact_type_registry()

    return {
        "capabilities": sorted(cap_reg.all_ids),
        "algorithms": sorted(algo_reg.all_ids),
        "artifact_types": sorted(art_reg.all_ids),
    }


def _tool_rows(
    tool_registry: Any,
    test_refs: Dict[str, List[str]],
    behavioral: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], List[QualityFinding]]:
    from app.tools.descriptor import SideEffectClass, ToolStatus

    rows: List[Dict[str, Any]] = []
    findings: List[QualityFinding] = []
    descriptors = tool_registry.descriptors()
    for name in sorted(descriptors):
        d = descriptors[name]
        tests = list(test_refs.get(name, []))
        side_effect = getattr(getattr(d, "side_effect", None), "value", str(getattr(d, "side_effect", "")))
        status = getattr(getattr(d, "status", None), "value", str(getattr(d, "status", "")))
        requires_confirmation = bool(getattr(d, "requires_confirmation", False))
        capabilities = list(getattr(d, "capabilities", ()) or ())
        row: Dict[str, Any] = {
            "name": name,
            "status": status,
            "tier": int(getattr(d, "tier", 1)),
            "side_effect": side_effect,
            "execution_policy": str(getattr(d, "execution_policy", "")),
            "capabilities": capabilities,
            "tags": list(getattr(d, "tags", ()) or ()),
            "latency_class": str(getattr(d, "latency_class", "") or ""),
            "memory_class": str(getattr(d, "memory_class", "") or ""),
            "scale_class": str(getattr(d, "scale_class", "") or ""),
            "deterministic": getattr(d, "deterministic", None),
            "requires_confirmation": requires_confirmation,
            "tests": tests,
            "behavior": behavior_level(name, behavioral, tests),
        }
        # ── findings ────────────────────────────────────────────────
        executable = status != ToolStatus.PLANNED.value
        if executable and not tests:
            row["flags"] = ["untested"]
            findings.append(
                QualityFinding(
                    code="TOOL_UNTESTED",
                    severity="high" if requires_confirmation else "medium",
                    subject=name,
                    detail=(
                        "destructive/mutating tool without any test reference"
                        if requires_confirmation
                        else "registered tool without any static test reference"
                    ),
                    surface="tools",
                    domain="tools",
                )
            )
            if requires_confirmation:
                row["flags"].append("destructive_untested")
                findings.append(
                    QualityFinding(
                        code="TOOL_DESTRUCTIVE_UNTESTED",
                        severity="high",
                        subject=name,
                        detail="requires_confirmation tool without any test reference",
                        surface="tools",
                        domain="tools",
                    )
                )
        missing = []
        if side_effect == SideEffectClass.UNCLASSIFIED.value:
            missing.append("side_effect")
        for f in GATE_FIELDS:
            if f != "side_effect" and not row.get(f):
                missing.append(f)
        if missing:
            row.setdefault("flags", []).append("descriptor_incomplete")
            findings.append(
                QualityFinding(
                    code="TOOL_DESCRIPTOR_INCOMPLETE",
                    severity="low",
                    subject=name,
                    detail="missing descriptor fields: " + ",".join(missing),
                    surface="tools",
                    domain="tools",
                )
            )
        rows.append(row)
    return rows, findings


def _algorithm_rows(
    test_refs: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], List[QualityFinding]]:
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    reg = get_algorithm_registry()
    rows: List[Dict[str, Any]] = []
    findings: List[QualityFinding] = []
    for aid in sorted(reg.all_ids):
        algo = reg.get(aid)
        if algo is None:  # pragma: no cover - registry 一致性由自身 validate 保证
            continue
        tests = list(test_refs.get(aid, []))
        conformance = list(algo.conformance_tests or [])
        variants = list(algo.backend_variants or [])
        rows.append(
            {
                "id": aid,
                "category": algo.category,
                "runtime_status": str(algo.runtime_status),
                "scientific_status": str(algo.scientific_status),
                "deterministic": bool(algo.deterministic),
                "random_seed_policy": str(algo.random_seed_policy),
                "memory_cost": str(algo.memory_cost),
                "capabilities": list(algo.capabilities),
                "conformance_tests": conformance,
                "backend_variants": len(variants),
                "uncertainty_outputs": list(algo.uncertainty_outputs or []),
                "tests": tests,
            }
        )
        if not conformance:
            findings.append(
                QualityFinding(
                    code="ALGO_NO_CONFORMANCE",
                    severity="medium",
                    subject=aid,
                    detail="algorithm declares no conformance test node ids",
                    surface="algorithms",
                    domain=aid.split(".")[0],
                )
            )
        if str(algo.memory_cost) == "high" and not variants:
            findings.append(
                QualityFinding(
                    code="ALGO_HEAVY_NO_VARIANTS",
                    severity="medium",
                    subject=aid,
                    detail="memory_cost=high but no backend_variants scale windows",
                    surface="algorithms",
                    domain=aid.split(".")[0],
                )
            )
        if not bool(algo.deterministic) and str(algo.random_seed_policy) == "deterministic":
            findings.append(
                QualityFinding(
                    code="ALGO_SEED_POLICY_CONFLICT",
                    severity="medium",
                    subject=aid,
                    detail="deterministic=False but random_seed_policy=deterministic",
                    surface="algorithms",
                    domain=aid.split(".")[0],
                )
            )
        if (
            str(algo.scientific_status) == "PRODUCTION"
            and not list(algo.uncertainty_outputs or [])
        ):
            findings.append(
                QualityFinding(
                    code="ALGO_PRODUCTION_NO_UNCERTAINTY",
                    severity="medium",
                    subject=aid,
                    detail="scientific_status=PRODUCTION without uncertainty_outputs",
                    surface="algorithms",
                    domain=aid.split(".")[0],
                )
            )
    return rows, findings


def _capability_rows(
    test_refs: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], List[QualityFinding]]:
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    cap_reg = get_capability_registry()
    algo_reg = get_algorithm_registry()
    algo_ids = set(algo_reg.all_ids)
    tool_caps: Dict[str, List[str]] = {}
    for aid in algo_ids:
        algo = algo_reg.get(aid)
        if algo is None:  # pragma: no cover
            continue
        for cap in algo.capabilities:
            tool_caps.setdefault(cap, []).append(aid)

    rows: List[Dict[str, Any]] = []
    findings: List[QualityFinding] = []
    for cid in sorted(cap_reg.all_ids):
        cap = cap_reg.get(cid)
        if cap is None:  # pragma: no cover - registry 一致性由自身 validate 保证
            continue
        tests = list(test_refs.get(cap.id, []))
        producers = sorted(set(tool_caps.get(cap.id, [])))
        has_conformance = any(
            (algo_reg.get(a) is not None and algo_reg.get(a).conformance_tests)
            for a in producers
        )
        rows.append(
            {
                "id": cap.id,
                "status": str(cap.status),
                "category": cap.category,
                "domain": cap.domain,
                "deterministic": bool(cap.deterministic),
                "supports_large_data": bool(cap.supports_large_data),
                "producer_algorithms": producers,
                "tests": tests,
            }
        )
        if str(cap.status) == "native" and not producers:
            findings.append(
                QualityFinding(
                    code="CAPABILITY_NO_PRODUCER",
                    severity="high",
                    subject=cap.id,
                    detail="native capability without algorithm/tool producer",
                    surface="capabilities",
                    domain=cap.domain,
                )
            )
        elif str(cap.status) == "native" and not has_conformance:
            findings.append(
                QualityFinding(
                    code="CAPABILITY_NO_CONFORMANCE",
                    severity="medium",
                    subject=cap.id,
                    detail="native capability whose producers declare no conformance tests",
                    surface="capabilities",
                    domain=cap.domain,
                )
            )
    return rows, findings


def _artifact_rows(
    test_refs: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], List[QualityFinding]]:
    from app.lib.gis.artifacts import get_artifact_type_registry

    reg = get_artifact_type_registry()
    rows: List[Dict[str, Any]] = []
    findings: List[QualityFinding] = []
    for tid in sorted(reg.all_ids):
        tests = list(test_refs.get(tid, []))
        rows.append({"id": tid, "tests": tests})
        if not tests:
            findings.append(
                QualityFinding(
                    code="ARTIFACT_TYPE_UNTESTED",
                    severity="medium",
                    subject=tid,
                    detail="artifact type without any static test reference",
                    surface="artifact_types",
                    domain="artifacts",
                )
            )
    return rows, findings


def _gate_report(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """描述符富化覆盖率（ADR-0103 语义：闸只对非 PLANNED 可执行工具生效）。"""
    executable = [t for t in tools if t["status"] != "planned"]
    fields: Dict[str, Any] = {}
    all_pass = True
    for f in GATE_FIELDS:
        covered = 0
        missing: List[str] = []
        for t in executable:
            value = t.get(f)
            ok = value not in (None, "", (), [], "unclassified") if f == "side_effect" else bool(value)
            if ok:
                covered += 1
            else:
                missing.append(t["name"])
        total = len(executable)
        coverage = round(covered * 100 / total) if total else 0
        threshold = GATE_THRESHOLDS[f]
        passed = coverage >= threshold
        all_pass = all_pass and passed
        fields[f] = {
            "coverage": coverage,
            "threshold": threshold,
            "pass": passed,
            "missing_count": len(missing),
            "missing": sorted(missing)[:20],
        }
    return {
        "total": len(executable),
        "fields": fields,
        "pass": all_pass,
    }


def collect() -> Dict[str, Any]:
    """ADR-0103 历史 CLI/测试入口：返回富化覆盖率报告（gate 消费的形态）。"""
    manifest = compile_quality_manifest()
    return manifest.gate_report


def gate(report: Dict[str, Any]) -> int:
    """0 = 通过；1 = 存在未达阈值的富化字段（富化回退即红）。"""
    if not report.get("fields"):
        return 1
    return 0 if report.get("pass") else 1


# ── Quality V2：findings 棘轮 + waiver ───────────────────────────────────
# 语义：findings 只许下降不许回升（baseline 是每 code 的数量上限）；
# 豁免必须显式登记且带过期日（过期即红）；行为化 dispatch 覆盖数只许
# 上升（min_behavioral_dispatch 下限）。这让"质量债务"成为可执行约束，
# 而不是滚动报告。


def load_findings_baseline(root: Optional[Path] = None) -> Dict[str, Any]:
    base = root if root is not None else repo_root()
    path = base / FINDINGS_BASELINE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_waivers(root: Optional[Path] = None) -> List[Dict[str, str]]:
    base = root if root is not None else repo_root()
    path = base / WAIVERS_PATH
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("waivers", []))


def evaluate_findings_ratchet(
    findings: List[Dict[str, str]],
    behavioral_dispatch: int,
    baseline: Optional[Dict[str, Any]] = None,
    waivers: Optional[List[Dict[str, str]]] = None,
    today: Optional[Any] = None,
) -> Dict[str, Any]:
    """棘轮判定（纯函数；today 便于测试注入，缺省 date.today）。"""
    import datetime

    baseline = baseline or {}
    waivers = waivers or []
    if today is None:
        today = datetime.date.today()
    elif isinstance(today, str):
        today = datetime.date.fromisoformat(today)

    caps = {k: int(v) for k, v in (baseline.get("findings_max") or {}).items()}
    min_behavioral = int(baseline.get("min_behavioral_dispatch") or 0)

    waived_keys = {(w.get("code", ""), w.get("subject", "")) for w in waivers}
    expired: List[Dict[str, str]] = []
    active: List[Dict[str, str]] = []
    for w in waivers:
        try:
            exp = datetime.date.fromisoformat(str(w.get("expires", "")))
        except ValueError:
            exp = datetime.date(1970, 1, 1)  # 缺失/非法 expires → 视为已过期
        (active if exp >= today else expired).append(w)

    current: Dict[str, int] = {}
    waived_count = 0
    for f in findings:
        code = f["code"]
        if (code, f.get("subject", "")) in waived_keys:
            waived_count += 1
            continue  # 豁免项不计入棘轮计数（但仍在 findings 全量清单中）
        current[code] = current.get(code, 0) + 1

    violations: List[Dict[str, Any]] = []
    for code in sorted(set(current) | set(caps)):
        now = current.get(code, 0)
        cap = caps.get(code)
        if cap is None:
            # 新 code 未登记 → 视为上限 0（防止静默新增债务类别）
            violations.append({"code": code, "baseline": 0, "current": now})
        elif now > cap:
            violations.append({"code": code, "baseline": cap, "current": now})

    behavioral_ok = behavioral_dispatch >= min_behavioral
    return {
        "pass": not violations and not expired and behavioral_ok,
        "violations": violations,
        "expired_waivers": [
            {"code": w.get("code", ""), "subject": w.get("subject", ""),
             "expires": str(w.get("expires", ""))}
            for w in expired
        ],
        "waivers_active": len(active),
        "waived_current": waived_count,
        "behavioral_dispatch": behavioral_dispatch,
        "min_behavioral_dispatch": min_behavioral,
    }


def compile_quality_manifest(
    tool_registry: Optional[Any] = None,
    root: Optional[Path] = None,
) -> QualityManifest:
    """编译 QualityManifest（纯只读投影；同一提交结果字节一致）。"""
    if tool_registry is None:
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        tool_registry = ToolRegistry()
        init_tools(tool_registry)
    base = root if root is not None else repo_root()

    names = _collect_registry_names()
    tool_names = sorted(tool_registry.descriptors().keys())
    refs = discover_test_references(
        tool_names + names["capabilities"] + names["algorithms"] + names["artifact_types"],
        root=base,
    )
    behavioral = discover_behavioral_dispatch(root=base)

    tools, f_tools = _tool_rows(tool_registry, refs, behavioral)
    algorithms, f_algos = _algorithm_rows(refs)
    capabilities, f_caps = _capability_rows(refs)
    artifact_types, f_arts = _artifact_rows(refs)

    from app.services.gis_harness.recipes import get_recipe_registry

    recipe_reg = get_recipe_registry()
    domains = sorted(recipe_reg.domains())
    recipes = {
        "count": int(recipe_reg.count),
        "domains": domains,
    }

    findings = sorted(
        [*f_tools, *f_algos, *f_caps, *f_arts],
        key=lambda f: (f.code, f.subject),
    )
    counts = {
        "tools": len(tools),
        "algorithms": len(algorithms),
        "capabilities": len(capabilities),
        "artifact_types": len(artifact_types),
        "recipes": recipes["count"],
        "findings": len(findings),
    }
    gate_report = _gate_report(tools)

    behavioral_stats = {"dispatch": 0, "mention": 0, "none": 0}
    for t in tools:
        behavioral_stats[t["behavior"]] = behavioral_stats.get(t["behavior"], 0) + 1

    # 棘轮判定（不进指纹：baseline/waiver 有日期语义；失败信息进渲染物）
    ratchet = evaluate_findings_ratchet(
        [f.to_dict() for f in findings],
        behavioral_stats["dispatch"],
        baseline=load_findings_baseline(base),
        waivers=load_waivers(base),
    )

    fingerprint_payload = {
        "manifest_version": MANIFEST_VERSION,
        "counts": counts,
        "tools": tools,
        "algorithms": algorithms,
        "capabilities": capabilities,
        "artifact_types": artifact_types,
        "recipes": recipes,
        "findings": [f.to_dict() for f in findings],
        "gate": {f: gate_report["fields"][f]["coverage"] for f in GATE_FIELDS},
    }
    return QualityManifest(
        fingerprint=_fingerprint(fingerprint_payload),
        counts=counts,
        gate_report=gate_report,
        tools=tools,
        algorithms=algorithms,
        capabilities=capabilities,
        artifact_types=artifact_types,
        recipes=recipes,
        findings=[f.to_dict() for f in findings],
        behavioral=behavioral_stats,
        ratchet_report=ratchet,
    )


# ── 渲染（MD 人可读有界 / JSON 机器可读全量）─────────────────────────────


def render_json(manifest: QualityManifest) -> str:
    payload = {
        "manifest_version": manifest.manifest_version,
        "fingerprint": manifest.fingerprint,
        "counts": manifest.counts,
        "gate": manifest.gate_report,
        "behavioral": manifest.behavioral,
        "findings_ratchet": manifest.ratchet_report,
        "tools": manifest.tools,
        "algorithms": manifest.algorithms,
        "capabilities": manifest.capabilities,
        "artifact_types": manifest.artifact_types,
        "recipes": manifest.recipes,
        "findings": manifest.findings,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_markdown(manifest: QualityManifest) -> str:
    lines: List[str] = []
    lines.append("# Quality Manifest（自动生成）")
    lines.append("")
    lines.append(
        "> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与"
        "测试引用索引派生，请勿手改。唯一事实源：ToolRegistry /"
        " AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry /"
        " RecipeRegistry 与 tests/ 源码本身。"
    )
    lines.append("")
    lines.append(f"- Manifest 版本：**{manifest.manifest_version}**")
    lines.append(f"- 内容指纹：`{manifest.fingerprint[:16]}…`")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| section | total | 静态测试引用 | findings |")
    lines.append("|---|---|---|---|")
    lines.append(f"| tools | {manifest.counts['tools']} | {_tested_count(manifest.tools)} | {_finding_count(manifest.findings, ('TOOL_UNTESTED', 'TOOL_DESTRUCTIVE_UNTESTED', 'TOOL_DESCRIPTOR_INCOMPLETE'))} |")
    lines.append(f"| algorithms | {manifest.counts['algorithms']} | {_tested_count(manifest.algorithms)} | {_finding_count(manifest.findings, ('ALGO_NO_CONFORMANCE', 'ALGO_HEAVY_NO_VARIANTS', 'ALGO_SEED_POLICY_CONFLICT', 'ALGO_PRODUCTION_NO_UNCERTAINTY'))} |")
    lines.append(f"| capabilities | {manifest.counts['capabilities']} | {_tested_count(manifest.capabilities)} | {_finding_count(manifest.findings, ('CAPABILITY_NO_PRODUCER', 'CAPABILITY_NO_CONFORMANCE'))} |")
    lines.append(f"| artifact_types | {manifest.counts['artifact_types']} | {_tested_count(manifest.artifact_types)} | {_finding_count(manifest.findings, ('ARTIFACT_TYPE_UNTESTED',))} |")
    lines.append(f"| recipes | {manifest.counts['recipes']} | {manifest.recipes['count']}（conformance 由 workflow 闸保护） | 0 |")
    lines.append("")

    lines.append("## 描述符富化闸（ADR-0103，可执行工具）")
    lines.append("")
    gate_report = manifest.gate_report
    verdict = "PASS" if gate_report["pass"] else "FAIL"
    lines.append("| field | coverage | threshold | status |")
    lines.append("|---|---|---|---|")
    for f in GATE_FIELDS:
        fd = gate_report["fields"][f]
        lines.append(
            f"| {f} | {fd['coverage']}% | {fd['threshold']}% | "
            f"{'PASS' if fd['pass'] else 'FAIL'}（缺 {fd['missing_count']}） |"
        )
    lines.append("")
    lines.append(f"**gate: {verdict}**（`total={gate_report['total']}`）")
    lines.append("")

    lines.append("## 行为化覆盖与 findings 棘轮（Quality V2）")
    lines.append("")
    b = manifest.behavioral
    total_tools = sum(b.values()) or 1
    lines.append(
        f"- 工具行为证据：dispatch **{b.get('dispatch', 0)}** / mention "
        f"{b.get('mention', 0)} / none {b.get('none', 0)}"
        f"（dispatch 覆盖率 {round(b.get('dispatch', 0) * 100 / total_tools)}%）"
    )
    r = manifest.ratchet_report
    ratchet_verdict = "PASS" if r.get("pass") else "FAIL"
    lines.append(
        f"- findings 棘轮：**{ratchet_verdict}**"
        f"（dispatch 下限 {r.get('min_behavioral_dispatch', 0)}，"
        f"当前 {r.get('behavioral_dispatch', 0)}；"
        f"active waivers {r.get('waivers_active', 0)}，"
        f"过期 {len(r.get('expired_waivers', []))}）"
    )
    for v in r.get("violations", []):
        lines.append(
            f"  - 违规：`{v['code']}` 当前 {v['current']} > 基线 {v['baseline']}"
        )
    for w in r.get("expired_waivers", []):
        lines.append(f"  - waiver 过期：`{w['code']}` `{w['subject']}`（{w['expires']}）")
    lines.append("")

    lines.append("## Findings（派生线索，非缺陷判定）")
    lines.append("")
    lines.append(
        "> 静态引用 ≠ 行为覆盖。findings 只回答\"哪里没有任何测试证据\"，"
        "修复优先级需结合 02-coverage-risk-map 的风险分级。"
    )
    lines.append("")
    by_code: Dict[str, List[Dict[str, str]]] = {}
    for f in manifest.findings:
        by_code.setdefault(f["code"], []).append(f)
    for code in FINDING_CODES:
        items = by_code.get(code, [])
        lines.append(f"### {code}（{len(items)}）")
        lines.append("")
        if not items:
            lines.append("（无）")
            lines.append("")
            continue
        for item in items[:MD_FINDINGS_LIMIT]:
            lines.append(f"- `{item['subject']}`（{item['severity']}）— {item['detail']}")
        if len(items) > MD_FINDINGS_LIMIT:
            lines.append(f"- …另有 {len(items) - MD_FINDINGS_LIMIT} 条，见 quality-manifest.json")
        lines.append("")

    lines.append("## 已知边界")
    lines.append("")
    lines.append("- 本清单只做静态引用发现；行为正确性由各 lane（unit / oracle replay /")
    lines.append("  cartography closed-loop / perf / chaos）保证，见 docs/quality/。")
    lines.append("- recipes 的行为闸由 workflow conformance（test_workflow_guards）与")
    lines.append("  gen_workflow_catalog 字节一致性闸保护，此处不重复展开。")
    lines.append("")
    return "\n".join(lines)


def _tested_count(rows: List[Dict[str, Any]]) -> int:
    return sum(1 for r in rows if r.get("tests"))


def _finding_count(findings: List[Dict[str, str]], codes: Tuple[str, ...]) -> int:
    return sum(1 for f in findings if f["code"] in codes)
