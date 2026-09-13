"""Recipe eligibility / fallback 覆盖审计（ADR-0151 P0 勘察 + 定期审计）。

只读脚本：dump 全部 recipe 的资格检查与 fallback 声明矩阵（CSV），并输出
覆盖率统计。不放行任何写操作；registry 加载失败 fail loud（与
load_builtins 的知识库完整性语义一致）。

用法：
    python scripts/recipe_eligibility_audit.py [--csv PATH]

输出列：
    recipe_id | source(seed/pack:module) | domain | intent_tasks |
    required_geometry | min_points | requires_fields | primary_cartography |
    secondary_cartography | n_fallback_decls | fallback_reason_codes |
    auto_generated(任一声明携带) | n_eligibility_rules | rule_elements
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

# 保证从仓库根以任意 CWD 运行都能 import app（与 pytest.ini pythonpath=. 同语义）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _source_of(recipe_id: str) -> str:
    from app.services.gis_harness.recipes import SEED_RECIPES

    if any(r.id == recipe_id for r in SEED_RECIPES):
        return "seed"
    from app.services.gis_harness.recipe_packs import PACK_MODULES, _BASE

    import importlib

    for module_name in PACK_MODULES:
        module = importlib.import_module(_BASE + module_name)
        for r in getattr(module, "RECIPES", []):
            if r.id == recipe_id:
                return f"pack:{module_name}"
    return "unknown"


def _fmt_list(values) -> str:
    return ";".join(str(v) for v in (values or []))


def _rule_min_points(recipe) -> str:
    """元素级最小点数阈值（None=check_points 语义用注入默认值）。"""
    values = []
    for rule in recipe.eligibility:
        if rule.check_points or rule.min_points is not None:
            values.append(
                f"{rule.element}:{rule.min_points if rule.min_points is not None else 'default'}"
            )
    return ";".join(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", default="docs/dev/ac-02-recipe-matrix.csv",
        help="输出 CSV 路径（默认 docs/dev/ac-02-recipe-matrix.csv）",
    )
    args = parser.parse_args()

    from app.services.gis_harness.recipes import get_recipe_registry

    registry = get_recipe_registry()
    recipes = [registry.get(rid) for rid in registry.all_ids]
    recipes = [r for r in recipes if r is not None]

    rows = []
    for r in recipes:
        wf = getattr(r, "workflow", None)
        fb = list(getattr(r, "fallbacks", None) or [])
        rows.append({
            "recipe_id": r.id,
            "source": _source_of(r.id),
            "domain": (wf.domain if wf else "") or "",
            "intent_tasks": _fmt_list(r.intent_tasks),
            "required_geometry": _fmt_list(r.required_geometry),
            "allowed_geometry": _fmt_list(r.allowed_geometry),
            "min_points_rules": _rule_min_points(r),
            "requires_fields": _fmt_list(r.required_fields),
            "primary_cartography": r.primary_cartography,
            "secondary_cartography": _fmt_list(r.secondary_cartography),
            "n_eligibility_rules": len(r.eligibility),
            "rule_elements": _fmt_list(rule.element for rule in r.eligibility),
            "n_fallback_decls": len(fb),
            "fallback_reason_codes": _fmt_list(f.reason_code for f in fb),
            "fallback_targets": _fmt_list(f.use for f in fb),
            "fallback_auto_generated": "yes" if any(
                getattr(f, "auto_generated", False) for f in fb
            ) else "no",
            "priority": r.priority,
        })

    out_path = _REPO_ROOT / args.csv
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    out_path.write_text(buf.getvalue(), encoding="utf-8")

    # ── 覆盖率统计 ────────────────────────────────────────────────────
    total = len(rows)
    with_fb = sum(1 for r in rows if r["n_fallback_decls"] > 0)
    by_source: dict = {}
    for r in rows:
        src = r["source"].split(":")[0]
        stat = by_source.setdefault(src, {"total": 0, "with_fb": 0})
        stat["total"] += 1
        stat["with_fb"] += 1 if r["n_fallback_decls"] > 0 else 0

    print(f"total_recipes={total}")
    print(f"with_fallback_decls={with_fb}")
    print(f"coverage={with_fb / total:.1%}")
    for src, stat in sorted(by_source.items()):
        print(f"  {src}: {stat['with_fb']}/{stat['total']}")
    print(f"csv={out_path}")

    # 领域分布（pack 模块粒度）
    domains: dict = {}
    for r in rows:
        if r["source"].startswith("pack:"):
            mod = r["source"].split(":", 1)[1]
            domains[mod] = domains.get(mod, 0) + 1
    print("pack_modules=" + ",".join(f"{k}({v})" for k, v in sorted(domains.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
