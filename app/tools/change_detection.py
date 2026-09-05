"""
变化检测工具包 - 自然资源遥感监测核心能力
支持双时相植被指数变化检测与分类分析

VNext（ADR-0099）：新增内存数组面的 CVA（变化向量分析，Malila 1980）
与比值/对数比值变化（SAR 后向散射设计）工具——薄包装 + 科学证据块，
实现位于 app/lib/geo_analysis/raster_change.py。反目标：CVA 只输出
幅度/方向，不做土地覆盖语义归类。
"""
import logging
from typing import Any, Dict, List, Literal, Optional

import numpy as np
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

    # ── VNext（ADR-0099）：CVA + 比值变化（内存数组面，薄包装）────────

    def _to_2d(name: str, data: List[List[float]]) -> np.ndarray:
        arr = np.asarray(data, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"{name} 必须是 2D 数组，got ndim={arr.ndim}")
        return arr

    def _attach_evidence(
        payload: dict,
        algorithm_id: str,
        *,
        tool: str,
        parameters_applied: dict,
        diagnostics: Optional[List[Dict[str, Any]]] = None,
        warnings: Optional[List[str]] = None,
        dates: Optional[Dict[str, Optional[str]]] = None,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.scientific_evidence import Diagnostic, build_evidence

        descriptor = get_algorithm_registry().get(algorithm_id)
        if descriptor is None:
            logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
            return payload
        if dates:
            # 时间元数据显式进参数面（缺省 None 也要可见——未知≠不存在）。
            parameters_applied = {
                **parameters_applied,
                **{k: (v or "unknown") for k, v in dates.items()},
            }
        payload["scientific_evidence"] = build_evidence(
            descriptor,
            tool=tool,
            parameters_applied=parameters_applied,
            input_facts={"feature_count": payload.get("stats", {}).get("total_pixels")},
            warnings=warnings,
            diagnostics=[
                d if isinstance(d, Diagnostic) else Diagnostic(**d)
                for d in (diagnostics or [])
            ],
        )
        return payload

    @tool(registry, name="detect_change_cva",
          description=(
              "变化向量分析（CVA, Malila 1980）：对两期同角色波段集合计算逐像元变化幅度"
              "（全部角色欧氏范数）与方向角（固定角色序前两分量 atan2，弧度）。"
              "只回答『变了多大、朝哪个方向变』——不做土地覆盖语义归类"
              "（不输出『植被→建筑』类命名）。"
              "\n何时用：多波段双时相的强度/方向概览；SAR 双极化双时相；"
              "\n关键约束：t1/t2 的角色键集合必须一致（缺角色拒绝，不按位置猜测）；"
              "同一像元任一角色任一期无效（NaN）→ 该像元输出 NaN。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "t1_bands": "T1 波段 {role: 2D 数组}（角色显式命名，如 {\"red\": [[..]], \"nir\": [[..]]}）",
              "t2_bands": "T2 波段（与 T1 完全相同的角色集合与形状）",
              "t1_date": "T1 获取日期 YYYY-MM-DD（可选，进证据块）",
              "t2_date": "T2 获取日期 YYYY-MM-DD（可选，进证据块）",
          })
    async def detect_change_cva(
        t1_bands: Dict[str, List[List[float]]],
        t2_bands: Dict[str, List[List[float]]],
        t1_date: Optional[str] = None,
        t2_date: Optional[str] = None,
    ) -> dict:
        from app.lib.geo_analysis.raster_change import change_vector_analysis

        from app.tools.remote_sensing import _bands_to_arrays
        _bands_to_arrays(t1_bands or {})   # 资源包络守卫（评审 M3）
        _bands_to_arrays(t2_bands or {})
        if set(t1_bands or {}) != set(t2_bands or {}):
            from app.lib.gis.scientific_errors import UnsupportedBandSemantics

            raise UnsupportedBandSemantics(
                f"CVA 两景角色集不一致：t1={sorted(t1_bands or {})} vs "
                f"t2={sorted(t2_bands or {})}；变化向量要求同一组语义角色",
                correction_hint="两景提供完全相同的语义角色集合",
            )
        t1_arrays = {r: _to_2d(f"t1_bands[{r}]", d) for r, d in t1_bands.items()}
        t2_arrays = {r: _to_2d(f"t2_bands[{r}]", d) for r, d in t2_bands.items()}

        res = change_vector_analysis(
            t1_arrays, t2_arrays, t1_date=t1_date, t2_date=t2_date)
        finite = np.isfinite(res["magnitude"])
        payload = {
            "success": True,
            "roles_used": res["roles_used"],
            "role_order_contract": res["meta"]["role_order_contract"],
            "stats": {
                "magnitude_mean": float(np.nanmean(res["magnitude"])) if finite.any() else None,
                "magnitude_max": float(np.nanmax(res["magnitude"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["magnitude"].size),
            },
            "magnitude": res["magnitude"].round(6).tolist(),
            "angle_rad": res["angle"].round(6).tolist(),
            "meta": res["meta"],
        }
        return _attach_evidence(
            payload, "remote.cva", tool="detect_change_cva",
            parameters_applied={
                "roles": ",".join(res["roles_used"]),
                "angle_unit": "radians",
            },
            diagnostics=[
                {"name": "magnitude_mean",
                 "value": payload["stats"]["magnitude_mean"]},
                {"name": "valid_pixels",
                 "value": float(payload["stats"]["valid_pixels"])},
            ],
            warnings=[res["meta"]["disclosure"]],
            dates={"t1_date": t1_date, "t2_date": t2_date},
        )

    @tool(registry, name="detect_ratio_change",
          description=(
              "双时相比值变化：ratio（a/b，零分母→NaN）或 log_ratio"
              "（log(a)−log(b)，对数域对称——增强=衰减镜像）。"
              "为 SAR 后向散射/强度双期比较设计（同量纲输入）。"
              "\n何时用：SAR 双期后向散射变化（log_ratio 惯用）；强度量双期对比；"
              "\n关键约束：a/b 同形状；log_ratio 输入须为正（线性强度或 dB）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "a": "T1 强度/后向散射 2D 数组",
              "b": "T2 强度/后向散射 2D 数组（与 a 同形状）",
              "method": "ratio（默认，a/b） / log_ratio（log(a)−log(b)）",
              "t1_date": "T1 获取日期 YYYY-MM-DD（可选，进证据块）",
              "t2_date": "T2 获取日期 YYYY-MM-DD（可选，进证据块）",
          })
    async def detect_ratio_change(
        a: List[List[float]],
        b: List[List[float]],
        method: str = "ratio",
        t1_date: Optional[str] = None,
        t2_date: Optional[str] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.raster_change import ratio_change

        params = apply_contract("ratio_change_analysis", {"method": method})
        a_arr = _to_2d("a", a)
        b_arr = _to_2d("b", b)
        # 评审 M3：内联数组资源包络（与 remote_sensing 同一哨兵值）。
        from app.tools.remote_sensing import _TOOL_ARRAY_MAX_VALUES
        from app.lib.gis.scientific_errors import ResourceScaleMismatch
        for _name, _arr in (("a", a_arr), ("b", b_arr)):
            if _arr.size > _TOOL_ARRAY_MAX_VALUES:
                raise ResourceScaleMismatch(
                    f"detect_ratio_change {_name} 数组超限：{_arr.size} 值",
                    estimated=f"{_arr.size * 8 / 1e6:.1f} MB float64",
                    limit=f"≤{_TOOL_ARRAY_MAX_VALUES} values",
                    correction_hint="分块处理或走栅格文件路径",
                )
        res = ratio_change(
            a_arr, b_arr, method=params["method"],
            t1_date=t1_date, t2_date=t2_date)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "method": res["method"],
            "formula": res["meta"]["formula"],
            "stats": {
                "min": float(np.nanmin(res["array"])) if finite.any() else None,
                "max": float(np.nanmax(res["array"])) if finite.any() else None,
                "mean": float(np.nanmean(res["array"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["array"].size),
            },
            "array": res["array"].round(6).tolist(),
            "meta": res["meta"],
        }
        algorithm_id = (
            "sar.log_ratio_change" if res["method"] == "log_ratio"
            else "remote.ratio_change")
        return _attach_evidence(
            payload, algorithm_id, tool="detect_ratio_change",
            parameters_applied={"method": res["method"]},
            diagnostics=[
                {"name": "mean", "value": payload["stats"]["mean"]},
                {"name": "valid_pixels", "value": float(payload["stats"]["valid_pixels"])},
            ],
            warnings=[res["meta"]["disclosure"]],
            dates={"t1_date": t1_date, "t2_date": t2_date},
        )
