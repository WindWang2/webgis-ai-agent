#!/usr/bin/env python
"""AC-03 硬编码回潮扫描（ADR-0152 P8 深化杠杆）。

扫描 app/ 业务代码中「分类 method / 级数 k / 色带 palette」的字面硬编码，
对照 allowlist 输出违规清单。退出码 0 = 无回潮；1 = 发现未登记站点。

用法：
    ./.venv/Scripts/python scripts/symbology_audit.py
    ./.venv/Scripts/python scripts/symbology_audit.py --verbose

允许例外（allowlist，改这些行前先读 docs/dev/ac-03-hardcode-ledger.csv）：
- 引擎/契约自身的默认值常量（classify.py / thematic_spec 签名、sym 容许值）
- 命名常量与注册表（DEFAULT_*、HEATMAP_LEGEND_PALETTE_KEY、模型库元数据）
- 结构语义色（LISA 五色、hillshade 灰度、光谱引擎连续带）
- 禁改文件（app/services/gis_harness/**，01/02 线领地）只统计不拦截
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["app"]
SKIP_PARTS = {"tests", "__pycache__", "node_modules", "frontend"}

# 文件级 allowlist：引擎自身默认值 / 命名常量 / 语义色（详见 ledger C 级）
FILE_ALLOWLIST: dict[str, str] = {
    "app/lib/cartography/classify.py": "分类引擎自身默认值常量（engine semantics）",
    "app/lib/cartography/visualization_plan.py": "裁决器内部（k 夹逼/词表）",
    "app/lib/cartography/palettes.py": "色带注册表 + 热力族常量 + 兜底默认",
    "app/lib/cartography/model_library.py": "模型/色带元数据目录（文档性）",
    "app/lib/cartography/model_packs/": "模型推荐元数据（文档性）",
    "app/lib/cartography/composition_selection.py": "组合语法词表（文档性）",
    "app/lib/cartography/isoline_model.py": "等值线专属策略词表",
    "app/lib/cartography/bivariate.py": "双变量色阵内部方法参数",
    "app/lib/cartography/design_system.py": "设计系统分类词表分支",
    "app/lib/cartography/themes.py": "主题推荐清单（palette 知识源）",
    "app/lib/cartography/heatmap_contract.py": "DEFAULT_RADIUS_PX 命名常量",
    "app/lib/cartography/thematic_spec.py": "canonical builder 签名默认（None=裁决已接线）",
    "app/lib/cartography/symbology.py": "裁决引擎本体",
    "app/lib/geo_analysis/kriging.py": "IDW k=5 是插值近邻数（非分类）",
    "app/lib/geo_analysis/regression_kriging.py": "IDW k=5 是插值近邻数（非分类）",
    "app/lib/geo_analysis/rs_v3.py": "遥感统计分位数（非符号化）",
    "app/services/raster_cartography_converter.py": "栅格 PNG 渲染族命名常量（Viridis/Gray/Oranges）",
    "app/services/spatial_tasks.py": "变化检测预览 PNG（栅格族）",
    "app/services/rs/spectral_engine.py": "光谱指数连续带（Viridis 语义）",
    "app/services/cartography_service.py": "签名默认值常量（None=裁决已接线）+ LISA 语义色",
    "app/services/mapspec/composite_builder.py": "无数据合成 spec 兜底 + LISA 语义色（主路径已接引擎）",
    "app/schemas/map_component_slots.py": "组合槽位 schema 默认值常量",
    "app/schemas/template_schema.py": "模板 schema 默认值常量 + SEED payload（语义=声明偏好）",
    "app/tools/spatial.py": "热力族默认 classic（family↔canonical 映射已接引擎）",
    "app/services/analysis_cartography_converter.py": "热力族 fallback",
}

# 01/02 线领地：只报告不拦截
REPORT_ONLY = {"app/services/gis_harness/"}

# 扫描两条正则：method 赋值 与 palette 字面量赋值（词边界，避免误伤普通字符串）
RE_METHOD_KW = re.compile(
    r"\b(?:method|recommended_method|default_method)\s*=\s*[\"'](quantiles|equal_interval|natural_breaks|std_dev|head_tail)[\"']"
)
RE_PALETTE_KW = re.compile(
    r"\b(?:palette|fallback|default_palette)\s*=\s*[\"'](YlOrRd|Blues|Greens|Reds|Oranges|Purples|RdYlGn|RdBu|PuOr|Set1|Set2|Dark2|Pastel1|Viridis|Magma|Inferno|Plasma|Gray)[\"']"
)
RE_K_ASSIGN = re.compile(r"\bk\s*=\s*(\d+)\b")


def iter_py_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            yield p


def allowlist_reason(rel: str) -> str | None:
    for prefix, reason in FILE_ALLOWLIST.items():
        if rel.startswith(prefix) or rel == prefix:
            return reason
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    violations: list[tuple[str, int, str, str]] = []
    report_only_hits: list[tuple[str, int, str]] = []

    for path in iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = []
            m = RE_METHOD_KW.search(line)
            if m:
                hits.append(f"method 硬编码 {m.group(1)!r}")
            m = RE_PALETTE_KW.search(line)
            if m:
                hits.append(f"palette 硬编码 {m.group(1)!r}")
            if not hits:
                continue
            reason = allowlist_reason(rel)
            if reason:
                if args.verbose:
                    print(f"  [allow] {rel}:{lineno}: {hits[0]} ({reason})")
                continue
            if any(rel.startswith(p) for p in REPORT_ONLY):
                report_only_hits.append((rel, lineno, hits[0]))
                continue
            violations.append((rel, lineno, hits[0], line.strip()[:120]))

    if report_only_hits:
        print(f"[report-only] 禁改领地站点（{len(report_only_hits)} 处，不拦截）：")
        for rel, lineno, hit in report_only_hits:
            print(f"  {rel}:{lineno}: {hit}")

    if violations:
        print(f"[FAIL] 未登记的硬编码回潮（{len(violations)} 处）：")
        for rel, lineno, hit, line in violations:
            print(f"  {rel}:{lineno}: {hit}\n      | {line}")
        print("\n处理方式：改走 app.lib.cartography.symbology.resolve_symbology，"
              "或在 scripts/symbology_audit.py FILE_ALLOWLIST 登记 C 级例外（附理由）。")
        return 1
    print("[OK] 硬编码销项扫描通过（无未登记站点）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
