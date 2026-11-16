"""G47 出版三格式**独立回读**（WP10）。

生产链：``compute_layout`` → 冻结 artifact → ``render_export``：
SVG 是唯一矢量真源（真实 ``<circle>`` + ``data-*``），PNG 由 rsvg-convert 按
DPI 栅格化，PDF 由 WeasyPrint 排版（SVG 内联为 data URI）。

回读故意**不用**生成时的任何中间量：

* SVG 用 XML 解析器读圆集合/半径/数量/图例/参考轮廓/披露文本；
* PNG 用 PIL 读真实像素，量"橙色覆盖面积"，并核对 96/300 DPI 只改像素尺寸、
  不改面积比例（非等比拉伸会让两者对不上）；
* PDF 用 pypdf 读页几何，解压内容流确认橙填充真的被画进去。

"文件存在"或"容器能打开"都不算证据；这里检查的是内容。
"""
from __future__ import annotations

import base64
import fcntl
import io
import math
import os
import re
import shutil
import subprocess
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree

import pytest

from app.services.cartogram_authoring.compute import compute_layout
from app.services.cartogram_authoring.render import (
    render_export,
    svg_pixel_size,
)
from tests.unit.g47_fixtures import frozen, grid_features, plan

_SVG_NS = "{http://www.w3.org/2000/svg}"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK_PATH = Path(
    subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout.strip()
) / "webgis-heavy-stage.lock"

#: 等待同仓重阶段锁的上限（秒）。合法持有者可能是整包构建（数小时），长等无意义，
#: 默认只给 60s 让"锁被占"这件事快速变成一条可读的失败而不是挂死。
_LOCK_WAIT_SECONDS = float(os.environ.get("G47_LOCK_WAIT_SECONDS", "60"))
#: 本进程内是否已确认锁被外部持有 —— 避免 4 个加锁用例各等满一次。
_LOCK_BUSY = False

#: 3×3 网格，值 100..900 → 半径比 1:3，面积比 1:9（便于像素级核验）。
V9 = [100, 200, 300, 400, 500, 600, 700, 800, 900]


@contextmanager
def _heavy_stage_lock():
    """同仓统一重阶段锁（媒体编码不与其他 worktree 并行放大）。

    外部 worktree 长期持锁时**不**无限期阻塞：有界等待后失败，并给出锁路径与
    诊断方向。原先的 ``flock(LOCK_EX)`` 会静默挂死整份回读套件 —— 持锁者不在
    本 PID 命名空间时（容器/sandbox）连 ``/proc`` 都扫不到，除挂死外拿不到任何
    线索。首次等待失败后本进程内直接快速失败，不重复等满。

    ``G47_LOCK_BYPASS=1`` 是显式放行口，只在「锁被外部持有、仍要取得有界证据」
    时使用；它会打出横幅，账本必须注明**未持锁**，不得当作持锁通过。
    """
    global _LOCK_BUSY
    if os.environ.get("G47_LOCK_BYPASS") == "1":
        print(f"[g47-lock] BYPASS=1：未持共享重阶段锁即渲染（{_LOCK_PATH}）"
              " —— 有界证据，不等同于持锁通过", flush=True)
        yield
        return
    if _LOCK_BUSY:
        pytest.fail(f"共享重阶段锁已确认被外部持有：{_LOCK_PATH}", pytrace=False)

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+") as fh:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    _LOCK_BUSY = True
                    pytest.fail(
                        f"重阶段锁 {_LOCK_WAIT_SECONDS:.0f}s 内未取得：{_LOCK_PATH}\n"
                        "  flock LOCK_EX/LOCK_SH 皆 EAGAIN 而 POSIX lockf 可得 ⇒ 是其它\n"
                        "  worktree 的 flock 持有者；持锁者若不在本 PID 命名空间，/proc\n"
                        "  与 /proc/locks 都看不到它，无法定位。处置：等锁释放后重跑，或\n"
                        "  以 G47_LOCK_BYPASS=1 取得有界证据（账本须注明未持锁）。",
                        pytrace=False)
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@pytest.fixture(scope="module")
def layout() -> Dict[str, Any]:
    built = compute_layout(plan(), frozen(grid_features(values=V9), revision=7))
    assert built.layout["publication_ready"] is True
    return built.layout


def _parse_svg(svg_text: str) -> ElementTree.Element:
    return ElementTree.fromstring(svg_text)


def _svg_circles(root) -> List[Dict[str, float]]:
    out = []
    for el in root.iter(f"{_SVG_NS}circle"):
        if el.get("data-feature-id") is None:
            continue  # 图例参考圆没有 feature id，不参与布局核验
        out.append({
            "feature_id": el.get("data-feature-id"),
            "value": float(el.get("data-value")),
            "radius": float(el.get("r")),
            "data_radius": float(el.get("data-radius")),
            "cx": float(el.get("cx")),
            "cy": float(el.get("cy")),
        })
    return out


