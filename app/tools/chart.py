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


def generate_chart(chart_type: str, title: str, data: str, x_label: str = "", y_label: str = "") -> dict:
    """生成图表配置数据，供前端渲染"""
    # Validate chart_type
    if not isinstance(chart_type, str) or chart_type not in VALID_CHART_TYPES:
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
    else:
        # DoS protection: check payload size before parsing
        if len(data) > MAX_DATA_PAYLOAD_SIZE:
            return {"error": f"Data payload too large (max {MAX_DATA_PAYLOAD_SIZE // 1024}KB)"}
        try:
            parsed_data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return {"error": "Invalid JSON format in data"}

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
        is_valid, error_msg = _validate_data_point(point, chart_type)
        if not is_valid:
            return {"error": f"Invalid data point at index {i}: {error_msg}"}

    chart = {
        "type": chart_type,
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
        name="generate_chart",
        description="生成统计图表（柱状图/折线图/饼图/散点图）。先用查询工具获取数据，再调用此工具将结果可视化。",
        func=generate_chart,
        param_descriptions={
            "chart_type": '图表类型: "bar"(柱状图), "line"(折线图), "pie"(饼图), "scatter"(散点图)',
            "title": "图表标题",
            "data": 'JSON数组字符串。柱状/折线/饼图: [{"name":"类别","value":数值}]，散点图: [{"name":"标签","x":数值,"y":数值}]',
            "x_label": "X轴标签（可选）",
            "y_label": "Y轴标签（可选）",
        },
    )
