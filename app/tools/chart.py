"""图表生成 FC 工具"""
import html
import json
import logging
from typing import Any

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "pie", "scatter"}
MAX_DATA_PAYLOAD_SIZE = 100 * 1024  # 100KB raw JSON limit
MAX_DATA_POINTS = 500  # Maximum data points to prevent browser lag
MAX_STRING_LENGTH = 200  # Max length for title/labels


def _sanitize_string(value: str, max_length: int = MAX_STRING_LENGTH) -> str:
    """Sanitize string input: escape HTML and truncate"""
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    # Escape HTML special characters to prevent XSS
    sanitized = html.escape(value.strip())
    # Truncate to prevent DoS via long strings
    return sanitized[:max_length]


def _validate_data_point(point: Any, chart_type: str) -> tuple[bool, str]:
    """Validate a single data point structure"""
    if not isinstance(point, dict):
        return False, "each data point must be an object"

    # Check for prototype pollution
    if "__proto__" in point or "constructor" in point or "prototype" in point:
        return False, "invalid property name in data point"

    if chart_type == "scatter":
        # Scatter requires name, x, y
        if "name" not in point or "x" not in point or "y" not in point:
            return False, "scatter plot points require name, x, and y"
        if not isinstance(point.get("x"), (int, float)) or not isinstance(point.get("y"), (int, float)):
            return False, "scatter plot x and y must be numbers"
        if not isinstance(point.get("name"), str):
            return False, "scatter plot name must be a string"
        # Validate finite numbers
        if not (float('-inf') < point["x"] < float('inf')) or not (float('-inf') < point["y"] < float('inf')):
            return False, "x and y values must be finite numbers"
    else:
        # Bar, line, pie require name, value
        if "name" not in point or "value" not in point:
            return False, "data points require name and value"
        if not isinstance(point.get("value"), (int, float)):
            return False, "value must be a number"
        if not isinstance(point.get("name"), str):
            return False, "name must be a string"
        # Validate finite number
        if not (float('-inf') < point["value"] < float('inf')):
            return False, "value must be a finite number"

    return True, ""


