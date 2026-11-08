"""简报离线导出（G11 / BRF-05）——纯函数，零外链单文件 HTML + manifest。

纪律：
- 自解压单文件 HTML（沿 export_packager 的 ``_embed_json``/``esc()``
  先例）：script 早闭合/注释逃逸/\\u2028 全覆盖；一切数据面文本先 esc
  再拼 DOM。
- 内嵌图件只含**已许可冻结图件**（服务端渲染的静态 SVG）；无离线交互
  地图资源时显示真实告警，不伪造空白地图。
- 隐私：bundle 过 ``sanitize_dict``（敏感键 REDACT + URL 凭证 scrub）；
- CSV 附件公式注入防护（=+-@ 与制表符打头加撇号）。
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.lib.storymap.brief_spec import MapBriefSpec
from app.lib.storymap.export_packager import (
    _embed_json,
    sanitize_dict,
)
from app.lib.storymap.spec import assert_json_depth, strip_surrogates

BRIEF_BUNDLE_SCHEMA_VERSION = "briefbundle-1"

#: SVG 净化（纵深防御；生成侧 compile_mapspec_to_svg 已转义用户文本）。
#: 覆盖全部已知旁路形态（评审 #P1-2 实测清单）：
#: - script 全形态：配对闭合 / 自闭合 ``<script/>`` / 未闭合至结尾；
#: - SMIL 动画注入（<set>/<animate*> 可写事件属性与 href）；
#: - foreignObject；事件属性（含未引号值）；
#: - href 只放行 data:（含未引号形态），其余（javascript:/http:…）剔除。
_SVG_SCRIPT_PAIRED_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_SVG_SCRIPT_SELFCLOSE_RE = re.compile(r"<script\b[^>]*/>", re.I)
_SVG_SCRIPT_OPEN_RE = re.compile(r"<script\b[^>]*>.*", re.I | re.S)
_SVG_FOREIGN_RE = re.compile(
    r"<foreignObject\b[^>]*>.*?</foreignObject\s*>|<foreignObject\b[^>]*/>",
    re.I | re.S)
_SVG_SMIL_RE = re.compile(
    r"<(?:set|animate|animateTransform|animateMotion|animateColor|handler)\b"
    r"[^>]*(?:/>|>.*?</(?:set|animate|animateTransform|animateMotion"
    r"|animateColor|handler)\s*>)",
    re.I | re.S)
# N1（复审）：属性边界不止空白 —— 引号闭合后紧跟属性名（fill="x"onload=…）
# 同样是合法属性分隔；捕获边界字符以免破坏前一个属性。
_SVG_EVENT_RE = re.compile(
    r"([\s\"'])on\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_SVG_HREF_RE = re.compile(
    r"(?:xlink:)?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)

#: 单文件 HTML 预算（内嵌 SVG/图表有界；超限显式失败优于半包）。
MAX_BRIEF_HTML_BYTES = 8 * 1024 * 1024

#: release 元信息白名单（进入 bundle/HTML 的唯一字段；share_code/
#: owner_token/签名 URL 等秘密绝不内嵌 —— BRF-O08）。
_RELEASE_META_KEYS = frozenset({
    "release_id", "release_no", "frozen_revision", "created_at",
    "mapspec_fingerprint", "spec_sha256",
})


def _project_release_meta(meta: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {k: meta[k] for k in _RELEASE_META_KEYS if k in dict(meta or {})}


def sanitize_svg(markup: str) -> str:
    """静态 SVG → 可内嵌安全形态。

    纵深防御白名单式收窄：无脚本（全形态）/无 SMIL/无 foreignObject/
    无事件属性/href 仅 data:。审查实测的自闭合 script、未闭合 script、
    未引号 ``href=javascript:``、``<set attributeName=onmouseover>``、
    未引号外链 ``<image href=http://…>`` 全部被剔除（负例见
    tests/unit/test_mapbrief_security.py::TestSvgSanitizeHardened）。
    """
    out = _SVG_SCRIPT_PAIRED_RE.sub("", markup or "")
    out = _SVG_SCRIPT_SELFCLOSE_RE.sub("", out)
    out = _SVG_SCRIPT_OPEN_RE.sub("", out)
    out = _SVG_SMIL_RE.sub("", out)
    out = _SVG_FOREIGN_RE.sub("", out)
    out = _SVG_EVENT_RE.sub(r"\1", out)

    def _drop_external(match: "re.Match[str]") -> str:
        url = (match.group(1) or "").strip("\"'")
        return match.group(0) if url.startswith("data:") else ""

    return _SVG_HREF_RE.sub(_drop_external, out)


def csv_safe(value: Any) -> str:
    """CSV 单元格公式注入防护：危险打头字符（=+-@ 与 \\t/\\r/\\n）前置撇号。

    \\r/\\n 打头单独不成公式，但部分表格引擎会把行首控制符后的内容当
    公式重新解析 —— 一并打撇号（csv.writer 的引号包裹只管分隔符语义）。
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + text
    return text


