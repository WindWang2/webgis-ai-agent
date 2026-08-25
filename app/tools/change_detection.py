"""
变化检测工具包 - 自然资源遥感监测核心能力
支持双时相植被指数变化检测与分类分析
"""
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.spatial_tasks import run_change_detection
from app.services.jobs.submit import submit_durable_job
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)


class ChangeDetectionArgs(BaseModel):
    bbox: str = Field(..., description="边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]")
    t1_from: str = Field(..., description="T1 时期起始日期 YYYY-MM-DD")
    t1_to: str = Field(..., description="T1 时期结束日期 YYYY-MM-DD")
    t2_from: str = Field(..., description="T2 时期起始日期 YYYY-MM-DD")
    t2_to: str = Field(..., description="T2 时期结束日期 YYYY-MM-DD")
    index_type: Literal["ndvi", "ndwi", "nbr", "evi"] = Field(
        "ndvi", description="植被指数类型: ndvi, ndwi, nbr, evi")
    change_threshold: float = Field(0.1, description="变化检测阈值，默认 0.1")
    session_id: Optional[str] = Field(None, description="会话 ID")


def register_change_detection_tools(registry: ToolRegistry):
    """注册变化检测相关工具"""

    @tool(registry, name="detect_vegetation_change",
          tier=2, domains=["raster"],
          description=(
              "执行双时相植被变化检测分析。自动获取两个时期的 Sentinel-2 卫星影像，"
              "在两期影像足迹的公共栅格上计算指定植被指数的像元级差异，"
              "并将每个像元分类为：显著改善、轻微改善、无变化、"
              "轻微退化、显著退化。结果包含变化统计、像元级分类统计（各类像元数与占比）"
              "和差异预览图。适用于森林砍伐监测、"
              "植被恢复评估、湿地变化追踪、火灾后恢复监测等场景。"
          ),
          param_descriptions={
              "bbox": "边界框 [west, south, east, north]",
              "t1_from": "第一期起始日期 (YYYY-MM-DD)",
              "t1_to": "第一期结束日期 (YYYY-MM-DD)",
              "t2_from": "第二期起始日期 (YYYY-MM-DD)",
              "t2_to": "第二期结束日期 (YYYY-MM-DD)",
              "index_type": "指数类型: ndvi(植被), ndwi(水体), nbr(燃烧), evi(增强植被)",
              "change_threshold": "变化阈值，决定轻微/显著变化的边界",
          },
          # #996: 工具体经 submit_durable_job 内部投递 Celery
          # （run_change_detection.apply_async）——重工具显式标 heavy；
          # 提交路径本身只做 DB 写 + broker 入队，60s 预算绰绰有余。
          cost="heavy", timeout=60.0)
    def detect_vegetation_change(
        bbox: str,
        t1_from: str,
        t1_to: str,
        t2_from: str,
        t2_to: str,
        # #995: schema 层枚举（合法值 = 工具体 valid_indices；体内运行时
        # 校验保留兜底）。签名注解驱动 registry._generate_model 的 schema。
        index_type: Literal["ndvi", "ndwi", "nbr", "evi"] = "ndvi",
        change_threshold: float = 0.1,
        session_id: Optional[str] = None,
    ) -> dict:
        try:
            parts = parse_bbox(bbox)
        except ValueError as e:
            return {"error": str(e)}

        valid_indices = {"ndvi", "ndwi", "nbr", "evi"}
        if index_type.lower() not in valid_indices:
            return {
                "error": f"不支持的指数类型 '{index_type}'，可用: {', '.join(valid_indices)}"
            }

        # ADR-0052：走 durable job 投递 -- 任务注册进任务中心（进度/取消/
        # 结果可查，celery_task_id 回填后 /tasks/status/glm-5.3_common 也可查），统计结果由
        # worker 落为 GeoJSON 资产。此前 .delay 火忘投递：任务不注册资产、状态
        # 404，返回消息却承诺「自动推送到地图并进入资产库」。
        return submit_durable_job(
            celery_task=run_change_detection,
            task_type="change_detection",
            display_name=f"{index_type.upper()} 双时相变化检测",
            params={
                "bbox": parts,
                "t1_from": t1_from,
                "t1_to": t1_to,
                "t2_from": t2_from,
                "t2_to": t2_to,
                "index_type": index_type.lower(),
                "change_threshold": change_threshold,
            },
            task_args=(
                parts, t1_from, t1_to, t2_from, t2_to,
                index_type.lower(), change_threshold, session_id,
            ),
            session_id=session_id,
        )
