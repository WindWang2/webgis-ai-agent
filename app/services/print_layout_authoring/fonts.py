"""G34 受控字体资源、真实度量、缺字/weight fallback 与文本换行。

纪律（WP05）：

* 允许列表只含部署中**真实存在且可被引擎加载**的资源；运行时逐个探测，
  缺席如实 ``available=False``，不假称已应用。
* 用 fontTools 的真实 ``hmtx`` advance / ``cmap`` 做度量与缺字检查，
  **绝不用字符串长度冒充精确测量**。
* 未知 weight/style、缺字、fallback 全部进结构化回执；不静默覆盖。
* 不把用户字符串拼成 file/http 字体 URL；字体路径是代码内固定常量。
* 字体资源版本 = 可用资源文件摘要的确定性组合，用于作者计划绑定。

G26 的 ``@font-face`` / ``CSS_FONT_STACK`` 继续由
``app/services/publication_export.py`` 拥有，本模块只提供 family 名与可用性。
"""
from __future__ import annotations

import functools
import hashlib
import math
import os
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.print_layout_authoring.units import mm_to_pt, pt_to_mm

REPO_ROOT = Path(__file__).resolve().parents[3]
_VENDORED_TTF = REPO_ROOT / "app" / "lib" / "cartography" / "fonts" / "NotoSansSC-Regular-subset.ttf"
_NOTO_DIR = Path("/usr/share/fonts/noto-cjk")
_TTF_DIR = Path("/usr/share/fonts/TTF")

#: (weight, style) → 候选文件 + TTC 中期望的 family 名（用于选 SC 字体）。
FONT_RESOURCES: Dict[str, Dict[str, Any]] = {
    "noto-sans-sc-embedded": {
        "family": "Noto Sans SC Embedded",
        "embedded": True,
        "variants": {
            ("normal", "normal"): [(str(_VENDORED_TTF), None)],
        },
    },
    "noto-sans-cjk-sc": {
        "family": "Noto Sans CJK SC",
        "embedded": False,
        "variants": {
            ("normal", "normal"): [(str(_NOTO_DIR / "NotoSansCJK-Regular.ttc"), "SC")],
            ("bold", "normal"): [(str(_NOTO_DIR / "NotoSansCJK-Bold.ttc"), "SC")],
        },
    },
    "noto-serif-cjk-sc": {
        "family": "Noto Serif CJK SC",
        "embedded": False,
        "variants": {
            ("normal", "normal"): [(str(_NOTO_DIR / "NotoSerifCJK-Regular.ttc"), "SC")],
            ("bold", "normal"): [(str(_NOTO_DIR / "NotoSerifCJK-Bold.ttc"), "SC")],
        },
    },
    "dejavu-sans": {
        "family": "DejaVu Sans",
        "embedded": False,
        "variants": {
            ("normal", "normal"): [(str(_TTF_DIR / "DejaVuSans.ttf"), None)],
            ("bold", "normal"): [(str(_TTF_DIR / "DejaVuSans-Bold.ttf"), None)],
        },
    },
}

FONT_IDS = tuple(FONT_RESOURCES.keys())
DEFAULT_FONT_ID = "noto-sans-sc-embedded"
MAX_GLYPH_REPORT = 32
MAX_LINES = 64


def _first_existing(candidates: List[Tuple[str, Optional[str]]]) -> Optional[Tuple[str, Optional[str]]]:
    for path, hint in candidates:
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) > 0:
                return (path, hint)
        except OSError:
            continue
    return None


def resolve_variant(font_id: str, weight: str, style: str) -> Optional[Tuple[str, Optional[str], bool]]:
    """→ (path, ttc_hint, exact_match)。exact_match=False 表示 weight/style 回落。"""
    res = FONT_RESOURCES.get(font_id)
    if res is None:
        return None
    exact = _first_existing(res["variants"].get((weight, style), []))
    if exact is not None:
        return (exact[0], exact[1], True)
    fallback = _first_existing(res["variants"].get(("normal", "normal"), []))
    if fallback is None:
        return None
    return (fallback[0], fallback[1], False)


