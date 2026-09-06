#!/usr/bin/env python3
"""Catalog docs generator — 由 registry 生成制图目录文档（ADR-0101 §文档）.

单一真值是 registry（component/composition/model/theme），docs/cartography/
下的目录文档是**生成产物**：

    python -m app.lib.cartography.catalog_docs          # 生成
    python -m app.lib.cartography.catalog_docs --check  # 漂移检查（CI/测试用）

禁止手改生成文件 —— 修改请进入对应 registry/域包，再重新生成。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "docs" / "cartography"


def _model_catalog() -> str:
    from app.lib.cartography.model_library import get_map_model_registry

    reg = get_map_model_registry()
    lines = [
        "# Map Model Catalog",
        "",
        "> 由 `app.lib.cartography.catalog_docs` 从 MapModelRegistry 生成；",
        "> 手改无效。真值：`model_library.py` + `model_packs/`（ADR-0101）。",
        "",
        f"共 {reg.count} 个模型（native {len(reg.native_ids())} / planned {len(reg.planned_ids())}）。",
        "",
        "| id | 名称 | 几何 | 图层 | 分级 | 色系 | 默认色带 | 状态 | 降级 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for mid in reg.all_ids:
        m = reg.get(mid)
        lines.append(
            f"| {m.id} | {m.name_zh} | {'/'.join(m.geometry_kinds)} | "
            f"{m.maplibre_layer_type} | {m.classification} | {m.color_scheme_kind} | "
            f"{m.default_palette or '—'} | {m.runtime_status} | "
            f"{m.fallback_model_id or '—'} |"
        )
    lines.append("")
    lines.append("## planned 模型的诚实披露")
    lines.append("")
    for mid in reg.planned_ids():
        m = reg.get(mid)
        lines.append(f"### {m.id}（{m.name_zh}）")
        for p in m.pitfalls_zh:
            lines.append(f"- {p}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _component_catalog() -> str:
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.component_templates import get_component_template_registry

    comp_reg = get_component_registry()
    tmpl_reg = get_component_template_registry()
    lines = [
        "# Component & Variant Catalog",
        "",
        "> 由 registry 生成；真值：`component_registry.py` + `component_templates.py`。",
        "",
        f"共 {comp_reg.count} 个组件类型 / {tmpl_reg.count} 个变体模板。",
        "",
        "| type | 类目 | variants | 默认 | 卡数 | 位置 | 绑定 | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cid in comp_reg.all_ids:
        d = comp_reg.get(cid)
        lines.append(
            f"| {d.type} | {d.category} | {', '.join(d.variants) or '—'} | "
            f"{d.default_variant} | {d.cardinality} | {d.default_position} | "
            f"{'layerId' if d.requires_layer_binding else '—'} | {d.runtime_status} |"
        )
    lines.append("")
    lines.append("## 变体模板清单")
    lines.append("")
    for ctype in sorted({t.component_type for t in tmpl_reg._by_id.values()}):
        tpls = tmpl_reg.find_by_type(ctype)
        rows = [
            f"- `{t.id}`（variant={t.variant}{'，planned' if t.runtime_status != 'native' else ''}）"
            for t in tpls
        ]
        lines.append(f"### {ctype}")
        lines.extend(rows)
        lines.append("")
    return "\n".join(lines) + "\n"


def _composition_catalog() -> str:
    from app.lib.cartography.composition_templates import get_composition_template_registry

    reg = get_composition_template_registry()
    lines = [
        "# Composition Template Catalog",
        "",
        "> 由 registry 生成；真值：`composition_templates.py` + `composition_packs/`。",
        "",
        f"共 {reg.count} 个组合模板（seed 8 + 域包）。",
        "",
    ]
    for tpl in reg.all_templates():
        lines.append(f"## {tpl.id}（{tpl.name}）")
        lines.append("")
        lines.append(f"- 描述：{tpl.description}")
        lines.append(f"- 版式：{tpl.layout_profile}；输出：{', '.join(tpl.output_targets)}")
        lines.append(f"- 兼容模型：{', '.join(tpl.compatible_map_models) or '（泛匹配）'}")
        lines.append(f"- fallback：{tpl.fallback_template or '—'}")
        lines.append(f"- 标签：{', '.join(tpl.tags) or '—'}")
        lines.append("- 槽位：")
        for s in tpl.component_slots:
            bind = f"；bind_scope={s.bind_scope}" if s.bind_scope != "primary" else ""
            pref = f"；preferred={', '.join(s.preferred_templates)}" if s.preferred_templates else ""
            fb = f"；fallback_zones={', '.join(s.fallback_zones)}" if s.fallback_zones else ""
            lines.append(
                f"  - `{s.id}`：{s.cardinality}"
                f"（{', '.join(s.allowed_component_types)}）@ {s.position_zone}"
                f"{fb}{bind}{pref}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def _theme_catalog() -> str:
    from app.lib.cartography.themes import get_cartographic_theme_registry

    reg = get_cartographic_theme_registry()
    lines = [
        "# Theme & Palette Catalog",
        "",
        "> 由 registry 生成；颜色真值：`palettes.py`（thematic 色带）与前端",
        "> design tokens（chrome）。本目录只登记描述与引用（ADR-0101 D4）。",
        "",
        "## Palettes",
        "",
        "| id | 语义族 | 色盲安全 | 打印安全 | 灰度最小ΔL |",
        "|---|---|---|---|---|",
    ]
    for p in reg.palettes():
        lines.append(
            f"| {p.id} | {p.kind} | {'✓' if p.colorblind_safe else '—'} | "
            f"{'✓' if p.print_safe else '—'} | {p.min_gray_delta} |"
        )
    lines.append("")
    lines.append("## Themes")
    lines.append("")
    for t in reg.themes():
        lines.append(f"### {t.id}（{t.name_zh}，profile={t.profile}）")
        lines.append(f"- 输出：{', '.join(t.output_targets)}；适配版式：{', '.join(t.layout_profiles) or '—'}")
        lines.append(f"- 排版：{t.typography.title_size_token}/{t.typography.body_size_token}，标题字重 {t.typography.title_weight}")
        lines.append(f"- chrome token 引用：surface={t.chrome.surface}, ink={t.chrome.ink}, chrome-bg={t.chrome.map_chrome_bg}")
        rec = t.palettes
        lines.append(f"- 推荐：sequential={rec.sequential}；diverging={rec.diverging}；qualitative={rec.qualitative}；perceptual={rec.perceptual_uniform}")
        for n in t.notes_zh:
            lines.append(f"- 注：{n}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _parity_matrix() -> str:
    from app.lib.cartography.component_renderers import get_component_renderer_registry
    from app.lib.cartography.component_registry import get_component_registry

    renderers = get_component_renderer_registry()
    comp_reg = get_component_registry()
    lines = [
        "# Renderer Parity Matrix",
        "",
        "> 真值：`component_renderers.py` 单一权威矩阵；本文件是生成视图。",
        "> 空单元格是**声明的缺口**（诚实登记），不是豁免掩盖。",
        "",
        "| type | live | png | pdf | svg | print | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in renderers.all_types:
        s = renderers.support_for(t)
        desc = comp_reg.get_by_type(t)
        status = desc.runtime_status if desc else "（union）"
        def mark(targets, x):
            return "✓" if x in targets else "—"
        note = (s.note or "").split("；")[0].split("。")[0]
        lines.append(
            f"| {t} | {mark(s.renderers, 'interactive')} "
            f"| {mark(s.exporters, 'png')} | {mark(s.exporters, 'pdf')} "
            f"| {mark(s.exporters, 'svg')} | {mark(s.exporters, 'print')} "
            f"| {note}（{status}） |"
        )
    lines.append("")
    lines.append(
        "已知限制（诚实登记，ADR-0101 D6）：\n"
        "- `table_panel` 仅 interactive —— 工作区交互面不是制图产物面（Runtime V4 产品决策）。\n"
        "- 报告 vector SVG（python 孪生 mapspec_to_svg）不含 chrome marginalia；\n"
        "  画布导出（png/pdf/svg-包装）的 chrome 由 exporter/composeLayout 承载。\n"
        "- `basemap` 是类型占位（底图逻辑在 map-panel，非 chrome 渲染）。\n"
    )
    return "\n".join(lines) + "\n"


GENERATORS = {
    "map-model-catalog.md": _model_catalog,
    "component-catalog.md": _component_catalog,
    "composition-template-catalog.md": _composition_catalog,
    "theme-palette-catalog.md": _theme_catalog,
    "renderer-parity-matrix.md": _parity_matrix,
}


def generate() -> dict:
    out = {}
    for name, fn in GENERATORS.items():
        out[name] = fn()
    return out


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
            print("re-run: python -m app.lib.cartography.catalog_docs")
            return 1
        print("catalog docs up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
