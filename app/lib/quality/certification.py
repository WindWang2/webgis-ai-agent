"""Cancellation & Resource Safety Certification（ADR-0104 Wave 9/10）。

两张认证表都是**派生物**（派生失败即漂移红），不是手写 YAML：

- 取消覆盖（Wave 9）：重计算模块 × 协作式取消检查点站点数
  （``checkpoint()`` / ``cancellable(``）。零检查点的模块如实披露为
  ``no-checkpoints`` 缺口，不伪造 certified。
- 资源安全（Wave 10）：上限常量**程序化内省**（RasterResourceGuard /
  json_size / 工具参数预算门 / 算法注册表资格声明），写死数值即第二事实
  源 —— 所以表渲染时从源头读。

红线：tests/quality/test_cancellation_resource_certification.py
（表字节一致 + 运行时取消/typed reject 行为认证）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.lib.quality.discovery import repo_root

#: 重计算模块扫描根（相对 repo 根）
HEAVY_SCAN_ROOTS = (
    "app/lib/geo_analysis",
    "app/lib/geo_raster",
    "app/services/data_ingest",
    "app/services/mapspec_layer_pipeline.py",
    "app/services/mapspec_to_svg.py",
)

_CANCELLATION_SEAMS = ("checkpoint()", "cancellable(")


def _iter_heavy_files() -> List[Path]:
    root = repo_root()
    files: List[Path] = []
    for rel in HEAVY_SCAN_ROOTS:
        p = root / rel
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(x for x in p.rglob("*.py") if x.is_file()))
    return files


def cancellation_coverage() -> List[Dict[str, Any]]:
    """每个重计算文件的取消检查点站点数（静态发现，排序确定）。"""
    rows: List[Dict[str, Any]] = []
    for path in _iter_heavy_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(repo_root()).as_posix()
        sites = sum(text.count(seam) for seam in _CANCELLATION_SEAMS)
        loops = text.count("for ")  # 粗粒度循环计数（仅披露用，不作闸）
        rows.append({
            "file": rel,
            "cancellation_sites": sites,
            "loop_sites": loops,
            "status": "certified" if sites > 0 else "no-checkpoints",
        })
    return sorted(rows, key=lambda r: r["file"])


def resource_limits() -> List[Dict[str, Any]]:
    """上限常量内省（数值从源头读取；来源列即漂移定位）。"""
    from app.lib.geo_analysis.raster_guard import RasterResourceGuard
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.json_size import ESTIMATE_MAX_NODES, ESTIMATE_SIZE_LIMIT

    rows: List[Dict[str, Any]] = [
        {
            "kind": "raster_total_pixels",
            "limit": RasterResourceGuard.MAX_RASTER_PIXELS,
            "unit": "pixels",
            "source": "app/lib/geo_analysis/raster_guard.py",
            "typed_reject": "RasterResourceExceededError",
        },
        {
            "kind": "raster_max_width",
            "limit": RasterResourceGuard.MAX_RASTER_WIDTH,
            "unit": "pixels",
            "source": "app/lib/geo_analysis/raster_guard.py",
            "typed_reject": "RasterResourceExceededError",
        },
        {
            "kind": "raster_max_height",
            "limit": RasterResourceGuard.MAX_RASTER_HEIGHT,
            "unit": "pixels",
            "source": "app/lib/geo_analysis/raster_guard.py",
            "typed_reject": "RasterResourceExceededError",
        },
        {
            "kind": "raster_output_bytes",
            "limit": RasterResourceGuard.MAX_ESTIMATED_OUTPUT_BYTES,
            "unit": "bytes",
            "source": "app/lib/geo_analysis/raster_guard.py",
            "typed_reject": "RasterResourceExceededError",
        },
        {
            "kind": "json_node_budget",
            "limit": ESTIMATE_MAX_NODES,
            "unit": "nodes",
            "source": "app/lib/json_size.py",
            "typed_reject": "args oversized 门（Registry）",
        },
        {
            "kind": "tool_args_bytes",
            "limit": ESTIMATE_SIZE_LIMIT,
            "unit": "bytes",
            "source": "app/lib/json_size.py",
            "typed_reject": "args oversized 门（Registry）",
        },
    ]

    reg = get_algorithm_registry()
    mins = hints = variants = 0
    for aid in reg.all_ids:
        algo = reg.get(aid)
        if algo is None:
            continue
        mins += 1 if algo.min_features is not None else 0
        hints += 1 if algo.max_features_hint is not None else 0
        variants += 1 if algo.backend_variants else 0
    total = reg.count
    rows.append({
        "kind": "algorithm_min_features_declarations",
        "limit": f"{mins}/{total}",
        "unit": "algorithms",
        "source": "app/lib/gis/algorithm_registry.py",
        "typed_reject": "数据资格四态（qualify_data）",
    })
    rows.append({
        "kind": "algorithm_max_features_hint_declarations",
        "limit": f"{hints}/{total}",
        "unit": "algorithms",
        "source": "app/lib/gis/algorithm_registry.py",
        "typed_reject": "数据资格四态（qualify_data）",
    })
    rows.append({
        "kind": "algorithm_backend_variants",
        "limit": f"{variants}/{total}",
        "unit": "algorithms",
        "source": "app/lib/gis/algorithm_registry.py",
        "typed_reject": "ScaleProfile 窗口（dispatch）",
    })
    return rows


# ── 渲染 ─────────────────────────────────────────────────────────────────


def _esc(text: Any) -> str:
    return str(text).replace("|", "\\|")


def render_cancellation_md() -> str:
    rows = cancellation_coverage()
    certified = sum(1 for r in rows if r["status"] == "certified")
    lines: List[str] = []
    lines.append("# Cancellation Coverage Certification（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_resource_certification.py` 派生，请勿手改。")
    lines.append("> 契约：协作式取消原语 `app/lib/cancellation.py`（checkpoint /")
    lines.append("> cancellable）；行为认证：tests/quality/")
    lines.append("> test_cancellation_resource_certification.py。")
    lines.append("")
    lines.append(f"- 覆盖：**{certified}/{len(rows)}** 个重计算文件含取消检查点。")
    lines.append("")
    lines.append("| file | cancellation sites | loop sites（粗计） | status |")
    lines.append("|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {_esc(r['file'])} | {r['cancellation_sites']} | "
            f"{r['loop_sites']} | {r['status']} |"
        )
    lines.append("")
    lines.append(
        "> no-checkpoints 是如实披露的缺口（非失败）：该文件当前没有")
    lines.append("> 长循环或尚未接线；认证表随补齐更新。")
    lines.append("")
    return "\n".join(lines)


def render_resource_md() -> str:
    rows = resource_limits()
    lines: List[str] = []
    lines.append("# Resource Safety Certification（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_resource_certification.py` 派生，请勿手改。")
    lines.append("> 原则：estimate-before-allocate；超限必须 typed reject，")
    lines.append("> 而不是 OOM。行为认证：tests/quality/")
    lines.append("> test_cancellation_resource_certification.py。")
    lines.append("")
    lines.append("| resource kind | limit | unit | source | typed reject |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {_esc(r['kind'])} | {_esc(r['limit'])} | {r['unit']} | "
            f"{_esc(r['source'])} | {_esc(r['typed_reject'])} |"
        )
    lines.append("")
    return "\n".join(lines)
