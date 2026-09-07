#!/usr/bin/env python
"""从 registry 生成 Workflow Catalog 文档（docs/workflows/workflow-catalog.md）。

catalog 是派生物：唯一事实源是 RecipeRegistry（17 个 V1 seed + 24 个领域包）。
registry 语义变化后重新运行本脚本即可（CI 可校验 catalog 是否过期）。

用法：
    python scripts/gen_workflow_catalog.py            # 写入默认路径
    python scripts/gen_workflow_catalog.py --check    # 仅校验（过期则退出 1）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUT = Path("docs/workflows/workflow-catalog.md")


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_catalog() -> str:
    from app.services.gis_harness.recipe_packs import PACK_MODULES
    from app.services.gis_harness.recipes import get_recipe_registry
    from app.services.gis_harness.workflow_schema import recipe_content_fingerprint

    registry = get_recipe_registry()
    lines: list[str] = []
    lines.append("# Workflow Catalog（自动生成）")
    lines.append("")
    lines.append("> 本文件由 `python scripts/gen_workflow_catalog.py` 从 RecipeRegistry 生成，")
    lines.append("> 请勿手改。唯一事实源：`app/services/gis_harness/recipes.py`（V1 seeds）与")
    lines.append("> `app/services/gis_harness/recipe_packs/`（领域包）。")
    lines.append("")
    lines.append(f"- Registry 总量：**{registry.count}**（V1 seeds 17 + 领域包 {registry.count - 17}）")
    lines.append(f"- 领域包：**{len(registry.domains())}** 个")
    lines.append(f"- Registry 内容指纹：`{registry.content_fingerprint()[:16]}…`")
    lines.append("")

    # ── V3 分层组合总览（family / composite / scenario）───────────────
    from app.services.gis_harness.workflow_families import (
        get_workflow_family_registry,
    )
    layer = get_workflow_family_registry()
    lines.append("## V3 分层组合总览")
    lines.append("")
    lines.append(f"- Workflow Family（从 registry 派生）：**{layer.family_count}**")
    lines.append(f"- Composite Recipe（跨族组合）：**{layer.composite_count}**")
    lines.append(f"- Scenario Template（场景模板）：**{layer.scenario_count}**")
    lines.append(f"- 分层实体合计：**{registry.count + layer.family_count + layer.composite_count + layer.scenario_count}**"
                 "（覆盖靠组合生成，不是平铺复制）")
    lines.append(f"- 分层指纹：`{layer.fingerprint()[:16]}…`")
    lines.append("")

    lines.append("## Workflow Families（派生投影）")
    lines.append("")
    lines.append("> family 是 RecipeRegistry 的确定性投影：按 (domain, workflow_family)"
                 " 聚簇；V1 seed 自成单成员 family。成员变化 → family 自动变化。")
    lines.append("")
    for fam in layer.families:
        onto = ", ".join(f"`{t}`" for t in fam.ontology_tasks[:4]) or "—"
        members = ", ".join(f"`{m}`" for m in fam.member_recipe_ids[:4])
        lines.append(f"- `{fam.family_id}` — 成员 {len(fam.member_recipe_ids)} 个：{members}；"
                     f"本体任务：{onto}")
    lines.append("")

    lines.append("## Composite Recipes（组合产品）")
    lines.append("")
    for c in layer.composites:
        support = ", ".join(f"`{s}`" for s in c.supporting_recipe_ids) or "—"
        trig = ", ".join(f"`{t}`" for t in c.trigger_ontology_tasks[:3]) or "—"
        lines.append(f"- `{c.composite_id}` — {c.label_zh}：base `{c.base_recipe_id}`"
                     f" + supporting [{support}]；触发：{trig}")
        if c.disclosures:
            lines.append(f"  - 披露：{'；'.join(c.disclosures)}")
    lines.append("")

    lines.append("## Scenario Templates（场景模板）")
    lines.append("")
    for s in layer.scenarios:
        subjects = "、".join(s.match_subjects[:6]) or "—"
        candidates = " → ".join(f"`{r}`" for r in s.candidate_recipes[:4]) or "—"
        lines.append(f"- `{s.scenario_id}` — {s.label_zh}（主体：{subjects}）")
        lines.append(f"  - 制图候选（数据资格裁决取位）：{candidates}")
        lines.append(f"  - minimal 兜底：{s.minimal_disclosure}")
    lines.append("")
    lines.append("## 领域总览")
    lines.append("")
    lines.append("| 领域 | recipe 数 |")
    lines.append("| --- | --- |")
    for domain in registry.domains():
        count = len(registry.recipes_for_domain(domain))
        lines.append(f"| {_esc(domain)} | {count} |")
    lines.append("")

    for module in PACK_MODULES:
        # 领域包 → workflow.domain 与模块名一致；seed 不属于任何包。
        domain_recipes = [
            r for r in _all_with_workflow(registry)
            if r.workflow.domain == module
        ]
        if not domain_recipes:
            continue
        lines.append(f"## {module}")
        lines.append("")
        for recipe in domain_recipes:
            wf = recipe.workflow
            lines.append(f"### `{recipe.id}` — {recipe.name}")
            lines.append("")
            lines.append(f"{recipe.description}")
            lines.append("")
            caps = ", ".join(f"`{c}`" for c in recipe.preferred_analysis[:6]) or "—"
            lines.append(f"- 任务族：{', '.join(f'`{t}`' for t in recipe.intent_tasks)}")
            lines.append(f"- 主制图：`{recipe.primary_cartography}`"
                         + (f"；辅：{', '.join('`'+c+'`' for c in recipe.secondary_cartography)}" if recipe.secondary_cartography else ""))
            lines.append(f"- 核心能力：{caps}")
            if wf.data_roles:
                roles = ", ".join(
                    f"`{req.role}`{'*' if req.required else ''}"
                    + (f"（{req.missing_policy}）" if req.required and req.missing_policy == "block" else "")
                    for req in wf.data_roles)
                lines.append(f"- 数据角色：{roles}（`*` = 必选）")
            if wf.obligations:
                obls = []
                for obl in wf.obligations:
                    code = obl.warning_code or "—"
                    obls.append(f"`{obl.obligation_id}`（{obl.kind} → {code}）")
                lines.append(f"- 科学义务：{'；'.join(obls)}")
            if wf.fallback_policies:
                fbs = "; ".join(
                    f"`{p.reason_code}` → {p.to_element or '—'}（{p.downgrade_class}）"
                    for p in wf.fallback_policies[:4])
                lines.append(f"- 语义回退：{fbs}")
            if wf.keywords_zh or wf.keywords_en:
                kws = "、".join(list(wf.keywords_zh)[:6] + list(wf.keywords_en)[:4])
                lines.append(f"- 路由关键词：{kws}")
            lines.append(f"- 内容指纹：`{recipe_content_fingerprint(recipe)[:16]}…`")
            lines.append("")
    return "\n".join(lines) + "\n"


def _all_with_workflow(registry):
    return [registry.get(rid) for rid in registry.all_ids
            if registry.get(rid) is not None and registry.get(rid).workflow is not None]


def _domain_of(registry, module: str) -> str:
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只校验 catalog 是否最新（不写入）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    content = render_catalog()
    if args.check:
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != content:
            print(f"[gen_workflow_catalog] {args.out} 已过期 —— 请重新生成", file=sys.stderr)
            return 1
        print(f"[gen_workflow_catalog] {args.out} 最新")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    print(f"[gen_workflow_catalog] 写入 {args.out}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