def _orange_mask(img):
    """橙色填充像素（#c2410c @0.78 → 约 (207,107,66)）：白底/灰线/文字都不算。"""
    px = img.convert("RGB").load()
    w, h = img.size
    mask = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r >= 150 and b <= 120 and (r - b) >= 80:
                mask[y * w + x] = 1
    return mask


def _orange_area(img) -> int:
    return sum(_orange_mask(img))


def _png_size(img) -> tuple:
    return img.size


# ── SVG：矢量真源 ───────────────────────────────────────────────────────

def test_svg_is_selfcontained_xml_with_one_circle_per_drawn_unit(layout):
    svg, media, name = render_export(layout, "svg")
    assert media == "image/svg+xml"
    assert name.endswith(".svg")
    root = _parse_svg(svg.decode("utf-8"))
    circles = _svg_circles(root)
    assert len(circles) == len(layout["circles"]) == 9
    assert {c["feature_id"] for c in circles} == {
        c["feature_id"] for c in layout["circles"]}
    # 自包含：没有外部引用（否则下载的单文件会缺图）
    text = svg.decode("utf-8")
    assert "http://" not in text.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "<image" not in text


def test_svg_radii_equal_the_frozen_artifact(layout):
    root = _parse_svg(render_export(layout, "svg")[0].decode("utf-8"))
    by_id = {c["feature_id"]: c for c in layout["circles"]}
    for c in _svg_circles(root):
        expected = round(float(by_id[c["feature_id"]]["radius"]), 2)
        assert c["radius"] == pytest.approx(expected, abs=0.01)
        assert c["data_radius"] == pytest.approx(
            float(by_id[c["feature_id"]]["radius"]), abs=1e-4)


def test_svg_area_ratio_equals_value_ratio(layout):
    """图元层面即可核对：``r²`` 比 == 数量比（读者就是这样比较面积的）。"""
    root = _parse_svg(render_export(layout, "svg")[0].decode("utf-8"))
    circles = sorted(_svg_circles(root), key=lambda c: c["value"])
    smallest, largest = circles[0], circles[-1]
    assert largest["value"] / smallest["value"] == pytest.approx(9.0)
    assert (largest["radius"] / smallest["radius"]) ** 2 == pytest.approx(
        9.0, rel=2e-3)
    # 逐个圆都要成立，不是只有极值成立
    base = circles[0]
    for c in circles[1:]:
        assert (c["radius"] / base["radius"]) ** 2 == pytest.approx(
            c["value"] / base["value"], rel=3e-3)


def test_svg_legend_carries_unit_and_reference_values(layout):
    text = render_export(layout, "svg")[0].decode("utf-8")
    root = _parse_svg(text)
    units = [el for el in root.iter(f"{_SVG_NS}text") if el.get("data-legend-unit")]
    assert units and "人" in "".join(units[0].itertext())
    assert "圆面积∝数量" in text
    ref_values = [float(el.get("data-legend-value"))
                  for el in root.iter(f"{_SVG_NS}circle")
                  if el.get("data-legend-value")]
    assert ref_values == [float(r["value"])
                          for r in layout["legend"]["reference_circles"]]


def test_svg_discloses_diagram_coordinates_and_adjacency(layout):
    text = render_export(layout, "svg")[0].decode("utf-8")
    assert "示意排布" in text and "非经纬度" in text
    assert "原邻接保留" in text
    # 源版本可追溯
    assert "ref:g47-fixture" in text and "rev 7" in text


def test_svg_makes_no_geographic_claim(layout):
    text = render_export(layout, "svg")[0].decode("utf-8")
    for token in ("EPSG", "longitude", "latitude", "4326", "3857"):
        assert token not in text


def test_svg_reference_outlines_are_real_paths(layout):
    root = _parse_svg(render_export(layout, "svg")[0].decode("utf-8"))
    paths = [el for el in root.iter(f"{_SVG_NS}path")
             if el.get("data-reference-id")]
    assert len(paths) == len(layout["reference"]["outlines"])
    for el in paths:
        d = el.get("d") or ""
        assert d.startswith("M ") and d.endswith(" Z")
        assert d.count("L ") >= 2


def test_unready_layout_is_banner_marked_in_svg():
    draft = compute_layout(plan(max_iterations=1),
                           frozen(grid_features(values=V9))).layout
    assert draft["publication_ready"] is False
    text = render_export(draft, "svg")[0].decode("utf-8")
    assert 'data-draft-banner="1"' in text
    assert "草稿" in text and "未达发布资格门" in text
    # 仍然给出真实圆（诊断价值），不是空白图
    root = _parse_svg(text)
    assert len(_svg_circles(root)) == 9


# ── PNG：真实像素 ───────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("rsvg-convert") is None,
                    reason="rsvg-convert 不可用")
