#!/usr/bin/env python3
"""重新生成 docs/science/ALGORITHM_CATALOG.md（ADR-0099 §63）。

事实源（唯一真相，目录只是投影）：
- app/lib/gis/capabilities/  → 能力（capability-major 排序，按 id 字典序）
- app/lib/gis/algorithms/    → 算法（能力内按 (priority, id) 排序，与
                               AlgorithmRegistry._by_capability 索引一致）
- 各域包 PARAMETER_CONTRACTS → 参数契约（只计数）

用法：``python scripts/gen_science_catalog.py``（工作树根目录执行）。
输出是确定性的：同注册表状态必产生字节相同的文档（CI 可 diff 校验）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lib.gis.algorithm_registry import get_algorithm_registry  # noqa: E402
from app.lib.gis.capability_registry import get_capability_registry  # noqa: E402
from app.lib.gis.parameter_contracts import get_parameter_contract_registry  # noqa: E402

HEADER = """# 算法目录（自动生成 · ADR-0099 §63）

> **本文件由注册表生成，请勿手工编辑。** 事实源：
> `app/lib/gis/algorithms/`（算法）、`app/lib/gis/capabilities/`（能力）、
> 各域包 `PARAMETER_CONTRACTS`（参数契约）。
> 再生成：`python scripts/gen_science_catalog.py`。
"""

_STATUS_LABEL = {
    "PRODUCTION": "生产",
    "VALIDATED": "已验证",
    "EXPERIMENTAL": "实验",
}


def _maturity(scientific_status: str) -> str:
    return _STATUS_LABEL.get(scientific_status, "—")


def _join_bounded(items: list[str], limit: int = 3) -> str:
    return "；".join(str(x) for x in items[:limit])


def _algorithm_bullet(algo) -> str:
    parts = [f"- **`{algo.id}`** {algo.name}（`{algo.runtime_status}`·成熟度 "
             f"{_maturity(algo.scientific_status)}"]
    if algo.parameter_contract_ref:
        parts.append(f"，契约: `{algo.parameter_contract_ref}`")
    if algo.method_references:
        refs = ", ".join(f"`{r}`" for r in algo.method_references)
        parts.append(f"，出处: {refs}")
    parts.append("）")
    lines = ["".join(parts)]
    if algo.assumptions:
        lines.append(f"  - 假设：{_join_bounded(algo.assumptions)}")
    if algo.limitations:
        lines.append(f"  - 局限：{_join_bounded(algo.limitations)}")
    for target in algo.fallback_algorithms:
        semantics = algo.fallback_semantics.get(target, "")
        lines.append(f"  - 回退：`{target}`→{semantics}")
    return "\n".join(lines)


def generate() -> str:
    caps = get_capability_registry()
    algos = get_algorithm_registry()
    contracts = get_parameter_contract_registry()

    stats_line = (f"统计：{caps.count} 能力 · {algos.count} 算法 · "
                  f"{contracts.count} 参数契约。")

    sections: list[str] = [HEADER, stats_line, ""]
    for cap_id in sorted(caps.all_ids):
        cap = caps.get(cap_id)
        sections.append(f"## `{cap.id}` — {cap.name}")
        sections.append("")
        if cap.description:
            sections.append(cap.description)
            sections.append("")
        cap_alg = algos.algorithms_for_capability(cap_id)
        for algo in cap_alg:
            sections.append(_algorithm_bullet(algo))
        if cap_alg:
            sections.append("")
    return "\n".join(sections).rstrip("\n") + "\n"


def main() -> int:
    target = ROOT / "docs" / "science" / "ALGORITHM_CATALOG.md"
    content = generate()
    target.write_text(content, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
