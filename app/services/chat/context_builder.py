"""上下文消息组装（M1 深水区拆出）：

把每轮 LLM 请求要插的『系统提示 + 实时感知 + 最近对话上下文』组装逻辑
从 ChatEngine 抽成纯函数。依赖只剩 session_data_manager（已是模块级单例）。

公开 API：
- `build_map_state_summary(session_id) -> str` — 实时感知（[环境感知]）
- `format_layer_lines(inventory, active_layers) -> list[str]` — 图层一行描述
- `build_last_analysis_context(messages) -> str` — 最近对话上下文摘要
- `compose_request_messages(session_id, messages) -> list[dict]` — 装配最终消息列表

ChatEngine 仍保留 _underscore 同名薄包装做 re-export，外部调用方不破。
"""
from __future__ import annotations

import datetime
import json
import logging
import re

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


def _infer_field_type(value: object) -> str:
    """粗略推断字段类型，输出 4 个值之一：number/string/bool/null。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def build_layer_schema(session_id: str, ref_id: str, sample_size: int = 5) -> dict | None:
    """从 session 里取 GeoJSON 数据，抽样推断 properties 字段名+类型 + 几何类型。

    返回形如 {"geom": "Polygon", "count": 123, "fields": {"pop":"number","name":"string"}}。
    LRU 已经在 manager.get() 内部维护，这里不再额外更新顺序。
    数据不存在或不是 FeatureCollection 时返回 None。
    """
    data = session_data_manager.get(session_id, ref_id)
    if not isinstance(data, dict):
        return None
    features = data.get("features")
    if not isinstance(features, list) or not features:
        return None

    geom_types: set[str] = set()
    field_types: dict[str, set[str]] = {}
    for feat in features[:sample_size]:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if isinstance(gtype, str):
            geom_types.add(gtype)
        props = feat.get("properties") or {}
        if isinstance(props, dict):
            for k, v in props.items():
                # 跳过样式注入字段（apply_layer_style 写进来的），它们不是业务属性
                if k in {"fill_color", "opacity", "stroke_width", "__style__"}:
                    continue
                field_types.setdefault(str(k), set()).add(_infer_field_type(v))

    # 多型字段合成：bool+null→bool；number+null→number；混合→mixed
    fields: dict[str, str] = {}
    for k, types in field_types.items():
        types.discard("null")
        if not types:
            fields[k] = "null"
        elif len(types) == 1:
            fields[k] = next(iter(types))
        else:
            fields[k] = "mixed"

    return {
        "geom": "/".join(sorted(geom_types)) if geom_types else None,
        "count": len(features),
        "fields": fields,
    }


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


def format_layer_schema(schema: dict) -> str:
    """把 build_layer_schema 的输出渲染为单行紧凑文本。"""
    parts: list[str] = []
    if schema.get("geom"):
        parts.append(f"geom={schema['geom']}")
    if schema.get("count") is not None:
        parts.append(f"n={schema['count']}")
    fields = schema.get("fields") or {}
    if fields:
        # 限制最多 8 个字段，避免上下文爆
        items = list(fields.items())[:8]
        field_str = ", ".join(f"{k}:{t}" for k, t in items)
        if len(fields) > 8:
            field_str += f", ...(+{len(fields) - 8})"
        parts.append(f"fields=[{field_str}]")
    return " ".join(parts)


def build_map_state_summary(session_id: str) -> str:
    """构造一份紧凑的当前地图状态摘要（[环境感知] 系统消息）。

    双源策略：优先用后端 inventory 的 ref_id 数据引用；inventory 为空时
    回退到前端 map_state.layers 上报的活跃图层（页面刷新/新 Session 时）。
    只输出事实，不在 prompt 里夹杂"应该怎么做"的元指令。
    """
    state = session_data_manager.get_map_state(session_id)
    inventory = session_data_manager.list_refs(session_id)
    viewport = state.get("viewport") or {}
    center = viewport.get("center")
    zoom = viewport.get("zoom")
    bearing = viewport.get("bearing", 0) or 0
    pitch = viewport.get("pitch", 0) or 0
    bounds = viewport.get("bounds")
    base_layer = state.get("base_layer", "OSM 地图")
    is_3d = state.get("is_3d", False)
    active_layers = state.get("layers", []) or []

    lines = [
        "[环境感知 — 当前地图实时状态，必读，不要凭空假设位置]",
        f"- 时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    user_location = state.get("user_location")
    if isinstance(user_location, dict):
        lines.append(
            f"- 用户位置: {user_location.get('lng', 0):.6f}, {user_location.get('lat', 0):.6f} "
            f"(±{user_location.get('accuracy', '?')}m)"
        )
    else:
        lines.append("- 用户位置: 未授权")

    if isinstance(center, (list, tuple)) and len(center) == 2 and zoom is not None:
        viewport_line = (
            f"- 视口中心(WGS84 经纬度): lng={center[0]:.4f}, lat={center[1]:.4f}, zoom={zoom:.2f}"
        )
        if bearing:
            viewport_line += f", bearing={bearing:.0f}°"
        if pitch:
            viewport_line += f", pitch={pitch:.0f}°"
        if is_3d:
            viewport_line += ", 3D"
        lines.append(viewport_line)
    else:
        lines.append("- 视口: 未知（前端尚未上报，回答位置类问题前请先告知用户无法获取地图状态）")

    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        w, s, e, n = bounds
        lines.append(f"- 可视范围: W{w:.3f} S{s:.3f} E{e:.3f} N{n:.3f}")

    from app.core.base_layers import format_base_layer_catalog
    lines.append(f"- 底图: {base_layer}")
    lines.append(f"- 可切换底图: {format_base_layer_catalog()}")

    layer_lines = format_layer_lines(inventory, active_layers, session_id=session_id)
    if layer_lines:
        lines.append("- 活跃图层:")
        lines.extend(f"  * {ln}" for ln in layer_lines)
    else:
        lines.append("- 活跃图层: 无")

    event_log = session_data_manager.get_event_log(session_id)
    if event_log:
        lines.append("- 近期操作:")
        for evt in event_log[-5:]:
            lines.append(f"  * {evt['event']}: {json.dumps(evt['data'], ensure_ascii=False)}")

    return "\n".join(lines)


def format_layer_lines(inventory: dict, active_layers: list[dict], session_id: str | None = None) -> list[str]:
    """渲染图层一行式描述。inventory 优先，缺失时回退到前端上报。

    当传入 session_id 时，额外把每个 ref 的属性 schema (字段+类型+几何) 拼到末行。
    """
    visibility_map = {l.get("id"): l for l in active_layers if l.get("id")}
    out: list[str] = []
    if inventory:
        for ref_id, alias in inventory.items():
            meta = visibility_map.get(ref_id) or next(
                (m for aid, m in visibility_map.items() if aid in ref_id or ref_id in aid),
                {},
            )
            visible = meta.get("visible")
            status = "可见" if visible is True else "隐藏" if visible is False else "未知"
            attrs = []
            if alias:
                attrs.append(f"别名={alias}")
            if meta.get("type"):
                attrs.append(f"类型={meta['type']}")
            if meta.get("featureCount") is not None:
                attrs.append(f"要素={meta['featureCount']}")
            style_str = format_style_summary(meta.get("style"))
            if style_str:
                attrs.append(style_str)
            tail = f" [{', '.join(attrs)}]" if attrs else ""
            line = f"{ref_id}{tail} ({status})"
            if session_id:
                try:
                    schema = build_layer_schema(session_id, ref_id)
                    if schema:
                        line += f" | {format_layer_schema(schema)}"
                except Exception as e:
                    logger.debug(f"build_layer_schema failed for {ref_id}: {e}")
            out.append(line)
        return out
    for layer in active_layers:
        lid = layer.get("id", "unknown")
        name = layer.get("name", lid)
        attrs = []
        if layer.get("type"):
            attrs.append(f"类型={layer['type']}")
        if layer.get("featureCount") is not None:
            attrs.append(f"要素={layer['featureCount']}")
        opacity = layer.get("opacity", 1.0)
        attrs.append(f"不透明度={opacity:.0%}")
        style_str = format_style_summary(layer.get("style"))
        if style_str:
            attrs.append(style_str)
        status = "可见" if layer.get("visible") else "隐藏"
        out.append(f"{name} (id={lid}, {', '.join(attrs)}) ({status})")
    return out


_REF_RE = re.compile(r"(ref:[\w-]+)")


def build_last_analysis_context(messages: list[dict]) -> str:
    """从最近的历史消息中提取分析上下文摘要，帮 LLM 维持追问连贯性。

    遇到简短追问（"换个颜色"、"再放大点"、"画热力图"）时，让 LLM 能直接接续
    上一轮的区域 / 数据对象 / 分析类型，不要反问已经在前文说清楚的事。
    """
    last_user_msg = ""
    last_assistant_msg = ""
    data_refs: list[str] = []

    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "assistant" and content and not last_assistant_msg:
            last_assistant_msg = content[:300]
        elif role == "user" and content and not last_user_msg:
            last_user_msg = content[:200]
        if "ref:" in content:
            data_refs.extend(_REF_RE.findall(content))
        if last_assistant_msg and last_user_msg:
            break

    if not last_assistant_msg and not last_user_msg:
        return ""

    ctx = "[最近对话上下文]\n"
    if last_user_msg:
        ctx += f"- 用户上一次请求：{last_user_msg}\n"
    if last_assistant_msg:
        ctx += f"- 你上一次回复摘要：{last_assistant_msg}...\n"
    if data_refs:
        unique_refs = list(dict.fromkeys(data_refs))[-5:]
        ctx += f"- 可复用的数据引用：{', '.join(unique_refs)}\n"
    ctx += (
        "\n如果用户的新消息是简短的追问（如「绘制热力图」「换个颜色」「放大看看」），"
        "请基于以上上下文直接执行，不要重新询问区域或数据。"
    )
    return ctx


def build_plan_block(plan) -> str:
    """把 Plan 渲染成 [执行计划] 系统块，步骤带 ✅/⬜ 完成标记。

    存在未完成步骤时追加一行提醒——这就是 Checkpoint 式的「末尾校验」：
    每轮都注入，LLM 在决定最终回复那一轮自然看到，不硬拦截。
    """
    lines = [
        "[执行计划] — 你为本任务制定的步骤，按此推进，完成一步即视为打勾",
        f"- 意图: {plan.intent}",
    ]
    if plan.steps:
        lines.append("- 步骤:")
        for step in plan.steps:
            mark = "✅" if step.done else "⬜"
            lines.append(f"  {mark} {step.n}. {step.goal}")
        if any(not s.done for s in plan.steps):
            lines.append(
                "⚠️ 仍有未完成步骤。若要给出最终回复，请先确认这些步骤是否"
                "已无必要，或在回复中向用户说明未完成的原因。"
            )
    return "\n".join(lines)


def compose_request_messages(session_id: str, messages: list[dict]) -> list[dict]:
    """组装一次 LLM 请求的消息列表：SYSTEM_PROMPT + 实时感知 + (可选)对话上下文摘要 + 历史。

    感知状态只在每次请求前注入一次，不再写进工具结果里——保持历史紧凑。
    chat() 与 chat_stream() 共享此入口，避免两条路径行为漂移。
    """
    if not messages:
        return []

    env_summary = build_map_state_summary(session_id)
    logger.debug(f"[ENV-INJECT] session={session_id}\n{env_summary}")

    # Merge env summary directly into the system prompt so it is always read.
    # Injecting it as a separate system message is unreliable — many LLMs
    # (including MiniMax) silently drop all but the first system entry.
    sys_msg = dict(messages[0])
    sys_msg["content"] = sys_msg.get("content", "") + "\n\n" + env_summary

    head = [sys_msg]

    # 注入 [执行计划] 块（若本会话存在活跃计划）
    from app.services.chat.planner import get_plan
    plan = get_plan(session_id)
    if plan is not None:
        head.append({"role": "system", "content": build_plan_block(plan)})

    last_ctx = build_last_analysis_context(messages)
    if last_ctx:
        head.append({"role": "system", "content": last_ctx})
    head.extend(messages[1:])
    return head
