"""Formatters and untrusted value sanitization for LLM prompt context."""

_UNTRUSTED_MAX_LEN = 500

# Untrusted source XML fence boundary tag constants
TAG_UNTRUSTED_LAYER_NAME = "untrusted_layer_name"
TAG_UNTRUSTED_FEATURE_PROPERTY = "untrusted_feature_property"
TAG_UNTRUSTED_LAYER_ALIAS = "untrusted_layer_alias"
TAG_UNTRUSTED_LAYER_TYPE = "untrusted_layer_type"
TAG_UNTRUSTED_BASE_LAYER = "untrusted_base_layer"
TAG_UNTRUSTED_REGION_NAME = "untrusted_region_name"
TAG_UNTRUSTED_USER_ACTION = "untrusted_user_action"


def _untrusted(v: object, max_len: int = _UNTRUSTED_MAX_LEN) -> str:
    """Render a user/third-party value safe to splice into the [环境感知] block.

    - Coerces to str.
    - HTML-escapes `<`, `>`, `&` so an attacker can't close an enclosing tag or
      open a `<system>`-style fake.
    - Truncates at `max_len` to cap the size of any single injected field.
    """
    s = str(v)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short(v: object, max_len: int = 30) -> str:
    s = str(v)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _xml_fence(tag: str, v: object, max_len: int = _UNTRUSTED_MAX_LEN) -> str:
    """Wrap an untrusted value inside specific XML tags after HTML-escaping it."""
    escaped = _untrusted(v, max_len)
    return f"<{tag}>{escaped}</{tag}>"


def format_selected_feature(sel: dict | None) -> str | None:
    """把前端推上来的 selected_feature 渲染为单行可读文本。

    LLM 看到这一行就知道"用户刚点了哪个要素"——后续追问"这块面积多大"
    "查一下它的属性"不再需要反问坐标或图层。
    """
    if not isinstance(sel, dict):
        return None
    name_or_ref = sel.get("layer_name") or sel.get("ref_id") or sel.get("layer_id") or "?"
    point = sel.get("point")
    # /review P1-4: layer_name comes from user-uploaded GeoJSON; escape before splicing
    parts = [f"图层={_xml_fence(TAG_UNTRUSTED_LAYER_NAME, name_or_ref)}"]
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        try:
            parts.append(f"点击@{float(point[0]):.4f},{float(point[1]):.4f}")
        except (ValueError, TypeError):
            pass
    props = sel.get("properties")
    if isinstance(props, dict) and props:
        # 优先展示常见标签字段，否则取前 4 个属性
        label_keys = ("name", "title", "label", "id", "OBJECTID")
        chosen: list[tuple[str, object]] = []
        for k in label_keys:
            if k in props and props[k] is not None:
                chosen.append((k, props[k]))
        if not chosen:
            chosen = [(k, v) for k, v in list(props.items())[:4] if v is not None]
        if chosen:
            # /review P1-4: both keys and values come from user-uploaded GeoJSON; wrap in semantic XML tags
            kvs = ", ".join(
                _xml_fence(TAG_UNTRUSTED_FEATURE_PROPERTY, f"{k}={_short(v)}")
                for k, v in chosen[:4]
            )
            parts.append(f"属性={{{kvs}}}")
    return " ".join(parts)


def format_style_summary(style: dict | None) -> str | None:
    """把 layer.style 渲染成单行紧凑文本。支持 choropleth / lisa / 普通色。"""
    if not isinstance(style, dict):
        return None
    stype = style.get("type")
    if stype == "choropleth":
        breaks = style.get("breaks") or []
        if isinstance(breaks, list) and len(breaks) >= 2:
            k = len(breaks) - 1
            br_str = f"{breaks[0]:.2f}~{breaks[-1]:.2f}"
        else:
            k = 0
            br_str = "?"
        return f"专题图 field={style.get('field','?')} 分级={k} 范围={br_str}"
    if stype == "lisa":
        return f"LISA 空间自相关 field={style.get('field','?')} (HH/LL/HL/LH/NS)"
    color = style.get("color") or style.get("fill_color")
    if color:
        return f"色={color}"
    return None
