#!/usr/bin/env python3
"""重新生成 docs/science/CAPABILITY_CATALOG.md（ADR-0181）。

能力图的生成目录投影（C8）：capability 词表 + provider 面 + 结构审计
发现（C0：孤儿能力 / fallback 环 / 不可达工具 / 无消费者 artifact /
弃用暴露）。事实源（唯一真相，目录只是投影）：

- app/lib/gis/capability_registry.py + capabilities/ 域包（capability 词表）
- app/services/gis_harness/capability_graph.py（派生图 + 结构审计）
- app/services/gis_harness/recipes.py / product_templates.py、
  app/lib/cartography/component_registry.py、
  app/services/data_fabric/registry.py（provider 四段事实源）

用法：``python scripts/gen_capability_catalog.py``（工作树根目录执行）。
输出确定性：同注册表状态必产生字节相同的文档（CI diff 校验；
无时间戳 —— ADR-0118 同款纪律）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lib.gis.capability_registry import get_capability_registry  # noqa: E402
from app.services.gis_harness.capability_graph import (  # noqa: E402
    KIND_CAPABILITY,
    get_capability_graph,
    validate_graph,
)

HEADER = """# 能力目录（自动生成 · ADR-0181）

> **本文件由能力图派生，请勿手工编辑。** 事实源：
> `app/lib/gis/capability_registry.py`（capability 词表）、
> `app/services/gis_harness/capability_graph.py`（派生图 + 结构审计）、
> recipes / product_templates / component_registry / data_fabric
> （provider 四段）。再生成：`python scripts/gen_capability_catalog.py`。
> 治理语义：本目录披露 warning 级结构发现（孤儿 / 环 / 不可达 /
> 无消费者 / 弃用暴露）——「先可观测，再逐段收紧」；error 级闸仍在
> `registry_validation.validate_gis_library`（零容忍）。
"""


def main() -> int:
    caps = get_capability_registry()
    graph = get_capability_graph()
    issues = validate_graph(graph)

    lines: list[str] = [HEADER.rstrip(), ""]

    # ── 总览 ──────────────────────────────────────────────────────────
    by_domain: Counter = Counter()
    for node in graph.nodes_by_kind(KIND_CAPABILITY):
        by_domain[str(node.extras.get("domain", ""))] += 1
    provider_counts = {
        "workflow(recipes)": len(graph.nodes_by_kind("workflow")),
        "template": len(graph.nodes_by_kind("template")),
        "component": len(graph.nodes_by_kind("component")),
        "provider(adapters)": len(graph.nodes_by_kind("provider")),
        "tool": len(graph.nodes_by_kind("tool")),
        "model": len(graph.nodes_by_kind("model")),
        "algorithm": len(graph.nodes_by_kind("algorithm")),
    }
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- capability 词表：{caps.count} 条"
                 f"（图内 {len(graph.nodes_by_kind(KIND_CAPABILITY))} 节点）")
    lines.append("- 图节点 {} / 边 {} / graph fingerprint `{}`".format(
        graph.node_count, graph.edge_count, graph.fingerprint()[:16]))
    lines.append("- provider 面：" + "，".join(
        f"{k} {v}" for k, v in sorted(provider_counts.items())))
    lines.append("- 域分布：" + "，".join(
        f"`{k}` {v}" for k, v in sorted(by_domain.items())))
    lines.append("")

    # ── capability 明细（id 字典序；有界字段）────────────────────────
    lines.append("## Capability 词表")
    lines.append("")
    lines.append("| id | domain | category | status | offline | deterministic "
                 "| fallbacks | incompatible |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for cap_id in caps.all_ids:
        d = caps.get(cap_id)
        if d is None:
            continue
        offline = ("—" if d.offline_capable is None
                   else ("yes" if d.offline_capable else "no"))
        fb = ",".join(d.fallback_capabilities[:3]) or "—"
        inc = ",".join(d.incompatible_with[:3]) or "—"
        lines.append(
            f"| `{cap_id}` | {d.domain} | {d.category} | {d.status} "
            f"| {offline} | {'yes' if d.deterministic else 'no'} "
            f"| {fb} | {inc} |")
    lines.append("")

    # ── 结构审计发现（C0 治理披露）───────────────────────────────────
    audit_codes = (
        "orphan_capability", "cycle_detected", "unreachable_tool",
        "artifact_no_consumer", "exposes_deprecated_tool",
    )
    lines.append("## 结构审计发现（warning 级 · 治理清单）")
    lines.append("")
    counts = Counter(i.code for i in issues if i.code in audit_codes)
    if not counts:
        lines.append("无发现（全部能力可达且有 provider 路径）。")
    else:
        lines.append("| " + " | ".join(audit_codes) + " |")
        lines.append("| " + " | ".join(["---"] * len(audit_codes)) + " |")
        lines.append("| " + " | ".join(str(counts.get(c, 0))
                                       for c in audit_codes) + " |")
        lines.append("")
        for code in audit_codes:
            found = [i for i in issues if i.code == code]
            if not found:
                continue
            lines.append(f"### {code}（{len(found)}）")
            lines.append("")
            for issue in found:
                lines.append(f"- {issue.detail}")
        extra = [i for i in issues
                 if i.severity == "warning" and i.code not in audit_codes]
        if extra:
            lines.append(f"### 其他 warning（{len(extra)}）")
            lines.append("")
            for issue in extra[:8]:
                lines.append(f"- **{issue.code}**: {issue.detail}")
    lines.append("")

    out = ROOT / "docs" / "science" / "CAPABILITY_CATALOG.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