def _as_number(value: Any) -> "float | None":
    """数值宽容转换：bool 排除，数字/数字字符串 → float，其余 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_geojson_feature(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "Feature"
        and isinstance(item.get("properties"), dict)
    )


def _map_features_to_points(
    features: list,
    chart_type: str,
    x_field: str,
    y_field: str,
    name_field: str,
) -> "tuple[list | None, str]":
    """GeoJSON 要素列表 / 纯记录数组 → 图表数据点。

    bar/line/pie: name = name_field 或 x_field 的属性值，value = y_field。
    scatter: x = x_field，y = y_field，name = name_field 或要素序号。
    2026-08-25 会话实录：LLM 传 ref + x_field/y_field 被旧契约整体拒绝 ——
    字段映射是真实使用需求，不是参数幻觉。要素（带 properties 包装）与
    裸记录 dict（properties 即本体，如 [{"ct_name":..,"count":..}]）都收。
    """
    points: list = []
    for i, feature in enumerate(features):
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else feature
        if not isinstance(props, dict):
            props = {}
        if chart_type == "scatter":
            if not x_field or not y_field:
                return None, "scatter 图从 GeoJSON 取数需要同时指定 x_field 与 y_field"
            x = _as_number(props.get(x_field))
            y = _as_number(props.get(y_field))
            if x is None or y is None:
                return None, (
                    f"feature[{i}] 的 {x_field}/{y_field} 不是可用数值 "
                    f"(x={props.get(x_field)!r}, y={props.get(y_field)!r})"
                )
            name = str(props.get(name_field)) if name_field and props.get(name_field) is not None else f"要素{i + 1}"
            points.append({"name": name, "x": x, "y": y})
        else:
            if not y_field:
                return None, "从 GeoJSON 取数需要 y_field 指定数值字段（bar/line/pie 的取值）"
            value = _as_number(props.get(y_field))
            if value is None:
                return None, f"feature[{i}] 的 {y_field} 不是可用数值 ({props.get(y_field)!r})"
            name: Any = None
            # 名称回退链：显式 name_field → x_field → 字面 "name" 列（记录
            # 数组的自然命名，与 {name,value} 约定一致）
            for field in (name_field, x_field, "name"):
                if field and props.get(field) is not None:
                    name = props.get(field)
                    break
            points.append({
                "name": str(name) if name is not None else f"要素{i + 1}",
                "value": value,
            })
    return points, ""


def validate_chart_payload(chart: Any) -> "str | None":
    """公共 ChartData 校验（map chart_panel 与 chat 图表共用同一契约）。

    输入应为 {type,title,data[,x_label,y_label]} dict；返回 None=合法，
    否则返回错误信息。复用 generate_chart 的全部 DoS/XSS 防线。
    """
    if not isinstance(chart, dict):
        return "chart 必须是对象 {type,title,data}"
    effective_type = str(chart.get("type") or "").strip().lower()
    if effective_type not in VALID_CHART_TYPES:
        return f"chart.type 必须是 {', '.join(sorted(VALID_CHART_TYPES))} 之一"
    title = chart.get("title")
    if not isinstance(title, str) or not title.strip():
        return "chart.title 不能为空"
    data = chart.get("data")
    if not isinstance(data, list):
        return "chart.data 必须是数组"
    if len(data) == 0:
        return "chart.data 不能为空"
    if len(data) > MAX_DATA_POINTS:
        return f"chart.data 超过 {MAX_DATA_POINTS} 点上限（请聚合/降采样）"
    for i, point in enumerate(data):
        is_valid, error_msg = _validate_data_point(point, effective_type)
        if not is_valid:
            return f"chart.data[{i}]: {error_msg}"
    return None


def generate_chart(chart_type: str = "", title: str = "", data: Any = "",
                   x_label: str = "", y_label: str = "",
                   x_field: str = "", y_field: str = "", name_field: str = "",
                   type: str = "") -> dict:
    """生成图表配置数据，供前端渲染（同步核心——chat 图表路径不变）。

    data 三种形态：[{name,value}] / [{name,x,y}] JSON 数组字符串；已解析的
    列表/FeatureCollection（registry 会在调用前把 ref: 引用解引用成存储的
    GeoJSON 本体）；GeoJSON 要素列表。GeoJSON 形态必须配合 x_field/y_field
    做字段映射。type 是 chart_type 的别名（LLM 常见命名）。
    """
    effective_type = (chart_type or type or "").strip().lower()
    if effective_type not in VALID_CHART_TYPES:
        return {"error": f"Invalid chart_type. Must be one of: {', '.join(sorted(VALID_CHART_TYPES))}"}

    # Sanitize string inputs (XSS protection)
    safe_title = _sanitize_string(title, MAX_STRING_LENGTH)
    safe_x_label = _sanitize_string(x_label, MAX_STRING_LENGTH)
    safe_y_label = _sanitize_string(y_label, MAX_STRING_LENGTH)

    if not safe_title:
        return {"error": "title cannot be empty"}

    # Accept pre-parsed list/dict (some LLM providers pass parsed args directly)
    if isinstance(data, (list, dict)):
        parsed_data = data
    elif isinstance(data, str) and data.strip().startswith("ref:"):
        return {"error": f"数据引用 {data.strip()[:40]} 未能解引用 —— 请确认是当前会话产出的 ref 后重试"}
    else:
        if not isinstance(data, str):
            return {"error": "Invalid data format"}
        # DoS protection: check payload size before parsing
        if len(data) > MAX_DATA_PAYLOAD_SIZE:
            return {"error": f"Data payload too large (max {MAX_DATA_PAYLOAD_SIZE // 1024}KB)"}
        try:
            parsed_data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return {"error": "Invalid JSON format in data"}

    # GeoJSON / 记录数组 → 数据点（字段映射路径）。带任一映射字段且元素是
    # dict（Feature 或裸记录）即走映射；已是 {name,value} 形态时不带映射
    # 字段，维持既有校验路径。
    selection_field = ""
    if isinstance(parsed_data, dict) and isinstance(parsed_data.get("features"), list):
        parsed_data = parsed_data["features"]
    if (
        isinstance(parsed_data, list)
        and parsed_data
        and isinstance(parsed_data[0], dict)
        and ((x_field or y_field or name_field) or _is_geojson_feature(parsed_data[0]))
    ):
        parsed_data, map_error = _map_features_to_points(
            parsed_data, effective_type, x_field, y_field, name_field,
        )
        if map_error:
            return {"error": f"GeoJSON 字段映射失败: {map_error}"}
        # Runtime V4（§15）：selectionField 自动生成 —— 类别字段是确定性
        # 推导（bar/line/pie 的类目 = name_field ?? x_field），不是猜测；
        # 无映射字段的 [{name,value}] 路径无法可靠推导 → 如实省略。
        if effective_type != "scatter":
            selection_field = (name_field or x_field or "").strip()

    # Validate structure
    if not isinstance(parsed_data, list):
        return {"error": "data must be a JSON array"}

    if len(parsed_data) == 0:
        return {"error": "data array cannot be empty"}

    # DoS protection: limit data points
    if len(parsed_data) > MAX_DATA_POINTS:
        return {"error": f"Too many data points (max {MAX_DATA_POINTS})"}

    # Validate each data point
    for i, point in enumerate(parsed_data):
        is_valid, error_msg = _validate_data_point(point, effective_type)
        if not is_valid:
            return {"error": f"Invalid data point at index {i}: {error_msg}"}

    chart = {
        "type": effective_type,
        "title": safe_title,
        "data": parsed_data,
    }
    if safe_x_label:
        chart["x_label"] = safe_x_label
    if safe_y_label:
        chart["y_label"] = safe_y_label
    if selection_field:
        # Runtime V4：chart→map 类别过滤协议的映射字段（消费面在
        # chart_panel.options.selectionField）；scatter 无类别语义不带。
        chart["selectionField"] = selection_field

    return {"chart": chart}


async def generate_chart_tool(
    chart_type: str = "", title: str = "", data: Any = "",
    x_label: str = "", y_label: str = "",
    x_field: str = "", y_field: str = "", name_field: str = "",
    type: str = "",
    session_id: str = "", attach_to_map: bool = False,
    position: str = "", variant: str = "default",
    layer_id: str = "",
) -> dict:
    """generate_chart 的注册面（async 包装）。

    attach_to_map=true（需 session_id）：图表同时作为地图浮动 chart_panel
    组件写入 MapSpec（组件突变——不触发任何数据重查/图层重排）。与
    webgis_component_update(create=true) 走同一入口与校验。
    同步核心 generate_chart 保持既有 chat 图表契约与全部测试兼容。

    Runtime V4（§15）：GeoJSON 字段映射路径自动携带 selectionField（类目
    字段）；layer_id 显式指定或从 data ref ↔ MapSpec source 自动解析 ——
    chart→map 类别过滤从此无需 agent 手工补字段。
    """
    out = generate_chart(
        chart_type=chart_type, title=title, data=data,
        x_label=x_label, y_label=y_label,
        x_field=x_field, y_field=y_field, name_field=name_field,
        type=type,
    )
    if "error" in out:
        return out
    if attach_to_map and session_id:
        bound_layer_id = layer_id.strip()
        if not bound_layer_id:
            bound_layer_id = await _resolve_layer_for_data_ref(session_id, data)
        out.update(await _attach_chart_panel(
            session_id, out["chart"], position, variant, layer_id=bound_layer_id,
        ))
    elif attach_to_map and not session_id:
        out["map_chart_panel"] = {
            "attached": False,
            "layer_count_unchanged": True,
            "error": "attach_to_map 需要 session_id",
            "hint": "传 session_id=当前会话 后重试，或用 webgis_component_update(create=true)。",
        }
    return out


async def _resolve_layer_for_data_ref(session_id: str, data: Any) -> str:
    """data 是 ref: 引用时，解析 MapSpec 中以该 ref 为源的图层族 id。

    确定性推导（不是猜测）：仅当恰好一个图层族的 source 携带该 ref 时
    返回其 id；零个或多个命中 → 空串（agent 可用 layer_id 显式指定）。
    """
    ref = data.strip() if isinstance(data, str) else ""
    if not ref.startswith("ref:"):
        return ""
    try:
        from app.services.mapspec_store import mapspec_store

        spec = await mapspec_store.get_mapspec(session_id) or {}
    except Exception:  # noqa: BLE001 — 读失败 → 不绑定（诚实省略）
        return ""
    families: set = set()
    for src_id, src in (spec.get("sources") or {}).items():
        if not isinstance(src, dict):
            continue
        src_refs = {
            src.get(k) for k in ("ref", "ref_id", "result_ref")
            if isinstance(src.get(k), str)
        }
        if ref in src_refs:
            families.add(str(src_id))
    if len(families) != 1:
        return ""
    # source id 与图层族 id 同名（adapter/converter 惯例）；按图层验证一次
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and str(layer.get("source") or "") in families:
            return str(layer.get("id") or "").split("__")[0]
    return ""


async def _attach_chart_panel(
    session_id: str, chart: dict, position: str, variant: str,
    layer_id: str = "",
) -> dict:
    """把 chart 以 chart_panel 组件 upsert 进 MapSpec（组件突变，不动图层）。

    与 webgis_component_update(create=true) 同入口：小载荷 inline，大载荷
    （>32KB）存 session artifact ref（MapSpec 只持引用）。返回附加 evidence
    字段（合并进工具结果）。

    Runtime V4（§15）：chart.selectionField（字段映射路径自动推导）与
    layer_id（显式/ref 解析）随面板写入 options —— chart→map 类别过滤
    协议（filter_field + layer_id）零手工配置即闭环；二者缺席则如实省略。
    """
    import json as _json

    from app.lib.json_size import estimate_json_bytes
    from app.services.mapspec_store import mapspec_store

    options: dict = {}
    inline_bytes = estimate_json_bytes(chart)
    if inline_bytes > 32 * 1024:
        from app.services.session_data import session_data_manager
        ref_id = await session_data_manager.store(session_id, {"chart": chart}, prefix="chart")
        options["chartRef"] = ref_id
    else:
        options["chart"] = chart
    # selectionField 协议透传（generate_chart 推导；已在 chart dict 里）
    selection_field = str(chart.get("selectionField") or "").strip()
    if selection_field:
        options["selectionField"] = selection_field
    if layer_id:
        options["layerId"] = layer_id

    res = await mapspec_store.patch_component(
        session_id,
        component_id="chart-panel",
        component_type="chart_panel",
        options=options,
        position=position or None,
        variant=variant or None,
        upsert=True,
    )
    evidence = {
        "map_chart_panel": {
            "attached": bool(res.get("success")),
            "inline": "chart" in options,
            "bytes": inline_bytes,
            "layer_count_unchanged": True,
        },
    }
    if options.get("chartRef"):
        evidence["map_chart_panel"]["chart_ref"] = options["chartRef"]
    if options.get("selectionField"):
        evidence["map_chart_panel"]["selection_field"] = options["selectionField"]
    if options.get("layerId"):
        evidence["map_chart_panel"]["layer_id"] = options["layerId"]
    if res.get("success"):
        evidence["map_chart_panel"]["mutation_revision"] = res.get("mutation_revision")
        return evidence
    evidence["map_chart_panel"]["error"] = res.get("message") or _json.dumps(
        {k: res.get(k) for k in ("message", "correction_hint") if res.get(k)},
        ensure_ascii=False,
    )
    # 附加失败不使图表生成本身失败（chat 侧图表仍返回）；面板错误显式可见
    evidence["map_chart_panel"]["hint"] = (
        "地图面板附加失败——可用 webgis_component_update(create=true, "
        "component_type=chart_panel) 重试。"
    )
    return evidence


def register_chart_tools(registry: ToolRegistry):
    """注册图表工具"""
    registry.register(
        tier=2, domains=["report"], name="generate_chart",
        description="【核心可视化工具】生成统计图表。所有数值统计结果【必须】通过此工具展示。data 可传 JSON 数组，也可直接传 GeoJSON/ref 引用并配合 x_field/y_field 取字段。attach_to_map=true（+session_id）可同时把图表作为地图浮动面板显示（组件突变，不重查数据）。**严禁**在回复中使用任何图片 Markdown (如 `![已通过图表工具渲染](...)`) 作为占位符或展示标记，这会导致前端由于无法找到图片而报错。只需调用工具并直接进行文字总结即可。",
        func=generate_chart_tool,
        param_descriptions={
            "chart_type": '图表类型: "bar"(柱状图), "line"(折线图), "pie"(饼图), "scatter"(散点图)（别名 type）',
            "title": "图表标题",
            "data": '数据，三种形态：(a) JSON数组字符串——柱状/折线/饼图 [{"name":"类别","value":数值}]，散点图 [{"name":"标签","x":数值,"y":数值}]；(b) GeoJSON FeatureCollection、要素/记录数组或 ref:xxx 引用（空间分析结果直接传入），此时必须用 x_field/y_field 指定取数字段；(c) 记录数组 [{"ct_name":"武侯区","count":88},...] 配合 x_field="ct_name" y_field="count"',
            "x_label": "X轴标签（可选）",
            "y_label": "Y轴标签（可选）",
            "x_field": "GeoJSON 输入时的类目/名称字段（bar/line/pie 的类目，scatter 的 x 值字段）",
            "y_field": "GeoJSON 输入时的数值字段（bar/line/pie 的取值，scatter 的 y 值字段）",
            "name_field": "GeoJSON 输入时的名称字段（可选，缺省用 x_field）",
            "session_id": "当前会话 ID（attach_to_map 时必填）",
            "attach_to_map": "true 时图表同时作为地图浮动 chart_panel 组件写入 MapSpec（『在地图上显示统计图』场景；组件突变，不重查数据）；GeoJSON 字段映射路径会自动携带 selectionField/layerId —— 图表类别点击即过滤地图图层",
            "position": "地图面板位置（可选）：top-left/top-right/bottom-left/bottom-right 等槽位",
            "variant": "面板样式 variant（可选）：default/compact/transparent/report",
            "layer_id": "图表绑定的地图图层 id（可选；chart→map 类别过滤的锚点）。缺省时若 data 是 ref: 引用且恰有一层以它为源则自动解析",
        },
    )
