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


def _walk_coords_for_bbox(node: object, bounds: list[float]) -> None:
    """递归遍历 GeoJSON 几何坐标，原地更新 bounds=[w,s,e,n]。"""
    if isinstance(node, list):
        if node and isinstance(node[0], (int, float)) and len(node) >= 2:
            lng, lat = float(node[0]), float(node[1])
            if lng < bounds[0]: bounds[0] = lng
            if lat < bounds[1]: bounds[1] = lat
            if lng > bounds[2]: bounds[2] = lng
            if lat > bounds[3]: bounds[3] = lat
            return
        for child in node:
            _walk_coords_for_bbox(child, bounds)


def build_layer_schema(session_id: str, ref_id: str, sample_size: int = 5) -> dict | None:
    """从 session 里取 GeoJSON 数据，抽样推断 properties 字段名+类型 + 几何类型 + bbox。

    返回形如 {"geom":"Polygon", "count":123, "fields":{...}, "bbox":[w,s,e,n]}。
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
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    has_coords = False

    # 字段/几何类型只抽样，bbox 必须扫全量否则会偏小
    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if isinstance(gtype, str):
            geom_types.add(gtype)
        coords = geom.get("coordinates")
        if coords is not None:
            before = bounds[0]
            _walk_coords_for_bbox(coords, bounds)
            if bounds[0] != before or has_coords:
                has_coords = True
            elif bounds[0] != float("inf"):
                has_coords = True
        if idx < sample_size:
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

    bbox = bounds if has_coords and bounds[0] != float("inf") else None

    return {
        "geom": "/".join(sorted(geom_types)) if geom_types else None,
        "count": len(features),
        "fields": fields,
        "bbox": bbox,
    }


def _bbox_intersects(a: list[float], b: list[float]) -> bool:
    """两个 [w,s,e,n] 是否相交（含边界接触）。"""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _bbox_contains(outer: list[float], inner: list[float]) -> bool:
    """outer 是否完全包住 inner。"""
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def viewport_layer_relation(viewport_bounds: list[float] | None, layer_bbox: list[float] | None) -> str | None:
    """判断图层 bbox 相对当前视口的位置关系。

    返回 4 种之一：
    - "在视口内"  — 视口完全包住图层
    - "局部相交"  — 有交集但视口未完全包住
    - "在视口外"  — 无交集
    - None       — 任一边界缺失，无法判断
    """
    if not (isinstance(viewport_bounds, list) and len(viewport_bounds) >= 4):
        return None
    if not (isinstance(layer_bbox, list) and len(layer_bbox) >= 4):
        return None
    v = [float(x) for x in viewport_bounds[:4]]
    l = [float(x) for x in layer_bbox[:4]]
    if not _bbox_intersects(v, l):
        return "在视口外"
    if _bbox_contains(v, l):
        return "在视口内"
    return "局部相交"


def format_selected_feature(sel: dict | None) -> str | None:
    """把前端推上来的 selected_feature 渲染为单行可读文本。

    LLM 看到这一行就知道"用户刚点了哪个要素"——后续追问"这块面积多大"
    "查一下它的属性"不再需要反问坐标或图层。
    """
    if not isinstance(sel, dict):
        return None
    name_or_ref = sel.get("layer_name") or sel.get("ref_id") or sel.get("layer_id") or "?"
    point = sel.get("point")
    parts = [f"图层={name_or_ref}"]
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
            kvs = ", ".join(f"{k}={_short(v)}" for k, v in chosen[:4])
            parts.append(f"属性={{{kvs}}}")
    return " ".join(parts)


def _short(v: object, max_len: int = 30) -> str:
    s = str(v)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


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


def format_layer_schema(schema: dict, viewport_bounds: list[float] | None = None) -> str:
    """把 build_layer_schema 的输出渲染为单行紧凑文本，可选附加视口关系。"""
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

    bbox = schema.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        parts.append(f"bbox=[{bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f}]")
        relation = viewport_layer_relation(viewport_bounds, bbox)
        if relation:
            parts.append(relation)
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
        # Round 3: 异步预热的反查地名，命中才打印（未命中绝不阻塞）
        try:
            from app.services.viewport_naming import lookup as _vp_lookup, schedule_populate as _vp_schedule
            name = _vp_lookup(float(center[0]), float(center[1]))
            if name:
                lines.append(f"- 视口所在区域: {name}")
            else:
                # 缓存未命中：再触发一次预热（首轮兜底，下一轮就有了）
                _vp_schedule(float(center[0]), float(center[1]))
        except Exception as e:
            logger.debug(f"viewport_naming lookup skipped: {e}")
    else:
        lines.append("- 视口: 未知（前端尚未上报，回答位置类问题前请先告知用户无法获取地图状态）")

    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        w, s, e, n = bounds
        lines.append(f"- 可视范围: W{w:.3f} S{s:.3f} E{e:.3f} N{n:.3f}")

    from app.core.base_layers import format_base_layer_catalog
    lines.append(f"- 底图: {base_layer}")
    lines.append(f"- 可切换底图: {format_base_layer_catalog()}")

    selected = state.get("selected_feature")
    sel_line = format_selected_feature(selected)
    if sel_line:
        lines.append(f"- 选中要素: {sel_line}")

    layer_lines = format_layer_lines(
        inventory,
        active_layers,
        session_id=session_id,
        viewport_bounds=bounds if isinstance(bounds, (list, tuple)) and len(bounds) == 4 else None,
    )
    if layer_lines:
        lines.append("- 活跃图层:")
        lines.extend(f"  * {ln}" for ln in layer_lines)
    else:
        lines.append("- 活跃图层: 无")

    event_log = session_data_manager.get_event_log(session_id)
    tool_calls, user_actions, pending = _split_events(event_log)
    if pending:
        lines.append("- 进行中后台任务 (前端尚未回报完成):")
        for pe in pending:
            lines.append(f"  * {_format_pending_event(pe)}")
    if tool_calls:
        lines.append("- 近期工具调用:")
        for evt in tool_calls[-5:]:
            lines.append(f"  * {_format_tool_event(evt)}")
    if user_actions:
        lines.append("- 近期用户操作:")
        for evt in user_actions[-3:]:
            lines.append(f"  * {evt['event']}: {json.dumps(evt['data'], ensure_ascii=False)}")

    return "\n".join(lines)


# 哪些工具结果的 status 字段代表"后台异步任务，前端正在跑"
_PENDING_STATUSES = {
    "export_task_created",
    "export_batch_task_created",
    "change_detection_task_started",
    "analysis_task_started",
    "started",
}


def _split_events(event_log: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """把 event_log 拆成 (工具调用, 用户操作, 进行中任务)。

    进行中任务：最近 N 条工具调用里，status ∈ _PENDING_STATUSES 且尚未被后续同会话
    系统通知冲销的条目。这里我们不做对账（前端的 setPendingSystemMessage 不回写
    event_log），只用"最近 N 条里出现过 pending status"作为近似信号。
    """
    tool_calls: list[dict] = []
    user_actions: list[dict] = []
    pending: list[dict] = []
    for evt in event_log:
        if evt.get("event") == "tool_executed":
            tool_calls.append(evt)
            data = evt.get("data") or {}
            if data.get("status") in _PENDING_STATUSES:
                pending.append(evt)
        else:
            user_actions.append(evt)
    # 只看最近 3 个 pending（更早的多半已结束）
    return tool_calls, user_actions, pending[-3:]


def _format_tool_event(evt: dict) -> str:
    """格式化一条 tool_executed 事件，把关键字段拼到一行。"""
    data = evt.get("data") or {}
    tool = data.get("tool", "?")
    parts = [tool]
    if data.get("is_error"):
        err = data.get("error_msg") or ""
        parts.append(f"❌ {err}" if err else "❌")
    else:
        for k in ("command", "status", "ref", "layer_id", "feature_count", "alias"):
            v = data.get(k)
            if v is not None:
                parts.append(f"{k}={v}")
    return " ".join(parts)


def _format_pending_event(evt: dict) -> str:
    """格式化一条进行中后台任务事件，提示 LLM 不要重复触发。"""
    data = evt.get("data") or {}
    tool = data.get("tool", "?")
    status = data.get("status", "pending")
    cmd = data.get("command") or ""
    tail = f" → {cmd}" if cmd else ""
    return f"{tool} ({status}){tail} —— 等待前端完成后会通过 [系统通知] 回传，不要重复触发"


def format_layer_lines(
    inventory: dict,
    active_layers: list[dict],
    session_id: str | None = None,
    viewport_bounds: list[float] | None = None,
) -> list[str]:
    """渲染图层一行式描述。inventory 优先，缺失时回退到前端上报。

    当传入 session_id 时，额外把每个 ref 的属性 schema (字段+类型+几何+bbox) 拼到末行；
    传 viewport_bounds 时再附加"在视口内/外/局部相交"。
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
                        line += f" | {format_layer_schema(schema, viewport_bounds)}"
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


