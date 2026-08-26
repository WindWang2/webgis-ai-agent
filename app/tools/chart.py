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
    """生成图表配置数据，供前端渲染。

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

    return {"chart": chart}


def register_chart_tools(registry: ToolRegistry):
    """注册图表工具"""
    registry.register(
        tier=2, domains=["report"], name="generate_chart",
        description="【核心可视化工具】生成统计图表。所有数值统计结果【必须】通过此工具展示。data 可传 JSON 数组，也可直接传 GeoJSON/ref 引用并配合 x_field/y_field 取字段。**严禁**在回复中使用任何图片 Markdown (如 `![已通过图表工具渲染](...)`) 作为占位符或展示标记，这会导致前端由于无法找到图片而报错。只需调用工具并直接进行文字总结即可。",
        func=generate_chart,
        param_descriptions={
            "chart_type": '图表类型: "bar"(柱状图), "line"(折线图), "pie"(饼图), "scatter"(散点图)（别名 type）',
            "title": "图表标题",
            "data": '数据，三种形态：(a) JSON数组字符串——柱状/折线/饼图 [{"name":"类别","value":数值}]，散点图 [{"name":"标签","x":数值,"y":数值}]；(b) GeoJSON FeatureCollection、要素/记录数组或 ref:xxx 引用（空间分析结果直接传入），此时必须用 x_field/y_field 指定取数字段；(c) 记录数组 [{"ct_name":"武侯区","count":88},...] 配合 x_field="ct_name" y_field="count"',
            "x_label": "X轴标签（可选）",
            "y_label": "Y轴标签（可选）",
            "x_field": "GeoJSON 输入时的类目/名称字段（bar/line/pie 的类目，scatter 的 x 值字段）",
            "y_field": "GeoJSON 输入时的数值字段（bar/line/pie 的取值，scatter 的 y 值字段）",
            "name_field": "GeoJSON 输入时的名称字段（可选，缺省用 x_field）",
        },
    )