def test_png_is_a_real_raster_with_painted_circles(layout, tmp_path):
    from PIL import Image

    with _heavy_stage_lock():
        png, media, name = render_export(layout, "png", dpi=96)
    assert media == "image/png" and name.endswith(".png")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    path = tmp_path / "g47.png"
    path.write_bytes(png)
    with Image.open(io.BytesIO(png)) as img:
        img.load()
        assert _png_size(img) == svg_pixel_size(layout, 96)
        area_px = _orange_area(img)
        # 期望覆盖面积 = π·s²·Σr²（s = 像素/示意图单位），抗锯齿边缘会略微低估
        extent = float(layout["extent"]["width"])
        s = img.size[0] / extent
        expected = math.pi * s * s * sum(
            float(c["radius"]) ** 2 for c in layout["circles"])
    assert area_px > 0, "PNG 里没有橙色填充 —— 圆没被画出来"
    assert area_px == pytest.approx(expected, rel=0.08)


@pytest.mark.skipif(shutil.which("rsvg-convert") is None,
                    reason="rsvg-convert 不可用")
def test_dpi_changes_pixel_size_but_not_area_ratio(layout):
    """96 → 300 DPI 只允许改像素密度；一旦非等比拉伸，两者对不上。"""
    from PIL import Image

    with _heavy_stage_lock():
        png96, _, _ = render_export(layout, "png", dpi=96)
        png300, _, _ = render_export(layout, "png", dpi=300)
    with Image.open(io.BytesIO(png96)) as a, Image.open(io.BytesIO(png300)) as b:
        a.load()
        b.load()
        size96, size300 = a.size, b.size
        area96, area300 = _orange_area(a), _orange_area(b)

    assert size96 == svg_pixel_size(layout, 96)
    assert size300 == svg_pixel_size(layout, 300)
    assert size300[0] / size96[0] == pytest.approx(300 / 96, rel=1e-3)
    assert size300[0] == size300[1], "图面必须是正方形（等比）"
    # 面积随 DPI 的平方变化；若有非等比拉伸，横向/纵向比例会不一致而偏离
    assert area300 / area96 == pytest.approx((300 / 96) ** 2, rel=0.05)


# ── PDF：真实页面与填充 ─────────────────────────────────────────────────

def _pdf_streams(raw: bytes) -> bytes:
    haystack = bytearray(raw)
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            haystack += zlib.decompress(match.group(1))
        except Exception:
            continue
    return bytes(haystack)


def test_pdf_has_page_geometry_and_painted_fill(layout):
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - 依赖缺席时明确跳过
        pytest.skip("pypdf 不可用")

    with _heavy_stage_lock():
        pdf, media, name = render_export(layout, "pdf")
    assert media == "application/pdf" and name.endswith(".pdf")
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")

    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    width_pt, height_pt = float(box.width), float(box.height)
    # A4 横向（842×595 pt，±2pt 容差来自 WeasyPrint 的舍入）
    assert width_pt == pytest.approx(842, abs=2)
    assert height_pt == pytest.approx(595, abs=2)
    assert width_pt > height_pt

    # 橙色填充真的进了内容流（#c2410c / 0.78 透明度 → 0.761 0.255 0.047 rg）
    streams = _pdf_streams(pdf)
    assert b"0.760784" in streams or b"0.761" in streams


def test_pdf_embeds_a_vector_figure_not_a_blank_page(layout):
    """区分"有页面"与"有内容"：内容流必须有绘图算子与足量字节。"""
    with _heavy_stage_lock():
        pdf, _, _ = render_export(layout, "pdf")
    streams = _pdf_streams(pdf)
    assert len(streams) > 5_000
    assert streams.count(b" rg") >= 1 or streams.count(b" RG") >= 1
    # 曲线路径（圆的贝塞尔近似）必须存在，否则只有一个空白矩形
    assert b" c\n" in streams or b" c " in streams


# ── 导出契约 ────────────────────────────────────────────────────────────

def test_export_names_are_traceable_and_format_specific(layout):
    names = {}
    for fmt in ("svg", "png", "pdf"):
        with _heavy_stage_lock() if fmt != "svg" else _nullcontext():
            _data, media, name = render_export(layout, fmt)
        names[fmt] = (media, name)
    digest = str(layout["candidate_digest"])[:12]
    for fmt, (media, name) in names.items():
        assert name == f"dorling-cartogram-{digest}.{fmt}"
    assert names["svg"][0] == "image/svg+xml"
    assert names["png"][0] == "image/png"
    assert names["pdf"][0] == "application/pdf"


def test_svg_export_round_trips_through_base64(layout):
    """出版 adapter 把 SVG 内联成 data URI —— 解回来必须是同一份图。"""
    svg, _, _ = render_export(layout, "svg")
    b64 = base64.b64encode(svg).decode("ascii")
    restored = base64.b64decode(b64)
    assert restored == svg
    assert restored.count(b"data-feature-id") == 9


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