# 单次请求里给"历史对话"留的 token 预算（粗估）。
# 不含 system prompt / 环境感知 / plan / 工具 schema / 当前 user 消息。
# 模型自身的 context 极限通常 32k-128k，留 ~6k 给历史，剩下给其他部分。
# 真要调，把它移到 settings 即可。
HISTORY_TOKEN_BUDGET = 6000
HISTORY_MIN_TURNS = 2  # 至少保留最近 N 轮 user/assistant，绝不为节省 token 砍掉刚刚的对话


def _estimate_tokens(content: object) -> int:
    """超粗 token 估算：CJK 1 char ≈ 1.5 tokens，ASCII 4 char ≈ 1 token。

    精度只要不长期偏离 30% 就行——这里宁可高估也别低估，防止侥幸压线还是爆 context。
    """
    if content is None:
        return 0
    if isinstance(content, (list, dict)):
        import json as _json
        content = _json.dumps(content, ensure_ascii=False)
    if not isinstance(content, str):
        content = str(content)
    if not content:
        return 0
    cjk = sum(1 for c in content if "一" <= c <= "鿿")
    other = len(content) - cjk
    return int(cjk * 1.5 + other / 4) + 1


def _message_tokens(msg: dict) -> int:
    """估算单条消息总开销（content + tool_calls + tool_call_id 元数据）。"""
    total = _estimate_tokens(msg.get("content"))
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        total += _estimate_tokens(tool_calls)
    # 角色+少量结构开销
    return total + 4


