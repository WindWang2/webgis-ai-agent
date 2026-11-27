"""Moving-object trajectory tools (GOAL-20261127-02).

Exposes the trajectory domain as a first-class tool family over the SAME
service the REST route uses (one engine, two entry faces, identical
artifacts):

- ``trajectory_inspect``  — read-only source inspection (cacheable_read)
- ``trajectory_build``    — ingest→segments→dwells→region events; persists
                            the product + FCs as session refs (bounded
                            envelope for the LLM)
- ``trajectory_apply``    — one CAS MapSpec transaction (state_mutation)
- ``trajectory_query``    — paged drill-down over a stored product

Scientific honesty mirrors the domain contracts: interval-estimated
speeds, inferred event brackets, typed rejections (no fabricated motion),
and a scientific_evidence block per algorithm descriptor.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.moving_object.service import (
    TrajectoryProductConfig,
    TrajectoryServiceError,
    apply_trajectory_product_preview,
    inspect_moving_object_source,
    preview_trajectory_product,
    query_trajectory_product,
)

logger = logging.getLogger(__name__)


class TrajectoryInspectArgs(BaseModel):
    dataset: str = Field(..., min_length=1, max_length=160,
                         json_schema_extra={"ref_cursor": True},
                         description="会话内点表数据 ref（ref:geojson-*）")
    entity_id_field: str = Field("entity_id", max_length=64)
    time_field: str = Field("timestamp", max_length=64)
    assumed_timezone: str = Field("UTC", max_length=64,
                                  description="naive 时间戳的声明时区（IANA）")


class TrajectoryBuildArgs(BaseModel):
    dataset: str = Field(..., min_length=1, max_length=160,
                         json_schema_extra={"ref_cursor": True},
                         description="会话内点表数据 ref")
    entity_id_field: str = Field("entity_id", max_length=64)
    time_field: str = Field("timestamp", max_length=64)
    source_row_field: Optional[str] = Field(None, max_length=64,
                                            description="稳定源行身份字段（缺省派生 row:<input position>）")
    assumed_timezone: str = Field("UTC", max_length=64)
    duplicate_time_policy: str = Field("keep_all", description="keep_all|keep_first|keep_last")
    sort_policy: str = Field("time_asc", description="time_asc|keep_input")
    max_gap_s: float = Field(3600.0, gt=0, description="段断点：最大时间间隙（秒）")
    max_speed_kmh: float = Field(300.0, gt=0, description="段断点：最大观测间速度（km/h）")
    max_jump_m: float = Field(50000.0, gt=0, description="段断点：最大位置跳变（米）")
    min_points_per_segment: int = Field(2, ge=2, le=1000)
    detect_dwells: bool = True
    dwell_radius_m: float = Field(50.0, gt=0)
    dwell_min_dwell_s: float = Field(120.0, gt=0)
    dwell_min_points: int = Field(3, ge=2, le=10000)
    regions_ref: Optional[str] = Field(
        None, max_length=160, json_schema_extra={"ref_cursor": True},
        description="可选区域面 ref（ref:geojson-*，Polygon/MultiPolygon）——给定时同轮产出区域事件")
    region_id_field: str = Field("region_id", max_length=64)
    boundary_rule: str = Field("closed", description="closed|open（边界归属规则，结果内披露）")
    detect_cross: bool = True
    region_dwell_min_s: float = Field(60.0, gt=0)
    detect_region_dwell: bool = True
    time_start: Optional[str] = Field(None, description="分析窗起点（ISO-8601，含时区）")
    time_end: Optional[str] = Field(None, description="分析窗终点（ISO-8601，含时区）")
    entity_filter: Optional[List[str]] = Field(
        None, max_length=100, description="实体白名单（缺省=全部实体）")
    palette: Optional[List[str]] = Field(None, max_length=8)
    width_min_px: float = Field(1.5, gt=0)
    width_max_px: float = Field(7.0, ge=1.0, le=20.0)
    show_direction: bool = True


class TrajectoryApplyArgs(BaseModel):
    product_ref: str = Field(..., min_length=8, max_length=200,
                             description="trajectory_build 返回的产品 ref")
    expected_revision: int = Field(..., ge=0, description="MapSpec CAS 基线")
    mutation_id: Optional[str] = Field(None, max_length=96)


class TrajectoryQueryArgs(BaseModel):
    product_ref: str = Field(..., min_length=8, max_length=200)
    kind: str = Field("product", description="product|entity|dwell|event")
    entity_id: str = Field("", max_length=160, description="按实体钻取")
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)


def _config_from_args(a: TrajectoryBuildArgs) -> TrajectoryProductConfig:
    return TrajectoryProductConfig(
        dataset=a.dataset,
        entity_id_field=a.entity_id_field,
        time_field=a.time_field,
        source_row_field=a.source_row_field,
        assumed_timezone=a.assumed_timezone,
        duplicate_time_policy=a.duplicate_time_policy,
        sort_policy=a.sort_policy,
        max_gap_s=a.max_gap_s,
        max_speed_kmh=a.max_speed_kmh,
        max_jump_m=a.max_jump_m,
        min_points_per_segment=a.min_points_per_segment,
        detect_dwells=a.detect_dwells,
        dwell_radius_m=a.dwell_radius_m,
        dwell_min_dwell_s=a.dwell_min_dwell_s,
        dwell_min_points=a.dwell_min_points,
        regions_ref=a.regions_ref,
        region_id_field=a.region_id_field,
        boundary_rule=a.boundary_rule,
        detect_cross=a.detect_cross,
        region_dwell_min_s=a.region_dwell_min_s,
        detect_region_dwell=a.detect_region_dwell,
        time_start=a.time_start,
        time_end=a.time_end,
        entity_filter=a.entity_filter,
        palette=a.palette,
        width_min_px=a.width_min_px,
        width_max_px=a.width_max_px,
        show_direction=a.show_direction,
    )


def _error(message: str, code: str = "TRAJECTORY_INPUT_INVALID") -> dict:
    return {"type": "error", "error_code": code, "message": message}


def _attach_evidence(payload: dict, *, algorithm_id: str, tool: str,
                     parameters_applied: dict, n_points: Optional[int],
                     warnings: Optional[List[str]] = None) -> dict:
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.scientific_evidence import build_evidence

    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=({"feature_count": int(n_points)}
                     if n_points is not None else {}),
        warnings=warnings,
    )
    return payload


def register_moving_object_tools(registry: ToolRegistry) -> None:
    """Register the trajectory tool family (Pi 与 legacy 同面)。"""

    @tool(
        registry,
        name="trajectory_inspect",
        description=(
            "移动对象轨迹数据体检：按实体/时间字段探测可摄取性，输出字段清单、"
            "行级异常计数（缺失ID/不可解析时间/退化几何）、时间范围与时区假设。"
            "只读，不写任何状态。"
        ),
        tier=2,
        domains=["temporal", "data_access"],
        args_model=TrajectoryInspectArgs,
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("轨迹", "移动对象", "数据体检", "trajectory", "inspect"),
        failure_modes=("missing_data", "invalid_args", "ref_not_found"),
    )
    async def trajectory_inspect(
        dataset: str,
        entity_id_field: str = "entity_id",
        time_field: str = "timestamp",
        assumed_timezone: str = "UTC",
        session_id: str = "",
    ) -> dict:
        try:
            return await inspect_moving_object_source(
                session_id, dataset,
                entity_id_field=entity_id_field,
                time_field=time_field,
                assumed_timezone=assumed_timezone,
            )
        except TrajectoryServiceError as e:
            return _error(str(e), e.code)
        except Exception as e:  # noqa: BLE001 — tool face types all failures
            logger.error("[trajectory_inspect] Failed: %s", e, exc_info=True)
            return _error(f"轨迹数据体检失败: {e}")

    @tool(
        registry,
        name="trajectory_build",
        capabilities=["trajectory_build", "trajectory_dwell", "region_events"],
        anti_examples=("把轨迹画成两点 OD 弧线（用 od_flow_edges）",),
        description=(
            "移动对象轨迹构建与分析：点表→按实体规范观测流（时区/乱序/重复显式处理）→"
            "测地分段轨迹（时隙/速度跳变/位置矛盾断点可溯源）→速度/距离/方向区间估计→"
            "驻留识别（半径+时长阈值）→可选区域 enter/exit/cross/dwell 事件（推断区间诚实标注）。"
            "产出产品 ref 与轨迹/驻留/事件图层数据 ref（大本体不入 LLM 上下文）；"
            "地图落图请用 trajectory_apply。"
        ),
        tier=2,
        domains=["temporal", "analysis"],
        args_model=TrajectoryBuildArgs,
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        unit_semantics="meters;km/h;seconds",
        tags=("轨迹构建", "停留识别", "驻留", "时空事件", "trajectory", "dwell", "enter_exit"),
        failure_modes=("missing_data", "empty_result", "memory", "invalid_args"),
    )
    async def trajectory_build(
        dataset: str,
        entity_id_field: str = "entity_id",
        time_field: str = "timestamp",
        source_row_field: Optional[str] = None,
        assumed_timezone: str = "UTC",
        duplicate_time_policy: str = "keep_all",
        sort_policy: str = "time_asc",
        max_gap_s: float = 3600.0,
        max_speed_kmh: float = 300.0,
        max_jump_m: float = 50000.0,
        min_points_per_segment: int = 2,
        detect_dwells: bool = True,
        dwell_radius_m: float = 50.0,
        dwell_min_dwell_s: float = 120.0,
        dwell_min_points: int = 3,
        regions_ref: Optional[str] = None,
        region_id_field: str = "region_id",
        boundary_rule: str = "closed",
        detect_cross: bool = True,
        region_dwell_min_s: float = 60.0,
        detect_region_dwell: bool = True,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
        entity_filter: Optional[List[str]] = None,
        palette: Optional[List[str]] = None,
        width_min_px: float = 1.5,
        width_max_px: float = 7.0,
        show_direction: bool = True,
        session_id: str = "",
    ) -> dict:
        args = TrajectoryBuildArgs(
            dataset=dataset,
            entity_id_field=entity_id_field,
            time_field=time_field,
            source_row_field=source_row_field,
            assumed_timezone=assumed_timezone,
            duplicate_time_policy=duplicate_time_policy,
            sort_policy=sort_policy,
            max_gap_s=max_gap_s,
            max_speed_kmh=max_speed_kmh,
            max_jump_m=max_jump_m,
            min_points_per_segment=min_points_per_segment,
            detect_dwells=detect_dwells,
            dwell_radius_m=dwell_radius_m,
            dwell_min_dwell_s=dwell_min_dwell_s,
            dwell_min_points=dwell_min_points,
            regions_ref=regions_ref,
            region_id_field=region_id_field,
            boundary_rule=boundary_rule,
            detect_cross=detect_cross,
            region_dwell_min_s=region_dwell_min_s,
            detect_region_dwell=detect_region_dwell,
            time_start=time_start,
            time_end=time_end,
            entity_filter=entity_filter,
            palette=palette,
            width_min_px=width_min_px,
            width_max_px=width_max_px,
            show_direction=show_direction,
        )
        try:
            payload = await preview_trajectory_product(session_id, _config_from_args(args))
        except TrajectoryServiceError as e:
            return _error(str(e), e.code)
        except Exception as e:  # noqa: BLE001
            logger.error("[trajectory_build] Failed: %s", e, exc_info=True)
            return _error(f"轨迹构建失败: {e}")
        stats = payload.get("stats") or {}
        return _attach_evidence(
            payload,
            algorithm_id="moving_object.trajectory_build",
            tool="trajectory_build",
            parameters_applied={
                "max_gap_s": args.max_gap_s,
                "max_speed_kmh": args.max_speed_kmh,
                "max_jump_m": args.max_jump_m,
                "assumed_timezone": args.assumed_timezone,
                "boundary_rule": args.boundary_rule,
            },
            n_points=stats.get("observations"),
            warnings=payload.get("warnings") or None,
        )

    @tool(
        registry,
        name="trajectory_apply",
        capabilities=["trajectory_apply"],
        description=(
            "把 trajectory_build 的产品按一次 CAS 事务落图（轨迹线+方向+驻留+事件图层族，"
            "原子替换同族图层；用户显隐等展示状态在重应用时保留）。"
            "需要 expected_revision（取 preview/上一次 mutation 返回的 revision）。"
        ),
        tier=2,
        domains=["map"],
        args_model=TrajectoryApplyArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="mapspec_result",
        result_size_policy="inline_small",
        map_mutations=("add_layer", "remove_layer", "style_layer"),
        tags=("轨迹落图", "地图提交", "trajectory_apply", "CAS"),
        failure_modes=("stale_revision", "ref_not_found", "invalid_args"),
    )
    async def trajectory_apply(
        product_ref: str,
        expected_revision: int,
        mutation_id: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        try:
            return await apply_trajectory_product_preview(
                session_id, product_ref,
                expected_revision=expected_revision,
                mutation_id=mutation_id,
                origin="agent",
            )
        except TrajectoryServiceError as e:
            return _error(str(e), e.code)
        except Exception as e:  # noqa: BLE001
            logger.error("[trajectory_apply] Failed: %s", e, exc_info=True)
            return _error(f"轨迹落图失败: {e}")

    @tool(
        registry,
        name="trajectory_query",
        description=(
            "轨迹产品钻取查询：product（元数据/统计/质量）/ entity（该实体的轨迹线段）/"
            "dwell（驻留点）/ event（区域事件表），分页有界返回。"
        ),
        tier=2,
        domains=["temporal"],
        args_model=TrajectoryQueryArgs,
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="stats",
        result_size_policy="bounded",
        tags=("轨迹查询", "事件表", "trajectory_query", "钻取"),
        failure_modes=("ref_not_found", "invalid_args"),
    )
    async def trajectory_query(
        product_ref: str,
        kind: str = "product",
        entity_id: str = "",
        offset: int = 0,
        limit: int = 50,
        session_id: str = "",
    ) -> dict:
        try:
            return await query_trajectory_product(
                session_id, product_ref,
                kind=kind, entity_id=entity_id,
                offset=offset, limit=limit,
            )
        except TrajectoryServiceError as e:
            return _error(str(e), e.code)
        except Exception as e:  # noqa: BLE001
            logger.error("[trajectory_query] Failed: %s", e, exc_info=True)
            return _error(f"轨迹查询失败: {e}")
