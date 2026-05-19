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

    lines.append(f"- 底图: {base_layer}")

    layer_lines = format_layer_lines(inventory, active_layers)
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


def format_layer_lines(inventory: dict, active_layers: list[dict]) -> list[str]:
    """渲染图层一行式描述。inventory 优先，缺失时回退到前端上报。"""
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
            if meta.get("style", {}).get("color"):
                attrs.append(f"色={meta['style']['color']}")
            tail = f" [{', '.join(attrs)}]" if attrs else ""
            out.append(f"{ref_id}{tail} ({status})")
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
    last_ctx = build_last_analysis_context(messages)
    if last_ctx:
        head.append({"role": "system", "content": last_ctx})
    head.extend(messages[1:])
    return head
