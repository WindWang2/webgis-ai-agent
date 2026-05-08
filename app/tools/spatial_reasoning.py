"""Spatial reasoning rule library and LLM tool."""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

SPATIAL_RULES = {
    "traffic": {
        "category": "交通影响规则",
        "rules": [
            "暴雨天气道路通行能力下降 20-40%",
            "早高峰时段道路饱和度增加 30-50%",
            "地铁换乘站 500m 范围内步行可达性极高",
            "单车道发生事故后通行能力下降 50-70%",
            "限行政策对区域交通流量产生显著影响",
        ],
    },
    "commercial": {
        "category": "商业选址规则",
        "rules": [
            "学校周边 200m 范围内禁止开设娱乐场所",
            "餐饮工作日午餐客流约等于办公人口 x 0.3",
            "社区店辐射半径通常为 500-800m",
            "便利店竞争饱和度超过 3 家后盈利能力下降",
            "购物中心 1km 范围内同业态小店客流下降 20-40%",
        ],
    },
    "urban_planning": {
        "category": "城市规划规则",
        "rules": [
            "小学服务半径 500m",
            "初中服务半径 1000m",
            "社区医院服务半径 1.5-3km",
            "15分钟生活圈覆盖居民日常需求",
            "公园绿地 500m 覆盖率目标 >90%",
        ],
    },
    "real_estate": {
        "category": "房地产规则",
        "rules": [
            "地铁站 500m 内房价溢价 +15-25%",
            "地铁换乘站房价溢价 +20-30%",
            "公园 300m 内房价溢价 +5-10%",
            "高压线/垃圾站 200m 内房价折价 -10-20%",
            "学区房溢价 +20-50%",
        ],
    },
    "environment": {
        "category": "环境灾害规则",
        "rules": [
            "暴雨内涝风险区积水深度 30-100cm",
            "台风中心 50km 范围内停工停课概率 >80%",
            "PM2.5 >150 建议取消户外活动",
            "地震烈度 6 度以上砖混结构受损风险显著增加",
        ],
    },
}


class SpatialReasoningArgs(BaseModel):
    query: str = Field(..., description="空间推理查询")
    context: dict = Field(default_factory=dict, description="额外上下文数据")
    reasoning_depth: Literal["brief", "standard", "deep"] = Field(
        default="standard", description="推理深度: brief/standard/deep"
    )


class ReasoningStep(BaseModel):
    step: int = Field(..., description="步骤序号")
    fact: str = Field(..., description="事实或规则引用")
    source: str = Field(..., description="规则来源类别")


class SpatialReasoningResult(BaseModel):
    type: str = Field(default="spatial_reasoning", description="结果类型")
    conclusion: str = Field(..., description="推理结论")
    reasoning_chain: list[ReasoningStep] = Field(..., description="推理链")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")
    uncertainty: str = Field(..., description="不确定性说明")
    recommendations: list[str] = Field(default_factory=list, description="建议列表")


def _build_system_prompt() -> str:
    """构建包含规则库的系统提示词。"""
    lines = [
        "你是一名空间规则推理专家。请基于以下规则库对用户的空间问题进行可解释的逻辑推理。",
        "",
        "=== 空间规则库 ===",
        "",
    ]
    for key, value in SPATIAL_RULES.items():
        lines.append(f"【{value['category']}】({key})")
        for rule in value["rules"]:
            lines.append(f"  - {rule}")
        lines.append("")
    lines.extend(
        [
            "=== 输出要求 ===",
            "- 推理过程必须引用具体规则",
            "- 置信度应基于数据完整性和规则适用性评估 (0.0-1.0)",
            "- 对于不确定的部分，明确说明不确定性来源",
            "- 给出可操作的建议",
            "- 输出必须是有效的 JSON 格式",
            "",
            "=== 置信度标准 ===",
            "- 0.9-1.0: 规则直接适用，数据充分",
            "- 0.7-0.89: 规则基本适用，少量假设",
            "- 0.5-0.69: 规则部分适用，存在不确定性",
            "- 0.3-0.49: 规则适用性较弱，需谨慎",
            "- 0.0-0.29: 缺乏适用规则或数据，高度不确定",
        ]
    )
    return "\n".join(lines)


def _build_user_prompt(query: str, context: dict, depth: str) -> str:
    """构建用户提示词。"""
    depth_instructions = {
        "brief": "简要推理：给出核心结论和关键依据，控制在3步以内。",
        "standard": "标准推理：给出完整推理链，引用具体规则，评估置信度。",
        "deep": "深度推理：多角度分析，考虑规则冲突与边界条件，给出详细的不确定性分析和多情景建议。",
    }

    lines = [
        f"用户查询: {query}",
        f"推理深度: {depth} - {depth_instructions.get(depth, depth_instructions['standard'])}",
        "",
    ]
    if context:
        lines.append("=== 上下文数据 ===")
        for k, v in context.items():
            lines.append(f"{k}: {v}")
        lines.append("")
    lines.extend(
        [
            "=== 输出格式示例 ===",
            "{",
            '  "type": "spatial_reasoning",',
            '  "conclusion": "推理结论文本",',
            '  "reasoning_chain": [',
            '    {"step": 1, "fact": "引用的事实", "source": "规则类别"}',
            "  ],",
            '  "confidence": 0.85,',
            '  "uncertainty": "不确定性说明",',
            '  "recommendations": ["建议1", "建议2"]',
            "}",
        ]
    )
    return "\n".join(lines)


async def _call_llm(system_prompt: str, user_prompt: str) -> dict:
    """LLM 调用占位符。生产环境集成真实 LLM 服务。"""
    logger.debug("_call_llm placeholder invoked")
    # Mock structured response for placeholder
    return {
        "type": "spatial_reasoning",
        "conclusion": "基于现有规则库，该位置适合商业选址，但需考虑竞争饱和度。",
        "reasoning_chain": [
            {"step": 1, "fact": "社区店辐射半径 500-800m", "source": "commercial"},
            {"step": 2, "fact": "便利店竞争饱和度超过 3 家后盈利能力下降", "source": "commercial"},
        ],
        "confidence": 0.75,
        "uncertainty": "缺乏具体人流量数据，竞争饱和度基于周边POI估算",
        "recommendations": ["进一步获取周边 exact 人流量数据", "分析工作日午餐客流与办公人口比例"],
    }


def register_spatial_reasoning(registry: ToolRegistry):
    """注册空间推理工具到 ToolRegistry。"""

    @tool(
        registry,
        name="spatial_reasoning",
        description="空间规则推演：基于地理/城市规划规则库，对空间现象进行可解释的逻辑推理。适用于趋势分析、选址对比、空间关联分析等场景。",
        args_model=SpatialReasoningArgs,
    )
    async def spatial_reasoning(
        query: str,
        context: dict = None,
        reasoning_depth: str = "standard",
    ) -> dict:
        if context is None:
            context = {}

        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(query, context, reasoning_depth)

        result = await _call_llm(system_prompt, user_prompt)
        return result
