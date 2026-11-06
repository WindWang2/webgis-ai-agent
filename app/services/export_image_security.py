"""Active-content and format boundaries for persisted map exports."""
from __future__ import annotations

import io
import re
from fastapi import HTTPException

_TAGS = frozenset({"svg", "g", "defs", "style", "title", "desc", "metadata", "a", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "textPath", "image", "clipPath", "mask", "pattern", "linearGradient", "radialGradient", "stop"})
_ATTRS = frozenset({"id", "version", "xmlns", "class", "style", "x", "y", "x1", "x2", "y1", "y2", "width", "height", "viewBox", "preserveAspectRatio", "transform", "opacity", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit", "points", "d", "cx", "cy", "r", "rx", "ry", "dx", "dy", "offset", "stop-color", "stop-opacity", "clip-path", "clip-rule", "clipPathUnits", "mask", "maskUnits", "maskContentUnits", "patternUnits", "patternContentUnits", "patternTransform", "gradientUnits", "gradientTransform", "spreadMethod", "font-family", "font-size", "font-style", "font-weight", "text-anchor", "dominant-baseline", "letter-spacing", "vector-effect", "href", "space"})


def _local_tag(name: str) -> str:
    return name.split("}", 1)[-1]


def _safe_declarations(text: str) -> str:
    import tinycss2
    declarations = []
    for item in tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration" or item.lower_name not in _ATTRS:
            continue
        value = tinycss2.serialize(item.value).strip()
        if "url(" in value.lower() and not re.fullmatch(r"url\(#[A-Za-z0-9_.:-]+\)", value):
            continue
        if "expression(" in value.lower():
            continue
        declarations.append(f"{item.lower_name}:{value}")
    return ";".join(declarations)


def _safe_stylesheet(text: str) -> str:
    import tinycss2
    rules = []
    for item in tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True):
        if item.type == "qualified-rule":
            declarations = _safe_declarations(tinycss2.serialize(item.content))
            if declarations:
                rules.append(tinycss2.serialize(item.prelude) + "{" + declarations + "}")
    return "".join(rules)


def sanitize_svg(content: bytes) -> bytes:
    from defusedxml import ElementTree as safe_xml
    from xml.etree import ElementTree
    try:
        root = safe_xml.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise HTTPException(400, "SVG XML is invalid or contains forbidden declarations") from exc
    if _local_tag(root.tag) != "svg":
        raise HTTPException(400, "SVG root must be svg")
    for parent in root.iter():
        if _local_tag(parent.tag) == "style":
            parent.text = _safe_stylesheet(parent.text or "")
        for child in list(parent):
            if _local_tag(child.tag) not in _TAGS:
                parent.remove(child)
        for key, value in list(parent.attrib.items()):
            name = _local_tag(key)
            safe = name in _ATTRS
            if name == "style":
                parent.attrib[key] = _safe_declarations(value)
                continue
            if name == "href":
                safe = value.startswith("#") or bool(re.match(r"^data:image/(png|jpeg);base64,", value, re.I))
            if "url(" in value.lower():
                safe = safe and bool(re.fullmatch(r"url\(#[A-Za-z0-9_.:-]+\)", value))
            if not safe:
                del parent.attrib[key]
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def verify_bitmap(content: bytes, extension: str) -> None:
    from PIL import Image
    expected = "PNG" if extension == ".png" else "JPEG"
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if image.format != expected or not (0 < width <= 16384 and 0 < height <= 16384) or width * height > 100_000_000:
                raise ValueError("format or pixel budget mismatch")
            image.verify()
    except Exception as exc:
        raise HTTPException(400, "Export image is invalid or exceeds its pixel budget") from exc
