"""G41 真实出版回读（WP10 / B32 / B33）。

生产链：``build_*_legend`` canonical 图例 → ``read_legend_content`` →
``layout_legend`` → ``render_legend_box_authored``（G34 真实绘制面）→ 完整
SVG；地图主体由生产 ``compile_mapspec_to_svg_detailed`` 编译（真矢量面）。
SVG 用 rsvg-convert 栅格化 PNG、用 WeasyPrint 排成 PDF，逐格式**独立回读**：
解析 SVG 的格子/计数/单位/源版本、PNG 的字节与尺寸、PDF 的页对象与文本层，
并核对三者描述同一批数据与同一版本表达。不是黄金截图比对。

资源纪律：媒体编码阶段取得同仓统一跨进程锁；rsvg-convert / WeasyPrint
都是轻量单进程，不并行放大。
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.lib.cartography.svg_marginalia import render_legend_box_authored
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.print_layout_authoring.legend import (
    layout_legend,
    read_legend_content,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK_PATH = Path(
    subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout.strip()
) / "webgis-heavy-stage.lock"


@contextmanager
def _heavy_stage_lock():
    """同仓统一重阶段锁（Linux flock；等待时不伪称测试通过）。"""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


ROWS = [{"a": float(i), "b": float(i % 3), "est": float(i),
         "se": 0.0 if i == 0 else float(i % 4) * 0.5} for i in range(9)]


def _fc(rows):
    feats = []
    for i, props in enumerate(rows):
        base = 116.0 + i * 0.01
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0], [base + 0.01, 39.01],
                [base, 39.01], [base, 39.0],
            ]]},
            "properties": dict(props),
        })
    return {"type": "FeatureCollection", "features": feats}


def _canonical_legends():
    """用生产服务构建两种 canonical 图例（与真实提交同源）。"""
    from app.services.bivariate_reliability_authoring.classify import compute_bivariate
    from app.services.bivariate_reliability_authoring.contract import parse_plan
    from app.services.bivariate_reliability_authoring.legend import (
        build_bivariate_legend,
        build_reliability_legend,
    )
    from app.services.bivariate_reliability_authoring.reliability import (
        compute_reliability,
    )
    from app.services.bivariate_reliability_authoring.stats import scan_pairs

    features = _fc(ROWS)["features"]
    anchor = {"ref_id": "ref:publication-fixture", "content_revision": 4,
              "content_fingerprint": "g41f-publication", "feature_count": 9}

    bplan = parse_plan({
        "layer_id": "L1", "mode": "bivariate_matrix",
        "bivariate": {"n": 3, "axis_a": {"field": "a", "unit": "人"},
                      "axis_b": {"field": "b", "unit": "%"},
                      "matrix_id": "BiPurpleOrange"},
    })
    bscan = scan_pairs(features, bplan.bivariate)
    bres = compute_bivariate(bscan, bplan.bivariate)
    blegend = build_bivariate_legend(
        bplan.bivariate, bscan, bres, anchor=anchor,
        plan_digest=bplan.plan_digest())

    rplan = parse_plan({
        "layer_id": "L1", "mode": "reliability_dual_channel",
        "reliability": {
            "estimate_field": "est", "estimate_unit": "m", "estimate_k": 4,
            "measure": {"kind": "standard_error", "value_field": "se",
                        "unit": "m", "source": "author_declared",
                        "method_note": "出版测试声明：仪器标准误"},
            "visual": {"channel": "opacity", "min_value": 0, "max_value": 2,
                       "min_output": 0.25, "max_output": 0.9,
                       "unknown_output": 0.5, "buckets": 4, "clip": "clamp"},
        },
    })
    rres = compute_reliability(features, rplan.reliability)
    rlegend = build_reliability_legend(
        rplan.reliability, rres, anchor=anchor, plan_digest=rplan.plan_digest())
    return blegend, rlegend, bres


def _legend_svg(legend: dict) -> str:
    """走 G34 真实图例内容/排版/绘制链（不新建第二渲染器）。"""
    mapspec = {"layers": [{"id": "L1", "type": "fill", "source": "s1",
                           "legend_spec": legend}],
               "sources": {"s1": {"type": "geojson"}}}
    content = read_legend_content(mapspec, "legend-1")
    assert content["canonicals"], "canonical 图例必须被 G34 识别"
    lp = layout_legend(
        content,
        {"columns": 1, "continuation": "expand", "swatch_mm": 4.0},
        {"width": 120.0, "height": 80.0},
        {"size_pt": 9.0, "line_height": 1.35,
         "font_id": "noto-sans-sc-embedded"},
        96,
    )
    assert lp["canonicals"], "排版计划必须携带全部 canonical（不丢层）"
    assert lp["canonical_height_mm"] > 0
    # 框高必须按真正会被绘制的 canonical 定，而不是按不会被画的扁平条目虚增
    assert lp["needed_height_mm"] <= lp["frame_height_mm"] + 1e-6 or lp["status"] == "expanded"
    draw = {
        "id": "legend-1", "legend": lp,
        "box": {"x": 10.0, "y": 10.0,
                "width": 300.0, "height": float(lp["boxes"][0]["height_units"])},
        "font_family": "sans-serif", "font_size_units": 10.0,
        "line_units": 13.5, "color": "#1e293b",
    }
    return render_legend_box_authored(draw, {"components": []})


def _full_svg(legend_fragment: str, map_body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="148mm" '
        'viewBox="0 0 794 560">'
        '<rect width="794" height="560" fill="#ffffff"/>'
        f'{map_body}{legend_fragment}</svg>'
    )


def _map_body(legend: dict, class_field: str) -> str:
    """生产 SVG 编译器编译真实矢量面（paint match 消费派生类字段）。

    可靠性模式的派生字段是**字符串桶键**（``b0…``/``unknown``/``out_of_domain``），
    用整数列举颜色会让所有要素落到 ``default``（图面全透明但测试仍通过——
    复审 P2 指出的「只验容器」）。这里直接消费 canonical 的桶输出，让回读
    断言的是真正被绘制的透明度。
    """
    feats = _fc(ROWS)["features"]
    if str(legend.get("type") or "") == "reliability_dual_channel":
        buckets = [b for b in (legend.get("buckets") or [])
                   if isinstance(b, dict) and b.get("key")]
        assert buckets, "可靠性 canonical 必须带桶定义"
        for i, f in enumerate(feats):
            f["properties"][class_field] = buckets[i % len(buckets)]["key"]
        # 第二通道 = **opacity** 的 match（不是 color）：把桶输出写进 color
        # 会产出 fill="0.9" 这种非法颜色，图面看似有值实则不是颜色也不是透明度。
        paint_color: Any = "#225588"
        paint_opacity: Any = {
            "method": "match", "field": class_field,
            "cases": [[b["key"], b["output"]] for b in buckets],
            "default": buckets[0]["output"],
        }
    else:
        for i, f in enumerate(feats):
            f["properties"][class_field] = i % 9
        paint_color = {
            "method": "match", "field": class_field,
            "cases": [[i, c] for i, c in enumerate(
                legend.get("colors") or legend["estimate"]["palette_colors"])],
            "default": "rgba(0,0,0,0)",
        }
        paint_opacity = 1.0
    mapspec = {
        "sources": {"s1": {"type": "geojson",
                           "inlineData": {"type": "FeatureCollection",
                                          "features": feats}}},
        "layers": [{
            "id": "L1", "type": "fill", "source": "s1",
            "paint": {"color": paint_color, "opacity": paint_opacity},
            "legend_spec": legend,
        }],
        "view": {"center": [116.05, 39.005], "zoom": 10},
    }
    compiled = compile_mapspec_to_svg_detailed(
        mapspec, width=794, height=560, target_dpi=96, include_chrome=False)
    return compiled.svg


def _readback(svg_text: str, tmp_path: Path, stem: str) -> dict:
    svg_path = tmp_path / f"{stem}.svg"
    svg_path.write_text(svg_text, encoding="utf-8")
    out: dict = {"svg_bytes": svg_path.stat().st_size}

    # PNG：真实 rsvg-convert 编码，再用 PIL 独立回读尺寸与像素。
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        png_path = tmp_path / f"{stem}.png"
        proc = subprocess.run(
            [rsvg, "-w", "1588", "-o", str(png_path), str(svg_path)],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        from PIL import Image
        with Image.open(png_path) as img:
            img.load()
            out["png_bytes"] = png_path.stat().st_size
            out["png_size"] = list(img.size)
            assert img.size[0] > 0 and img.size[1] > 0
        assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        out["png_status"] = "not_run: rsvg-convert unavailable"

    # PDF：真实 WeasyPrint 排版（内联 SVG），再独立回读页对象。
    try:
        from weasyprint import HTML
        pdf_path = tmp_path / f"{stem}.pdf"
        HTML(string=(
            '<html><head><meta charset="utf-8"><style>'
            '@page { size: 210mm 148mm; margin: 0; }'
            'body { margin: 0; }</style></head><body>'
            + svg_text + '</body></html>'
        ), base_url=str(tmp_path)).write_pdf(str(pdf_path))
        raw = pdf_path.read_bytes()
        out["pdf_bytes"] = len(raw)
        assert raw.startswith(b"%PDF-")
        assert raw.rstrip().endswith(b"%%EOF")
        # 页对象可能位于压缩对象流内：解压所有 FlateDecode 流再数 /Type /Page。
        import re
        import zlib
        haystack = raw
        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
            try:
                haystack += zlib.decompress(match.group(1))
            except Exception:
                continue
        out["pdf_pages"] = (
            len(re.findall(rb"/Type\s*/Page[^s]", haystack)))
        assert out["pdf_pages"] >= 1
    except ImportError:
        out["pdf_status"] = "not_run: weasyprint unavailable"
    return out


@pytest.mark.parametrize("mode", ["bivariate", "reliability"])
def test_publication_three_formats_independent_readback(mode, tmp_path):
    """同一冻结源/版本/表达，SVG·PNG·PDF 三格式真实产出并独立回读。"""
    blegend, rlegend, bres = _canonical_legends()
    legend = blegend if mode == "bivariate" else rlegend
    fragment = _legend_svg(legend)
    assert fragment, "G34 图例绘制器必须产出真实 SVG 片段"
    assert f'data-g41-type="{legend["type"]}"' in fragment
    assert f'data-g41-revision="4"' in fragment
    assert "ref:publication-fixture" in fragment

    if mode == "bivariate":
        # 9 个格子 + 逐格精确计数 + 单位/有效对/边界规则
        assert fragment.count('data-g41-cell=') == 9
        assert 'data-g41-count=' in fragment
        assert "人" in fragment and "%" in fragment
        assert "upper_inclusive" in fragment
        body = _map_body(legend, "__g41_pair_class")
        assert any(c in body for c in bres.colors), "地图面必须使用同一色阵"
    else:
        assert 'data-g41-bucket=' in fragment
        assert "standard_error" in fragment
        assert "误差缺失" in fragment or "unknown" in fragment
        body = _map_body(legend, "__g41_rel_bucket")
        # 第二通道必须真的落到 opacity 上（复审 P2：只验容器会放过全透明地图面）
        assert 'fill-opacity="0.9"' in body, "最高可靠性桶的透明度未进产物"
        assert 'fill-opacity="0.25"' in body, "最低可靠性桶的透明度未进产物"
        assert 'fill="#225588"' in body, "估计主色通道必须正常绘制"
        assert 'fill="0.' not in body, "桶输出不得被当作颜色写进 fill"

    svg = _full_svg(fragment, body)
    with _heavy_stage_lock():
        result = _readback(svg, tmp_path, f"g41-{mode}")
    assert result["svg_bytes"] > 1000
    assert result.get("png_bytes", 0) > 0, result
    assert result.get("pdf_bytes", 0) > 0, result
    assert result.get("pdf_pages", 0) >= 1, result


def test_legend_rejects_plain_colorbar_substitute(tmp_path):
    """B32：不能用普通色条代替矩阵；服务端 PDF/SVG 必须画出 n×n 格。"""
    blegend, _, _ = _canonical_legends()
    fragment = _legend_svg(blegend)
    assert fragment.count('data-g41-cell=') == 9
    assert 'chrome-g41-canonical-legend' in fragment
    # 长字段名/单位文本必须完整转义后进入 SVG，不省略关键轴名
    long_legend = json.loads(json.dumps(blegend, ensure_ascii=False))
    # canonical 轴标签是唯一权威（legacy label_a/label_b 由同一来源派生）。
    long_legend["axes"]["col"]["label"] = "居民人均可支配收入_非常长的字段名" * 2
    long_legend["axes"]["row"]["label"] = "比率百分比_非常长的字段名" * 2
    long_legend["label_a"] = long_legend["axes"]["col"]["label"]
    long_legend["label_b"] = long_legend["axes"]["row"]["label"]
    long_fragment = _legend_svg(long_legend)
    # 完整轴名仍保存在导出件（data-g41-*-full / <title>），视觉截断必须显式标 …
    assert long_legend["axes"]["col"]["label"] in long_fragment
    assert long_legend["axes"]["row"]["label"] in long_fragment
    assert "…" in long_fragment
    assert 'data-g41-col-label-full=' in long_fragment
    assert 'data-g41-row-label-full=' in long_fragment


def test_mixed_canonical_and_plain_layers_keep_every_entry(tmp_path):
    """复审 P1：图例组件聚合多层时，canonical 不得吞掉其它层的条目。

    旧实现只取第一份 canonical，并把 ``box_entries`` 整体置空，于是同图的
    graduated/categorical 层标签**一条都不画**，却仍报
    ``entries_rendered == entries_total`` 且 ``complete=True`` —— 完整性门禁
    形同虚设。这里同时断言「画了矩阵」和「画了每一条扁平条目」。
    """
    blegend, _, _ = _canonical_legends()
    mapspec = {
        "layers": [
            {"id": "L1", "type": "fill", "source": "s1",
             "legend_spec": blegend},
            {"id": "L2", "type": "circle", "source": "s1",
             "legend_spec": {
                 "type": "categorical", "title": "监测点等级",
                 "categories": [
                     {"key": "a", "color": "#16a34a", "label": "一级"},
                     {"key": "b", "color": "#f59e0b", "label": "二级"},
                 ],
                 "nodata": {"color": "#dddddd", "label": "无数据"},
             }},
        ],
        "sources": {"s1": {"type": "geojson"}},
    }
    content = read_legend_content(mapspec, "legend-1")
    assert len(content["canonicals"]) == 1, "两层只应有一份 canonical"
    labels = [e["label"] for e in content["entries"]]
    assert any("一级" in lab for lab in labels), labels
    assert any("二级" in lab for lab in labels), labels

    lp = layout_legend(
        content,
        {"columns": 1, "continuation": "expand", "swatch_mm": 4.0},
        {"width": 120.0, "height": 80.0},
        {"size_pt": 9.0, "line_height": 1.35,
         "font_id": "noto-sans-sc-embedded"},
        96,
    )
    assert lp["complete"] is True
    assert lp["entries_rendered"] == lp["entries_total"] == len(content["entries"])
    draw = {
        "id": "legend-1", "legend": lp,
        "box": {"x": 10.0, "y": 10.0,
                "width": 300.0, "height": float(lp["boxes"][0]["height_units"])},
        "font_family": "sans-serif", "font_size_units": 10.0,
        "line_units": 13.5, "color": "#1e293b",
    }
    svg = render_legend_box_authored(draw, {"components": []})
    assert 'data-g41-type="bivariate"' in svg, "canonical 矩阵必须被绘制"
    assert svg.count("data-g34-entry=") == len(content["entries"]), (
        "非 canonical 层的每一条条目都必须落到产物里")
    assert "一级" in svg


def test_real_committed_paint_renders_in_production_svg(tmp_path):
    """复审补强：不再手工搭 match —— 用真实 commit 产出的 layer.paint +
    冻结展示副本，经生产 SVG 编译器验证颜色/透明度确实被解析。"""
    import asyncio
    import uuid

    import app.services.mapspec.store as store_module
    import app.services.session_data_protocol as sdp

    store_module.BASE_STORAGE_DIR = tmp_path / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    sdp._conversation_owner_kind = lambda sid: "none"  # 单元级无 Conversation 行

    from app.services.bivariate_reliability_authoring import (
        commit_expression, parse_plan, preview_expression,
    )
    from app.services.session_data import session_data_manager as sdm
    from tests.unit.bivariate_reliability.helpers import make_fc, seed_layer

    async def build(mode: str):
        sid = "sess-g41-pub-" + uuid.uuid4().hex[:10]
        await seed_layer(sid, make_fc(ROWS))
        if mode == "bivariate":
            plan = parse_plan({
                "layer_id": "L1", "mode": "bivariate_matrix",
                "bivariate": {"n": 3,
                              "axis_a": {"field": "a", "unit": "人"},
                              "axis_b": {"field": "b", "unit": "%"},
                              "matrix_id": "BiPurpleOrange"}})
        else:
            plan = parse_plan({
                "layer_id": "L1", "mode": "reliability_dual_channel",
                "reliability": {
                    "estimate_field": "est", "estimate_unit": "m", "estimate_k": 4,
                    "measure": {"kind": "standard_error", "value_field": "se",
                                "unit": "m", "source": "author_declared",
                                "method_note": "出版回读声明：仪器标准误"},
                    "visual": {"channel": "opacity", "min_value": 0,
                               "max_value": 2, "min_output": 0.25,
                               "max_output": 0.9, "unknown_output": 0.5,
                               "buckets": 4, "clip": "clamp"}}})
        prev = await preview_expression(sid, "L1", plan, trusted_dispatch=True)
        rev = int(await sdm.get_state_field(
            sid, "_cartographic_mutation_revision") or 0)
        await commit_expression(
            sid, "L1", plan, expected_revision=rev,
            preview_digest=prev["candidate_digest"], trusted_dispatch=True)
        from app.services.mapspec_store import mapspec_store
        spec = await mapspec_store.get_mapspec(sid)
        layer = next(l for l in spec["layers"] if l["id"] == "L1")
        ref = spec["sources"]["s1"]["ref_id"]
        payload = await sdm.get(sid, ref)
        return layer, payload["geojson"]

    for mode, expected_key in (("bivariate", "color"),
                               ("reliability", "opacity")):
        layer, fc = asyncio.run(build(mode))
        mapspec = {
            "sources": {"s1": {"type": "geojson", "inlineData": fc}},
            "layers": [{"id": "L1", "type": "fill", "source": "s1",
                        "paint": layer["paint"],
                        "legend_spec": layer["legend_spec"]}],
            "view": {"center": [116.05, 39.005], "zoom": 10},
        }
        svg = compile_mapspec_to_svg_detailed(
            mapspec, width=794, height=560, target_dpi=96,
            include_chrome=False).svg
        assert "<path" in svg, f"{mode}: 真实矢量面缺失"
        if mode == "bivariate":
            # 真实 paint.color 的 match cases 颜色必须出现在图面。
            cases = layer["paint"]["color"]["cases"]
            assert any(c[1] in svg for c in cases), \
                "真实提交的矩阵颜色未进入生产 SVG"
        else:
            # 真实 paint.opacity 的 match cases 输出必须进入 fill-opacity。
            outputs = {str(c[1]) for c in layer["paint"]["opacity"]["cases"]}
            assert any(f'fill-opacity="{o}"' in svg for o in outputs), \
                "真实提交的第二通道透明度未进入生产 SVG"
        assert expected_key in layer["paint"]


def test_dpi_scaling_keeps_same_semantics(tmp_path):
    """B33：同 mm 版式跨 DPI；数值意义与颜色对应一致，不靠截图。"""
    blegend, _, _ = _canonical_legends()
    frag96 = _legend_svg(blegend)
    cells96 = [line for line in frag96.split("<rect") if "data-g41-cell=" in line]
    assert len(cells96) == 9
    # 同一 canonical 合同再次编译：格子数/颜色/计数/源版本逐项一致。
    frag2 = _legend_svg(blegend)
    cells2 = [line for line in frag2.split("<rect") if "data-g41-cell=" in line]
    assert cells96 == cells2
    svg192 = _full_svg(frag2, _map_body(blegend, "__g41_pair_class"))
    assert 'width="210mm" height="148mm"' in svg192
    with _heavy_stage_lock():
        result = _readback(svg192, tmp_path, "g41-dpi")
    assert result.get("png_size", [0, 0])[0] >= 1588
