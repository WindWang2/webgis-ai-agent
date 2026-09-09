#!/usr/bin/env python3
"""Export the backend component catalog as a frontend contract artifact.

Writes ``frontend/lib/map-components/component-catalog.generated.json`` —
the single machine-readable truth consumed by:

- the frontend renderer parity test (every renderer_required type must have
  a registered renderer);
- the backend contract test (tests/unit/test_component_catalog_parity.py)
  which regenerates the payload and fails on drift.

Run from repo root:  python -m app.lib.cartography.export_component_catalog
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_renderers import get_component_renderer_registry
from app.lib.cartography.render_diagnostics import catalog_section
from app.lib.cartography.themes import get_cartographic_theme_registry
from app.services.gis_harness.components import ComponentType

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT = REPO_ROOT / "frontend" / "lib" / "map-components" / "component-catalog.generated.json"

# ComponentTypes that must have a live interactive renderer whenever a spec
# enables them (chrome/panel family). Export/planned types are exempt.
RENDERER_EXEMPT = {
    "basemap",          # type-only union member (style, not chrome)
    "export_layout",    # exporter-side only
    # "graticule" moved out (P3): live renderer landed — rendererRequired=true
    "map_border",       # export frame (renderer optional)
    # "inset_map" moved out (v2): live renderer landed — rendererRequired=true
}


def build_catalog() -> dict:
    registry = get_component_registry()
    renderers = get_component_renderer_registry()
    theme_reg = get_cartographic_theme_registry()
    types = sorted(get_args(ComponentType))
    components = []
    for t in types:
        desc = registry.get_by_type(t)
        support = renderers.support_for(t)
        entry = {
            "type": t,
            "variants": list(desc.variants) if desc else [],
            "defaultVariant": desc.default_variant if desc else "default",
            "defaultPosition": desc.default_position if desc else "none",
            "category": desc.category if desc else "",
            "runtimeStatus": desc.runtime_status if desc else "native",
            "rendererRequired": t not in RENDERER_EXEMPT,
            # 机器真值（component_renderers.py 单一权威）：live 渲染器与
            # 导出器各自真正消费该组件类型的目标清单。
            "rendererSupport": list(support.renderers) if support else [],
            "exporterSupport": list(support.exporters) if support else [],
        }
        components.append(entry)
    # V3（ADR-0101 D3/D4）：additive —— themes/palettes 描述层随目录
    # 导出（颜色 hex 不入目录：thematic 真值 palettes.py、chrome 真值
    # 前端 token；这里只有 id 与推导元数据）。
    palettes = [
        {
            "id": p.id,
            "kind": p.kind,
            "colorblindSafe": p.colorblind_safe,
            "colorblindSafeMaxClasses": p.colorblind_safe_max_classes,
            "printSafe": p.print_safe,
            "grayLevels": p.gray_levels,
            "minGrayDelta": p.min_gray_delta,
        }
        for p in theme_reg.palettes()
    ]
    themes = [
        {
            "id": t.id,
            "profile": t.profile,
            "outputTargets": list(t.output_targets),
            "colorblindSafeFirst": t.colorblind_safe_first,
            "typography": {
                "titleSizeToken": t.typography.title_size_token,
                "bodySizeToken": t.typography.body_size_token,
                "microSizeToken": t.typography.micro_size_token,
                "titleWeight": t.typography.title_weight,
                "minChromePx": t.typography.min_chrome_px,
            },
            "spacing": {
                "stackStepPx": t.spacing.stack_step_px,
                "chromePaddingPx": t.spacing.chrome_padding_px,
            },
            "chromeTokenRefs": t.chrome.model_dump(),
            "paletteRecommendations": t.palettes.model_dump(),
        }
        for t in theme_reg.themes()
    ]
    # V4：图表 kind 词表随目录导出（id/数据形状/live 引擎/导出能力等级）
    from app.lib.cartography.chart_kinds import (
        AGENT_CHART_OPERATIONS,
        CHART_KINDS,
        CHART_STATE_TRANSITIONS,
        CHART_STATES,
    )
    chart_states = sorted(CHART_STATES)
    chart_state_transitions = sorted(f"{a}>{b}" for a, b in CHART_STATE_TRANSITIONS)
    agent_chart_operations = {
        op: sorted(pre) for op, pre in sorted(AGENT_CHART_OPERATIONS.items())
    }
    chart_kinds = [
        {
            "id": k.id,
            "dataShape": k.data_shape,
            "liveEngine": k.live_engine,
            "exportLevel": k.export_level,
            "selectionLinkage": k.selection_linkage,
        }
        for k in sorted(CHART_KINDS, key=lambda k: k.id)
    ]
    return {
        "schemaVersion": 5,
        "exportedFrom": "app/lib/cartography/component_registry.py",
        "componentTypes": components,
        "palettes": palettes,
        "themes": themes,
        "chartKinds": chart_kinds,
        "chartStates": chart_states,
        "chartStateTransitions": chart_state_transitions,
        "agentChartOperations": agent_chart_operations,
        # V5（ADR-0118 D1）：渲染/导出降级诊断权威词表随目录导出 ——
        # 前端 ExportDegradation 码必须是其子集（registry-parity 锁定）。
        "renderDiagnostics": catalog_section(),
    }


def main() -> int:
    catalog = build_catalog()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(catalog['componentTypes'])} component types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