def build_fact_csv_rows(spec: MapBriefSpec) -> List[List[str]]:
    """事实清单 CSV 行（证据对账面）。"""
    rows = [["fact_id", "label", "kind", "value", "unit", "status",
             "status_reason", "artifact_ref", "field_path",
             "source_label", "method", "temporal_window"]]
    for fact in spec.facts:
        rows.append([
            fact.fact_id, fact.label, fact.kind, repr(fact.value),
            fact.unit, fact.status, fact.status_reason,
            fact.basis.artifact_ref, fact.basis.field_path,
            fact.basis.source_label, fact.basis.method,
            fact.basis.temporal_window,
        ])
    return rows


def rows_to_csv(rows: Sequence[Sequence[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return buf.getvalue()


def build_brief_bundle(
    spec: MapBriefSpec,
    *,
    payloads: Optional[Mapping[str, Any]] = None,
    static_maps: Optional[Mapping[str, str]] = None,
    release_meta: Optional[Mapping[str, Any]] = None,
    draft: bool = True,
    sanitize: bool = True,
) -> Dict[str, Any]:
    """spec + 产物载荷 + 静态图件 → 自包含简报 bundle dict。"""
    payloads = dict(payloads or {})
    static_maps = {str(k): sanitize_svg(str(v))
                   for k, v in (static_maps or {}).items()}
    spec_dict = spec.model_dump(mode="json")
    assert_json_depth(spec_dict)
    assert_json_depth(payloads)
    # 图表数据内联（widget ref → data 投影；缺数据的 widget 不内联）。
    widgets: Dict[str, Any] = {}
    for widget in spec.linked_widgets:
        ref = widget.ref or widget.id
        data = payloads.get(ref)
        if isinstance(data, Mapping):
            widgets[widget.id] = {
                "kind": widget.kind, "title": widget.title, "ref": ref,
                "chart": data.get("chart") if isinstance(data.get("chart"), Mapping) else None,
                "table": data.get("table") if data.get("table") is not None else None,
            }
        elif widget.data:
            widgets[widget.id] = {
                "kind": widget.kind, "title": widget.title, "ref": ref,
                "chart": widget.data if widget.kind == "chart" else None,
                "table": widget.data if widget.kind in ("table", "stats", "kpi") else None,
            }
    facts_by_id = {f.fact_id: f.model_dump(mode="json") for f in spec.facts}
    blocks_by_chapter: Dict[str, List[str]] = {}
    for block in spec.blocks:
        blocks_by_chapter.setdefault(block.chapter_id, []).append(block.id)
    bundle: Dict[str, Any] = {
        "schema_version": BRIEF_BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "draft": bool(draft),
        "release": _project_release_meta(release_meta),
        "spec": strip_surrogates(spec_dict),  # type: ignore[arg-type]
        "widgets": strip_surrogates(widgets),
        "static_maps": static_maps,
        "facts_index": strip_surrogates(facts_by_id),
        "manifest": {
            "chapter_count": len(spec.chapters),
            "block_count": len(spec.blocks),
            "fact_count": len(spec.facts),
            "fact_stats": {
                s: sum(1 for f in spec.facts if f.status == s)
                for s in ("verified", "warning", "unverified")
            },
            "static_map_count": len(static_maps),
            "mapspec_fingerprint": spec.provenance.mapspec_fingerprint,
            "artifact_refs": list(spec.provenance.artifact_refs),
        },
    }
    if sanitize:
        return sanitize_dict(bundle)
    return bundle


def build_release_manifest(
    *,
    release_id: str,
    release_no: int,
    brief_id: str,
    frozen_revision: int,
    draft: bool,
    bundle: Mapping[str, Any],
    html_sha256: str,
    html_bytes: int,
    csv_sha256: str,
    csv_bytes: int,
    spec_sha256: str,
) -> Dict[str, Any]:
    """机器可读发布 manifest（对账面：文件/证据/指纹/版本 pin）。"""
    import hashlib

    spec = bundle.get("spec") or {}
    files = [
        {"name": "brief.html", "kind": "html", "status": "ready",
         "sha256": html_sha256, "bytes": html_bytes},
    ]
    if csv_bytes:
        files.append({"name": "facts.csv", "kind": "csv",
                      "status": "ready" if csv_sha256 else "failed",
                      "sha256": csv_sha256, "bytes": csv_bytes})
    evidence = []
    for fact_id, fact in (bundle.get("facts_index") or {}).items():
        basis = fact.get("basis") or {}
        evidence.append({
            "fact_id": fact_id,
            "status": fact.get("status"),
            "artifact_ref": basis.get("artifact_ref"),
            "field_path": basis.get("field_path"),
            "artifact_sha256": basis.get("artifact_sha256"),
            "source_label": basis.get("source_label"),
        })
    return {
        "manifest_version": "briefrelease-1",
        "release_id": release_id,
        "release_no": release_no,
        "brief_id": brief_id,
        "frozen_revision": frozen_revision,
        "draft": draft,
        "title": ((bundle.get("spec") or {}).get("metadata") or {}).get(
            "title", ""),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
        "evidence": evidence,
        "spec_sha256": spec_sha256,
        "mapspec_fingerprint": (bundle.get("manifest") or {}).get(
            "mapspec_fingerprint", ""),
        "fact_stats": (bundle.get("manifest") or {}).get("fact_stats", {}),
        "counts": {
            "chapters": (bundle.get("manifest") or {}).get("chapter_count", 0),
            "blocks": (bundle.get("manifest") or {}).get("block_count", 0),
            "facts": (bundle.get("manifest") or {}).get("fact_count", 0),
        },
    }


# ── 自解压 HTML（查看器 JS：纯 esc + 白名单渲染，零外链） ──────────────

_VIEWER_JS = r"""
(function () {
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtNum(v) {
    if (typeof v !== 'number' || !isFinite(v)) return esc(v);
    return String(Math.round(v * 10000) / 10000);
  }
  var STATUS_ZH = { verified: '已核验', warning: '有提示', unverified: '未验证' };
  function tryParse() {
    return JSON.parse(document.getElementById('brief-bundle').textContent);
  }
  function renderBlock(b, bundle) {
    var facts = bundle.facts_index || {};
    var widgets = bundle.widgets || {};
    var maps = bundle.static_maps || {};
    var html = '';
    if (b.kind === 'text' || b.kind === 'method' || b.kind === 'limitation') {
      if (b.text) html += '<div class="prose">' + esc(b.text).replace(/\n/g, '<br>') + '</div>';
    } else if (b.kind === 'metric') {
      var f = facts[b.fact_id];
      if (!f) { return '<div class="warn">指标引用缺失：' + esc(b.fact_id) + '</div>'; }
      var basis = f.basis || {};
      html += '<div class="metric"><div class="metric-top"><span class="metric-label">'
        + esc(f.label) + '</span><span class="badge badge-' + esc(f.status) + '">'
        + esc(STATUS_ZH[f.status] || f.status) + '</span></div>'
        + '<div class="metric-value">' + fmtNum(f.value)
        + '<span class="metric-unit">' + esc(f.unit || '') + '</span></div>'
        + '<div class="metric-src">来源：' + esc(basis.source_label || basis.artifact_ref || '未绑定')
        + (basis.method ? ' · 方法：' + esc(basis.method) : '')
        + (basis.temporal_window ? ' · 时间窗：' + esc(basis.temporal_window) : '')
        + (basis.field_path ? ' · 字段：' + esc(basis.field_path) : '')
        + '</div>'
        + (f.status_reason ? '<div class="metric-reason">' + esc(f.status_reason) + '</div>' : '')
        + '</div>';
    } else if (b.kind === 'chart' || b.kind === 'table') {
      var w = widgets[b.widget_id];
      if (!w) { return '<div class="warn">图表数据缺失：' + esc(b.widget_id) + '</div>'; }
      html += '<div class="widget"><h3>' + esc(w.title || (b.kind === 'chart' ? '图表' : '表格')) + '</h3>';
      var table = w.table;
      var rows = table && table.rows ? table.rows : (Array.isArray(table) ? table : null);
      var chart = w.chart;
      var data = chart && chart.data ? chart.data : null;
      var records = rows || data;
      if (records) {
        html += '<table>';
        records.slice(0, 64).forEach(function (r) {
          if (r && typeof r === 'object') {
            html += '<tr>';
            Object.keys(r).slice(0, 12).forEach(function (k) {
              html += '<td>' + esc(r[k]) + '</td>';
            });
            html += '</tr>';
          }
        });
        html += '</table>';
      } else {
        html += '<div class="warn">图表以结构化数据附于 manifest，离线页仅呈现数据表。</div>';
      }
      html += '</div>';
    } else if (b.kind === 'map_view') {
      var svg = maps[b.id];
      html += '<div class="mapview">';
      if (svg) {
        html += '<div class="map-svg">' + svg + '</div>';
        var mv = b.map_view || {};
        html += '<div class="metric-src">镜头 [' + esc((mv.center || []).join(', '))
          + '] zoom ' + esc(mv.zoom) + ' · 图层：'
          + esc((mv.visible_layer_ids || []).join(', ') || '（沿用发布时状态）')
          + (mv.mapspec_fingerprint ? ' · 地图版本指纹 ' + esc(String(mv.mapspec_fingerprint).slice(0, 16)) : '')
          + '</div>';
      } else {
        html += '<div class="warn">本章节静态地图图件未随包提供（无许可的底图/瓦片不复制）。'
          + '离线包不含交互式地图；空间分布请参见上表数据或在线版本。</div>';
      }
      html += '</div>';
    } else if (b.kind === 'source') {
      var sb = b.binding || {};
      html += '<div class="source">来源：' + esc(sb.source_label || sb.artifact_ref)
        + (sb.produced_at ? '（' + esc(sb.produced_at) + '）' : '')
        + (sb.method ? ' · ' + esc(sb.method) : '') + '</div>';
    }
    return html;
  }
  function render(bundle) {
    var spec = bundle.spec || {};
    var meta = spec.metadata || {};
    var rel = bundle.release || {};
    var root = document.getElementById('brief-root');
    var html = '<header class="cover"><h1>' + esc(meta.title || '地图简报') + '</h1>';
    html += '<p class="meta">' + (bundle.draft ? '<span class="badge badge-draft">草稿 · 未认证</span>' : '<span class="badge badge-ok">已发布版本 v' + esc(rel.release_no) + '</span>')
      + ' · 生成于 ' + esc(bundle.generated_at || '')
      + ' · 章节 ' + esc((bundle.manifest || {}).chapter_count)
      + ' · 指标 ' + esc((bundle.manifest || {}).fact_count) + '</p>';
    if (meta.summary) html += '<p class="summary">' + esc(meta.summary) + '</p>';
    if (bundle.draft) {
      html += '<p class="draft-note">本文件为草稿导出：未核验指标以“未验证”披露，'
        + '不得作为已认证发布版本分发。</p>';
    }
    html += '</header>';
    // 块按 chapter 分组、组内按 order 排序后渲染（章节序保持 spec 序）。
    var byChapter = {};
    (spec.blocks || []).forEach(function (b) {
      (byChapter[b.chapter_id] = byChapter[b.chapter_id] || []).push(b);
    });
    (spec.chapters || []).forEach(function (ch) {
      html += '<section><h2>' + esc(ch.title) + '</h2>';
      var chapterBlocks = (byChapter[ch.id] || []).slice()
        .sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
      chapterBlocks.forEach(function (b) {
        html += renderBlock(b, bundle);
      });
      html += '</section>';
    });
    var stats = (bundle.manifest || {}).fact_stats || {};
    html += '<footer class="evidence"><h2>证据清单</h2><p>'
      + '已核验 ' + esc(stats.verified || 0) + ' · 有提示 ' + esc(stats.warning || 0)
      + ' · 未验证 ' + esc(stats.unverified || 0)
      + ' · 完整对账数据见随附 facts 清单与 JSON manifest。</p></footer>';
    root.innerHTML = html;
  }
  try {
    render(tryParse());
  } catch (err) {
    document.getElementById('brief-root').textContent = '简报数据损坏：' + err;
  }
})();
"""

_HTML_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 地图简报</title>
<style>
:root { color-scheme: dark; }
body { margin: 0; background: #0b0f14; color: #d7dde6;
       font: 15px/1.75 "Segoe UI", "Microsoft YaHei", sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 48px 24px 96px; }
h1 { color: #4ea1ff; letter-spacing: .1em; margin-bottom: 4px; }
h2 { color: #7fc4ff; border-bottom: 1px solid #223041; padding-bottom: 6px;
     margin-top: 44px; }
h3 { color: #a8d4ff; margin: 12px 0 6px; font-size: 15px; }
.meta { color: #7d8b9d; font-size: 13px; }
.summary { background: #101822; border: 1px solid #223041; border-radius: 8px;
           padding: 12px 16px; margin: 12px 0; }
.badge { display: inline-block; border-radius: 999px; padding: 1px 10px;
         font-size: 12px; margin-left: 8px; border: 1px solid; }
.badge-verified { color: #7fe3a5; border-color: #2c6e4b; }
.badge-warning { color: #ffd27f; border-color: #8a6d2f; }
.badge-unverified { color: #ff9f9f; border-color: #8a3f3f; }
.badge-draft { color: #ffd27f; border-color: #8a6d2f; }
.badge-ok { color: #7fe3a5; border-color: #2c6e4b; }
.draft-note { color: #ffd27f; background: #241c0e; border: 1px solid #6b5320;
              border-radius: 8px; padding: 8px 12px; font-size: 13px; }
.prose { white-space: normal; margin: 10px 0; }
.metric { background: #101822; border: 1px solid #223041; border-radius: 10px;
          padding: 12px 16px; margin: 14px 0; }
.metric-top { display: flex; justify-content: space-between; align-items: center; }
.metric-label { color: #a8d4ff; }
.metric-value { font-size: 26px; font-weight: 600; color: #e8f1ff; margin: 4px 0; }
.metric-unit { font-size: 13px; color: #7d8b9d; margin-left: 6px; }
.metric-src { color: #7d8b9d; font-size: 12px; }
.metric-reason { color: #b0986f; font-size: 12px; margin-top: 4px; }
.widget { margin: 14px 0; }
table { border-collapse: collapse; margin: 8px 0; width: 100%; }
td, th { border: 1px solid #223041; padding: 4px 10px; font-size: 13px; }
.mapview { margin: 14px 0; }
.map-svg svg { max-width: 100%; height: auto; background: #0e141c;
               border: 1px solid #223041; border-radius: 10px; }
.warn { color: #ffd27f; background: #241c0e; border: 1px solid #6b5320;
        border-radius: 8px; padding: 8px 12px; font-size: 13px; margin: 8px 0; }
.source { color: #8fa3b8; font-size: 12.5px; border-left: 3px solid #223041;
          padding-left: 10px; margin: 8px 0; }
footer.evidence { margin-top: 56px; color: #7d8b9d; font-size: 13px; }
</style>
</head>
<body>
<main id="brief-root"><p class="meta">正在解压简报…</p></main>
<script type="application/json" id="brief-bundle">__JSON__</script>
<script>__VIEWER__</script>
</body>
</html>
"""


def render_brief_html(bundle: Mapping[str, Any]) -> str:
    """bundle → 自解压单文件 HTML。"""
    spec = bundle.get("spec") or {}
    metadata = spec.get("metadata") or {}
    title = str(metadata.get("title") or "地图简报")
    title_escaped = (title.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
    html = _HTML_SHELL
    html = html.replace("__TITLE__", title_escaped)
    html = html.replace(
        "__VIEWER__", _VIEWER_JS.replace("</script>", "<\\/script>"))
    html = html.replace("__JSON__", _embed_json(bundle))
    if len(html.encode("utf-8")) > MAX_BRIEF_HTML_BYTES:
        raise ValueError(
            f"brief html exceeds {MAX_BRIEF_HTML_BYTES} bytes budget")
    return html


__all__ = [
    "BRIEF_BUNDLE_SCHEMA_VERSION",
    "MAX_BRIEF_HTML_BYTES",
    "sanitize_svg",
    "csv_safe",
    "build_fact_csv_rows",
    "rows_to_csv",
    "build_brief_bundle",
    "build_release_manifest",
    "render_brief_html",
]
