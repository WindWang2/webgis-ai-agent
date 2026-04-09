"""图表生成 FC 工具"""
import json
import logging
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "pie", "scatter"}


def generate_chart(chart_type: str, title: str, data: str, x_label: str = "", y_label: str = "") -> dict:
    """生成图表配置数据，供前端渲染"""
    if chart_type not in VALID_CHART_TYPES:
        return {"error": f"Invalid chart_type '{chart_type}'. Must be one of: {', '.join(sorted(VALID_CHART_TYPES))}"}

    try:
        parsed_data = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return {"error": "Invalid data: must be a valid JSON array string"}

    if not isinstance(parsed_data, list) or len(parsed_data) == 0:
        return {"error": "data must be a non-empty JSON array"}

    chart = {
        "type": chart_type,
        "title": title,
        "data": parsed_data,
    }
    if x_label:
        chart["x_label"] = x_label
    if y_label:
        chart["y_label"] = y_label

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