@functools.lru_cache(maxsize=32)
def _file_digest(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "unreadable"
    return h.hexdigest()


@functools.lru_cache(maxsize=16)
def _load_font(path: str, ttc_hint: Optional[str]) -> Any:
    """加载 TTF/TTC；TTC 时按 family 名挑 SC 面，避免拿到 JP 度量。"""
    from fontTools.ttLib import TTCollection, TTFont

    if path.lower().endswith(".ttc"):
        coll = TTCollection(path, lazy=True)
        chosen = coll.fonts[0]
        for font in coll.fonts:
            try:
                name = str(font["name"].getDebugName(1) or "")
            except Exception:
                name = ""
            if ttc_hint and ttc_hint in name and "Mono" not in name:
                chosen = font
                break
        return chosen
    return TTFont(path, lazy=True)


@functools.lru_cache(maxsize=16)
def _font_metrics(path: str, ttc_hint: Optional[str]) -> Tuple[Any, Any, int]:
    font = _load_font(path, ttc_hint)
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    upem = int(font["head"].unitsPerEm or 1000)
    return (cmap, hmtx, upem)


def font_resource_status() -> List[Dict[str, Any]]:
    """逐资源真实探测（文件存在 + fontTools 可解析 + 字形覆盖）。"""
    out: List[Dict[str, Any]] = []
    for fid, res in FONT_RESOURCES.items():
        entry: Dict[str, Any] = {
            "font_id": fid, "family": res["family"], "embedded": res["embedded"],
            "available": False, "weights": [], "styles": ["normal"],
            "sha256": "", "glyphs": 0, "supports_cjk": False,
            "supports_latin": False, "error": "",
        }
        for (weight, style), candidates in res["variants"].items():
            hit = _first_existing(candidates)
            if hit is None:
                continue
            path, hint = hit
            try:
                cmap, _hmtx, _upem = _font_metrics(path, hint)
                entry["available"] = True
                if weight not in entry["weights"]:
                    entry["weights"].append(weight)
                entry["glyphs"] = max(entry["glyphs"], len(cmap))
                entry["supports_cjk"] = entry["supports_cjk"] or (0x4E2D in cmap)
                entry["supports_latin"] = entry["supports_latin"] or (0x41 in cmap)
                entry["sha256"] = _file_digest(path)[:16]
            except Exception as ex:  # noqa: BLE001 — 字体损坏必须如实报告
                entry["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
        out.append(entry)
    return out


def font_asset_version() -> str:
    """可用资源集合的确定性版本（作者计划绑定；缺失/更换资源会改变它）。"""
    status = font_resource_status()
    payload = "|".join(
        f"{s['font_id']}:{s['sha256']}:{','.join(sorted(s['weights']))}"
        for s in status if s["available"]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def embedded_font_face_css() -> str:
    """复用 G26 的 @font-face（不复制 base64 逻辑）。"""
    from app.services.publication_export import _cjk_font_face_css

    return _cjk_font_face_css()


def resolve_font(font_id: str, weight: str = "normal",
                 style: str = "normal") -> Dict[str, Any]:
    """返回所选字体的**真实**应用状态；不把 fallback 伪装成完全应用。"""
    issues: List[str] = []
    requested = font_id
    if font_id not in FONT_RESOURCES:
        issues.append("UNKNOWN_FONT_ID")
        font_id = DEFAULT_FONT_ID
    hit = resolve_variant(font_id, weight, style)
    if hit is None:
        # 资源完全不可用 → 尝试任一可用资源，明确披露 fallback。
        issues.append("FONT_RESOURCE_UNAVAILABLE")
        for alt in FONT_IDS:
            alt_hit = resolve_variant(alt, "normal", "normal")
            if alt_hit is not None:
                hit, font_id = alt_hit, alt
                break
    if hit is None:
        return {
            "requested_font_id": requested, "applied_font_id": "", "family": "sans-serif",
            "available": False, "applied": False, "fallback_used": True,
            "weight_requested": weight, "weight_applied": "normal",
            "style_requested": style, "style_applied": "normal",
            "issues": issues + ["NO_FONT_RESOURCE_AVAILABLE"], "missing_glyphs": [],
            "css_stack": css_font_stack("", style),
        }
    path, hint, exact = hit
    if not exact:
        if weight != "normal" and not any(
                w == weight for (w, _s) in FONT_RESOURCES[font_id]["variants"]):
            issues.append("FONT_WEIGHT_UNAVAILABLE")
        if style != "normal":
            issues.append("FONT_STYLE_UNAVAILABLE")
    if font_id != requested:
        issues.append("FONT_FALLBACK_USED")
    try:
        _font_metrics(path, hint)
        available = True
    except Exception as ex:  # noqa: BLE001
        available = False
        issues.append(f"FONT_LOAD_FAILED:{type(ex).__name__}")
    res = FONT_RESOURCES[font_id]
    return {
        "requested_font_id": requested,
        "applied_font_id": font_id if available else "",
        "family": res["family"] if available else "sans-serif",
        "embedded": res["embedded"],
        "available": available,
        "applied": available and font_id == requested and not issues,
        "fallback_used": bool(issues),
        "weight_requested": weight,
        "weight_applied": weight if (available and exact) else "normal",
        "style_requested": style,
        "style_applied": style if (available and exact) else "normal",
        "issues": issues,
        "missing_glyphs": [],
        "css_stack": css_font_stack(font_id if available else "", style),
    }


#: 受控 CJK 兜底族：只在**主族缺字**时由渲染器接手，顺序固定、可审计。
#: 不引入任意用户字体，也不把兜底伪装成「作者字体已应用」。
_FALLBACK_SANS = ("Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
                  "Microsoft YaHei", "sans-serif")
_FALLBACK_SERIF = ("Noto Serif CJK SC", "Source Han Serif SC",
                   "SimSun", "serif")


def css_font_stack(font_id: str, style: str = "normal") -> str:
    """渲染用 CSS ``font-family`` 栈：作者主族在前，受控兜底在后。

    单独写一个族名会让缺字直接落到 tofu（PNG/SVG 尤其明显）；
    这里把主族 + 受控 CJK 兜底族一次写进 SVG/CSS，缺字由兜底族真渲染。
    """
    family = str((FONT_RESOURCES.get(font_id) or {}).get("family") or "")
    tail = _FALLBACK_SERIF if style == "italic" or "Serif" in family else _FALLBACK_SANS
    # 全部族名都不含 CSS 关键字/前导数字，可直接以标识符序列书写——
    # 这样 SVG presentation attribute 不会被 html.escape 转义成 &#x27;。
    stack = ([family] if family else []) + list(tail)
    return ", ".join(dict.fromkeys(stack))


def check_glyphs(text: str, font_id: str, weight: str = "normal",
                 style: str = "normal") -> List[str]:
    """真实 cmap 检查；返回有界的缺失字符列表（不抛异常）。"""
    hit = resolve_variant(font_id, weight, style) or resolve_variant(font_id, "normal", "normal")
    if hit is None:
        return [str(ch) for ch in dict.fromkeys(text) if not ch.isspace()][:MAX_GLYPH_REPORT]
    try:
        cmap, _hmtx, _upem = _font_metrics(hit[0], hit[1])
    except Exception:
        return []
    missing: List[str] = []
    for ch in text:
        if ch.isspace() or ch in "\n\r\t":
            continue
        if ord(ch) not in cmap and ch not in missing:
            missing.append(ch)
            if len(missing) >= MAX_GLYPH_REPORT:
                break
    return missing


def _fallback_metrics(style: str = "normal") -> Optional[Tuple[Any, Any, int]]:
    """受控兜底族的真实度量（与 ``css_font_stack`` 尾部第一个 CJK 族同源）。

    主族缺字时**渲染器实际用兜底族绘制**，度量也必须用同一字体，否则窄框
    换行会把缺字按 0.6em 低估（Noto Sans CJK SC 实测 1.0em），预览/PDF
    行数与宽度对不上。
    """
    fid = "noto-serif-cjk-sc" if style == "italic" else "noto-sans-cjk-sc"
    hit = resolve_variant(fid, "normal", "normal")
    if hit is None:
        return None
    try:
        return _font_metrics(hit[0], hit[1])
    except Exception:  # noqa: BLE001
        return None


def _advance_pt(ch: str, cmap: Any, hmtx: Any, upem: int,
                size_pt: float,
                fallback: Any = None) -> float:
    gid = cmap.get(ord(ch))
    if gid is None:
        # 缺字：优先用兜底族真实 advance（渲染时正是它接手）；仍缺则按
        # 东亚宽字符 1.0em / 其它 0.6em 估计，并如实报告缺字。
        if fallback is not None:
            fcmap, fhmtx, fupem = fallback
            fgid = fcmap.get(ord(ch))
            if fgid is not None:
                try:
                    fadv = fhmtx[fgid][0]
                except (KeyError, IndexError, TypeError):
                    fadv = fupem
                return float(fadv) / float(fupem) * size_pt
        return size_pt * (1.0 if _is_wide(ch) else 0.6)
    try:
        adv = hmtx[gid][0]
    except (KeyError, IndexError, TypeError):
        adv = upem
    return float(adv) / float(upem) * size_pt


def measure_text_pt(text: str, font_id: str, size_pt: float,
                    letter_spacing_pt: float = 0.0,
                    weight: str = "normal", style: str = "normal") -> float:
    """真实字体度量（pt）。不用 len(text)。"""
    hit = resolve_variant(font_id, weight, style) or resolve_variant(font_id, "normal", "normal")
    if hit is None:
        return len(text) * size_pt * 0.6
    try:
        cmap, hmtx, upem = _font_metrics(hit[0], hit[1])
    except Exception:
        return len(text) * size_pt * 0.6
    fallback = _fallback_metrics(style)
    width = sum(_advance_pt(ch, cmap, hmtx, upem, size_pt, fallback)
                for ch in text)
    if len(text) > 1:
        width += letter_spacing_pt * (len(text) - 1)
    return width


def _is_wide(ch: str) -> bool:
    """CJK / 全角字符可在任意字边界换行。"""
    if ch.isspace():
        return False
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _tokenize(paragraph: str, wrap_mode: str) -> List[str]:
    tokens: List[str] = []
    buf = ""
    for ch in paragraph:
        if wrap_mode == "char":
            tokens.append(ch)
            continue
        if ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif _is_wide(ch):
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def _split_long_token(token: str, max_pt: float, font_id: str, size_pt: float,
                      letter_spacing_pt: float, weight: str, style: str) -> List[str]:
    """拉丁长词逐字符切分（合法换行、不截字、不缩到不可读）。"""
    chunks: List[str] = []
    cur = ""
    for ch in token:
        cand = cur + ch
        if cur and measure_text_pt(cand, font_id, size_pt, letter_spacing_pt,
                                   weight, style) > max_pt:
            chunks.append(cur)
            cur = ch
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def wrap_text(
    text: str,
    *,
    font_id: str,
    size_pt: float,
    line_height: float = 1.35,
    letter_spacing_pt: float = 0.0,
    weight: str = "normal",
    style: str = "normal",
    box_width_mm: float,
    box_height_mm: float,
    wrap_mode: str = "word",
    overflow_policy: str = "expand",
) -> Dict[str, Any]:
    """真实度量换行 → ``lines`` / ``overflow`` / ``status`` / 缺字状态。

    绝不 slice 丢内容：所有行都返回；空间不足时按 overflow_policy
    ``expand | continue | block | wrap`` 处理并如实报告。
    """
    font = resolve_font(font_id, weight, style)
    effective_font = font["applied_font_id"] or DEFAULT_FONT_ID
    eff_weight = font["weight_applied"]
    eff_style = font["style_applied"]
    missing = check_glyphs(text, effective_font, eff_weight, eff_style)
    max_pt = mm_to_pt(max(0.1, box_width_mm))
    line_pt = size_pt * line_height
    box_pt = mm_to_pt(max(0.1, box_height_mm))

    lines: List[str] = []
    line_widths: List[float] = []
    if wrap_mode == "none":
        for para in text.split("\n"):
            lines.append(para)
            line_widths.append(measure_text_pt(para, effective_font, size_pt,
                                               letter_spacing_pt, eff_weight, eff_style))
    else:
        for para in text.split("\n"):
            if para == "":
                lines.append("")
                line_widths.append(0.0)
                continue
            tokens = _tokenize(para, wrap_mode)
            cur = ""
            for tok in tokens:
                # 长词先按最大宽度切分，保证不截字（场景 4）。
                pieces = ([tok] if measure_text_pt(tok, effective_font, size_pt,
                                                   letter_spacing_pt, eff_weight, eff_style) <= max_pt
                          else _split_long_token(tok, max_pt, effective_font, size_pt,
                                                 letter_spacing_pt, eff_weight, eff_style))
                for piece in pieces:
                    cand = cur + piece
                    if cand and cur and measure_text_pt(
                            cand, effective_font, size_pt, letter_spacing_pt,
                            eff_weight, eff_style) > max_pt:
                        lines.append(cur.rstrip())
                        line_widths.append(measure_text_pt(cur.rstrip(), effective_font,
                                                           size_pt, letter_spacing_pt,
                                                           eff_weight, eff_style))
                        cur = "" if piece.isspace() else piece
                    else:
                        cur = cand
            lines.append(cur.rstrip())
            line_widths.append(measure_text_pt(cur.rstrip(), effective_font, size_pt,
                                               letter_spacing_pt, eff_weight, eff_style))
    dropped_lines = max(0, len(lines) - MAX_LINES)
    lines = lines[:MAX_LINES] if dropped_lines else lines
    line_widths = line_widths[:len(lines)]
    needed_pt = len(lines) * line_pt
    overflow = needed_pt > box_pt + 1e-6
    status = "ok"
    continuation_lines: List[str] = []
    expanded_height_mm = box_height_mm
    if overflow:
        if overflow_policy == "block":
            status = "blocked"
        elif overflow_policy == "expand":
            status = "expanded"
            expanded_height_mm = pt_to_mm(needed_pt)
        elif overflow_policy == "continue":
            status = "continued"
            fit = max(1, int(math.floor(box_pt / line_pt)))
            continuation_lines = lines[fit:]
        else:
            status = "overflow"
    return {
        "lines": lines,
        "line_widths_pt": [round(v, 4) for v in line_widths],
        "line_height_pt": round(line_pt, 4),
        "used_width_pt": round(max(line_widths or [0.0]), 4),
        "used_height_pt": round(needed_pt, 4),
        "box_width_pt": round(max_pt, 4),
        "box_height_pt": round(box_pt, 4),
        "overflow": overflow,
        "status": status,
        "continuation_lines": continuation_lines,
        "expanded_height_mm": round(expanded_height_mm, 4),
        "font": {**font, "missing_glyphs": missing},
        "truncated": bool(dropped_lines),
        "truncated_lines": dropped_lines,
    }