def _group_into_turns(messages: list[dict]) -> list[list[dict]]:
    """把消息序列按 user 开头切成"轮次"。

    一轮 = 一个 user 消息 + 后面紧跟的所有 assistant/tool 消息，直到下一个 user。
    历史里没 user 开头的散落片段也作为独立轮（向后兼容）。
    """
    turns: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


def truncate_history_by_budget(
    history: list[dict],
    budget: int = HISTORY_TOKEN_BUDGET,
    min_turns: int = HISTORY_MIN_TURNS,
) -> tuple[list[dict], int]:
    """按 token 预算截断历史，返回 (保留下来的消息序列, 被丢弃的轮次数)。

    规则：
    - 把消息切成"轮次"（user 开头的连续段）
    - 从最新轮反向纳入，累计 token 不超预算
    - 永远至少保留最近 min_turns 轮，即使总和已超预算（最近的对话是最重要的）
    """
    if not history:
        return history, 0

    turns = _group_into_turns(history)
    if len(turns) <= min_turns:
        return history, 0

    kept_rev: list[list[dict]] = []
    used = 0
    for turn in reversed(turns):
        turn_cost = sum(_message_tokens(m) for m in turn)
        # 至少保留 min_turns，即使超预算也得收下
        if len(kept_rev) < min_turns:
            kept_rev.append(turn)
            used += turn_cost
            continue
        if used + turn_cost > budget:
            break
        kept_rev.append(turn)
        used += turn_cost

    kept = list(reversed(kept_rev))
    dropped = len(turns) - len(kept)
    if dropped <= 0:
        return history, 0
    flat = [m for turn in kept for m in turn]
    return flat, dropped


def _build_truncation_notice(dropped_turns: int) -> str:
    return (
        f"[历史折叠] 已省略最早 {dropped_turns} 轮对话以控制上下文长度。"
        f"完整历史仍保存在数据库中（如需引用旧分析结果，可通过 ref:xxx 直接调用）。"
    )


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

    # Round 5: 历史压缩 — 超预算时丢弃最早的轮次并注入折叠说明
    history, dropped = truncate_history_by_budget(messages[1:])
    if dropped > 0:
        head.append({"role": "system", "content": _build_truncation_notice(dropped)})
        logger.info(f"[HISTORY-TRUNC] session={session_id} dropped {dropped} turns")
    head.extend(history)
    return head
