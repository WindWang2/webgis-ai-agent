#!/usr/bin/env python3
"""ExecutionCatalog docs generator — 由 catalog 生成执行目录文档（F07）。

单一真值是权威 registry（capability / algorithm / tool / recipe）；本模块
把 ExecutionCatalog 只读投影渲染为 ``docs/catalog/execution-catalog/`` 下的
**生成产物**（含机器可读 manifest JSON）：

    python -m app.lib.gis.execution_catalog_docs          # 生成
    python -m app.lib.gis.execution_catalog_docs --check  # 漂移检查（CI/测试）

禁止手改生成文件 —— 修改请进入对应 registry/域包，再重新生成。
生成内容**不含时钟**（compiled_at 不参与），同 registry 内容跨机器逐字节
稳定（漂移测试依赖此性质）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "docs" / "catalog" / "execution-catalog"

#: 单次 generate() 内共享一次编译 + 一次 conformance（6 个 generator 否则
#: 各自全量重编译 —— review P2-9）。generate() 入口清空。
_BUILD: dict = {}


def _catalog():
    if "catalog" not in _BUILD:
        from app.lib.gis.execution_catalog import compile_execution_catalog

        _BUILD["catalog"] = compile_execution_catalog()
    return _BUILD["catalog"]


def _issues():
    if "issues" not in _BUILD:
        from app.lib.gis.execution_catalog_conformance import (
            conformance_summary,
            validate_execution_catalog_conformance,
        )

        issues = validate_execution_catalog_conformance(_catalog())
        _BUILD["issues"] = issues
        _BUILD["summary"] = conformance_summary(issues)
    return _BUILD["issues"], _BUILD["summary"]


def _summary_md() -> str:
    catalog = _catalog()
    _, summary = _issues()
    s = catalog.summary()
    lines = [
        "# Execution Catalog Summary",
        "",
        "> 由 `app.lib.gis.execution_catalog_docs` 从权威 registry 投影生成；",
        "> 手改无效。真值：CapabilityRegistry / AlgorithmRegistry /",
        "> ToolRegistry / RecipeRegistry（F07：docs/dev/f07-execution-catalog-design.md）。",
        "",
        f"- catalog_version: {s['catalog_version']}",
        f"- generation_fingerprint: `{s['generation_fingerprint']}`",
        f"- 条目：capability {s['counts']['capability']} / "
        f"algorithm {s['counts']['algorithm']} / tool {s['counts']['tool']} / "
        f"recipe {s['counts']['recipe']}",
        f"- deprecated 条目：{s['deprecated_entries']}",
        f"- provider 分布：{json.dumps(s['providers'], ensure_ascii=False, sort_keys=True)}",
        f"- conformance：fatal {summary['by_severity'].get('fatal', 0)} / "
        f"warning {summary['by_severity'].get('warning', 0)}"
        f"（truncate={summary['truncated']}）",
        "",
        "## conformance 码分布",
        "",
    ]
    for code, n in summary["by_code"].items():
        lines.append(f"- `{code}`: {n}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _capabilities_md() -> str:
    catalog = _catalog()
    lines = [
        "# Execution Catalog · Capability Chains",
        "",
        "> capability → algorithm（priority 序）→ 已注册工具候选；",
        "> 只列 native 实现链；planned/unavailable 如实缺席。",
        "",
    ]
    for cap in catalog.entries_of_kind("capability"):
        head = f"## {cap.id}（{cap.name}）"
        meta = [f"- status: {cap.status}；version: {cap.version}"]
        if cap.input_semantic_types:
            meta.append(f"- input: {', '.join(cap.input_semantic_types)}")
        if cap.output_semantic_types:
            meta.append(f"- output: {', '.join(cap.output_semantic_types)}")
        if cap.geometry_requirements:
            meta.append(f"- geometry: {', '.join(cap.geometry_requirements)}")
        fallback = cap.fallback_targets
        if fallback:
            meta.append(f"- fallback: {', '.join(fallback)}")
        lines.append(head)
        lines.extend(meta)
        algos = [a for a in catalog.entries_of_kind("algorithm")
                 if cap.id in a.capabilities
                 and a.status not in ("planned", "unavailable")]
        if not algos:
            lines.append("- （无 native 算法实现）")
        for a in algos:
            cand = [str(t) for t in a.detail.get("tool_candidates", ())]
            sci = a.detail.get("scientific_status", "")
            sci_tag = f"；scientific={sci}" if sci else ""
            dep = "；DEPRECATED" if a.deprecated else ""
            lines.append(
                f"- algorithm `{a.id}`（priority={a.priority}{sci_tag}{dep}）"
                f"→ tools: {', '.join(cand) or '（无候选）'}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _tools_md() -> str:
    catalog = _catalog()
    lines = [
        "# Execution Catalog · Tools",
        "",
        "> 工具执行契约面（有界投影；完整描述符见 ToolRegistry）。",
        "",
        "| tool | v | status | side_effect | latency | memory | scale | capabilities |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in catalog.entries_of_kind("tool"):
        rc = t.resource_class
        lines.append(
            f"| {t.id} | {t.version}#cv{t.contract_version} | {t.status} | "
            f"{t.side_effect or '—'} | {rc.get('latency', '—')} | "
            f"{rc.get('memory', '—')} | {rc.get('scale', '—')} | "
            f"{', '.join(t.capabilities) or '—'} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _recipes_md() -> str:
    catalog = _catalog()
    lines = [
        "# Execution Catalog · Recipes",
        "",
        "> recipe 的 capability 引用与降级链（capability-first，非工具名）。",
        "",
    ]
    for r in catalog.entries_of_kind("recipe"):
        lines.append(f"## {r.id}（{r.name}）")
        lines.append(
            f"- capabilities: {', '.join(r.capabilities) or '—'}"
            + (f"；optional: {', '.join(r.detail.get('optional_analysis', ()))}"
               if r.detail.get("optional_analysis") else ""))
        if r.geometry_requirements:
            lines.append(f"- geometry: {', '.join(r.geometry_requirements)}")
        if r.temporal_constraints:
            lines.append(
                f"- temporal: {', '.join(r.temporal_constraints)}")
        if r.crs_class:
            lines.append(f"- crs: {r.crs_class}")
        if r.fallback_targets:
            lines.append(f"- fallback_links: {', '.join(r.fallback_targets)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _deprecations_md() -> str:
    from app.lib.gis.execution_catalog_staleness import supersession_map

    catalog = _catalog()
    sup = supersession_map(catalog)
    deprecated_tools = [t for t in catalog.entries_of_kind("tool") if t.deprecated]
    deprecated_algos = [a for a in catalog.entries_of_kind("algorithm") if a.deprecated]
    lines = [
        "# Execution Catalog · Deprecations & Supersession",
        "",
        "> 弃用条目及其替代者（诚实登记；空表 = 当前无弃用）。",
        "",
        f"- deprecated tools: {len(deprecated_tools)}",
        f"- deprecated algorithms: {len(deprecated_algos)}",
        "",
    ]
    if not sup:
        lines.append("（当前无弃用条目）")
        lines.append("")
    for key, info in sup.items():
        known = "✓" if info.get("successor_known") else "✗（后继不在目录）"
        lines.append(
            f"- `{key}` → `{info.get('successor') or '（未声明）'}` {known}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _manifest_json() -> str:
    """机器可读 manifest（有界、canonical 排序；审计/对账用）。"""
    from app.lib.gis.execution_catalog_staleness import supersession_map

    catalog = _catalog()
    issues, _ = _issues()
    payload = {
        "manifest_schema_version": 1,
        "catalog_version": catalog.catalog_version,
        "generation_fingerprint": catalog.generation_fingerprint,
        "counts": catalog.counts(),
        "providers": catalog.summary()["providers"],
        "deprecated_entries": catalog.summary()["deprecated_entries"],
        "conformance": {
            "fatal": sum(1 for i in issues if i.severity == "fatal"),
            "warning": sum(1 for i in issues if i.severity == "warning"),
            "codes": {
                code: sum(1 for i in issues if i.code == code)
                for code in sorted({i.code for i in issues})
            },
        },
        "supersession": supersession_map(catalog),
        "entries": [
            {
                "kind": e.kind,
                "id": e.id,
                "version": e.version,
                "status": e.status,
                "provider": e.provider,
                "deprecated": e.deprecated,
                "fingerprint": e.fingerprint,
            }
            for e in sorted(catalog.entries.values(), key=lambda x: (x.kind, x.id))
        ],
    }
    from app.tools.descriptor import canonical_json

    return canonical_json(payload) + "\n"


GENERATORS = {
    "summary.md": _summary_md,
    "capability-chains.md": _capabilities_md,
    "tools.md": _tools_md,
    "recipes.md": _recipes_md,
    "deprecations.md": _deprecations_md,
    "execution-catalog.manifest.json": _manifest_json,
}


def generate() -> dict:
    _BUILD.clear()
    try:
        return {name: fn() for name, fn in GENERATORS.items()}
    finally:
        _BUILD.clear()


def main(argv: list) -> int:
    check = "--check" in argv
    docs = generate()
    drift = []
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in docs.items():
        path = DOCS_DIR / name
        if check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                drift.append(name)
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path}")
    if check:
        if drift:
            print("DRIFT: " + ", ".join(drift))
            print("re-run: python -m app.lib.gis.execution_catalog_docs")
            return 1
        print("execution catalog docs up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
