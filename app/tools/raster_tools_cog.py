"""Raster COG 转换工具（Wave 6，audit 05 §7.4）。

``write_cog`` / ``validate_cog``（app/lib/geo_raster/cog.py）此前零生产
调用方 —— 本模块把它们作为 tier-2 工具暴露：上传/产出的普通（可能条带
状）GeoTIFF 一转即得 tiled + 内部金字塔 + 压缩的 COG，远端 range read 与
瓦片流受益。职责限定：validate_data_path → ensure_cog（已是 COG 则原样
返回，绝不重复转换）→ 返回有界校验报告。算法语义全在 lib 层。

如实披露：app/services/data_parser.parse_raster / app/services/upload.py
的自动转换不在本波所有权内（Wave 6 报告已注明）——今天本工具与显式调
用 ``ensure_cog`` 的服务是仅有的接线方，没有任何静默转换。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.tools._utils import std_error_response
from app.tools.registry import ToolRegistry, tool
from app.utils.path import validate_data_path

logger = logging.getLogger(__name__)


def register_raster_cog_tools(registry: ToolRegistry):
    """注册栅格 COG 转换工具。"""

    @tool(registry, name="convert_raster_to_cog",
          tier=2, domains=["raster"],
          capabilities=["raster_cog_conversion"],
          description=(
              "栅格转 Cloud-Optimized GeoTIFF (COG)：tiled + 内置金字塔 + 压缩，远程/瓦片读取提速。"
              "\n何时用：(1) 上传的大 TIFF 要发布为可流式瓦片图层；(2) 栅格要供远端 range-read 访问；"
              "(3) 分析产物缺金字塔、前端渲染慢。"
              "\n何时不用：(1) 数据已是 COG（本工具会如实报告 already_cog=true 并原样返回）；"
              "(2) 矢量数据；(3) 只改样式 — 用样式接口，不要重转数据。"
              "\n关键约束：raster_path 必须位于 ./data 下；输出写到 out_dir（默认 ./data/cog）；"
              "原文件绝不原地修改。"
          ),
          param_descriptions={
              "raster_path": "输入栅格路径（./data 下的 GeoTIFF）",
              "out_dir": "输出目录（默认 ./data/cog，必须也在 ./data 下）",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="slow",
          memory_class="medium",
          scale_class="large",
          output_semantic_type="object",
          result_size_policy="bounded",
          tags=("cog", "栅格转换", "云优化", "金字塔", "geotiff", "瓦片"),
          failure_modes=("file_not_found", "invalid_format", "conversion_failed"),
          )
    async def convert_raster_to_cog(
        raster_path: str, out_dir: str = "data/cog", session_id: str = "",
    ) -> dict:
        try:
            src = validate_data_path(raster_path)
            dst = validate_data_path(out_dir)
        except ValueError as e:
            return std_error_response(
                str(e), code="INVALID_PATH",
                correction_hint="raster_path / out_dir 必须位于 ./data 目录之下。",
            )

        from app.lib.geo_raster.cog import ensure_cog, validate_cog

        try:
            out = await asyncio.to_thread(ensure_cog, src, dst)
        except ValueError as e:  # CogWriteError ⊂ ValueError（类型化拒绝）
            return std_error_response(
                str(e), code="COG_CONVERT_FAILED",
                error_type=type(e).__name__,
                correction_hint=(
                    "确认输入是可读栅格且磁盘可写；转换失败不留半截文件，"
                    "修复后重试。"
                ),
            )
        except (RuntimeError, OSError) as e:
            return std_error_response(str(e), code="COG_CONVERT_FAILED")

        already = str(out) == str(src)
        report = await asyncio.to_thread(validate_cog, str(out))
        result = {
            "success": True,
            "output_path": str(out),
            "already_cog": already,
            "converted": not already,
            "validation": {
                "ok": bool(report.get("ok")),
                "driver": report.get("driver"),
                "overviews": report.get("overviews"),
                "block_shape": report.get("block_shape"),
                "compressor": report.get("compressor"),
                "size": report.get("size"),
                "bands": report.get("bands"),
            },
        }
        # V6（ADR-0118）：COG → durable DataObject（内容寻址身份 + grid
        # identity + 有界 chunk checksums）。session 上下文缺席时诚实跳过
        # （owner scope 不可虚构）；发布失败不阻断转换结果。
        if session_id:
            from app.services.lakehouse.raster_object import publish_cog_data_object

            publication = await asyncio.to_thread(
                publish_cog_data_object,
                out,
                session_id=session_id,
                source_refs=[f"ref:raster-source/{Path(raster_path).name}"],
                producer={"capability": "raster_cog_conversion", "tool": "convert_raster_to_cog"},
            )
            if publication.get("published"):
                result["data_object"] = {
                    "published": True,
                    "data_object_id": publication["data_object_id"],
                    "content_sha256": publication["content_sha256"],
                    "deduped": publication.get("deduped", False),
                    "chunk_checksums": publication.get("chunk_checksums", False),
                }
            else:
                result["data_object"] = {
                    "published": False,
                    "reason": publication.get("reason", "failed"),
                }
        else:
            result["data_object"] = {
                "published": False,
                "reason": "owner_missing",
                "correction_hint": "durable publication requires a session context",
            }
        return result
