"""
变化检测工具包 - 自然资源遥感监测核心能力
支持双时相植被指数变化检测与分类分析
"""
import logging
from typing import Optional
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.spatial_tasks import run_change_detection
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)


class ChangeDetectionArgs(BaseModel):
    bbox: str = Field(..., description="边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]")
    t1_from: str = Field(..., description="T1 时期起始日期 YYYY-MM-DD")
    t1_to: str = Field(..., description="T1 时期结束日期 YYYY-MM-DD")
    t2_from: str = Field(..., description="T2 时期起始日期 YYYY-MM-DD")
    t2_to: str = Field(..., description="T2 时期结束日期 YYYY-MM-DD")
    index_type: str = Field("ndvi", description="植被指数类型: ndvi, ndwi, nbr, evi")
    change_threshold: float = Field(0.1, description="变化检测阈值，默认 0.1")
    session_id: Optional[str] = Field(None, description="会话 ID")


def register_change_detection_tools(registry: ToolRegistry):
    """注册变化检测相关工具"""

    @tool(registry, name="detect_vegetation_change",
          tier=2, domains=["raster"],
          description=(
              "执行双时相植被变化检测分析。自动获取两个时期的 Sentinel-2 卫星影像，"
              "计算指定植被指数的差异，并将变化区域分类为：显著改善、轻微改善、无变化、"
              "轻微退化、显著退化。结果包含变化统计、分类图和预览。适用于森林砍伐监测、"
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
          })
    def detect_vegetation_change(
        bbox: str,
        t1_from: str,
        t1_to: str,
        t2_from: str,
        t2_to: str,
        index_type: str = "ndvi",
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

        # 触发 Celery 异步任务
        task = run_change_detection.delay(
            bbox=parts,
            t1_from=t1_from,
            t1_to=t1_to,
            t2_from=t2_from,
            t2_to=t2_to,
            index_type=index_type.lower(),
            change_threshold=change_threshold,
            session_id=session_id,
        )

        return {
            "status": "change_detection_task_started",
            "task_id": task.id,
            "message": (
                f"{index_type.upper()} 双时相变化检测任务已启动。"
                f"T1: {t1_from}~{t1_to} vs T2: {t2_from}~{t2_to}。"
                "后台正在自动获取 Sentinel-2 影像并计算差异，"
                "完成后结果将自动推送到地图并进入资产库。"
            ),
            "parameters": {
                "bbox": parts,
                "t1_period": f"{t1_from} to {t1_to}",
                "t2_period": f"{t2_from} to {t2_to}",
                "index_type": index_type.lower(),
                "change_threshold": change_threshold,
            },
        }
