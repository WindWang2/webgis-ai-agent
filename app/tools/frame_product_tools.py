"""多画幅产品控制工具族（frame_product_*，G15 WP09）。

薄工具面：参数校验 + 会话上下文 + 服务层转发；全部副作用经
``apply_gis_mutation``（MapSpec CAS），Pi 与 legacy 同走 ToolRegistry/
ToolDispatchService，无第二执行入口。

情境投影语义：产品契约（active_frame/sync groups/shared 分级）随
``frame_product_status`` 的 refs-only 摘要进入模型上下文 —— 「只改第二幅
标题」「取消第四幅联动」经 update 的键级 patch 精确落位，不触碰整份地图。
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class FrameProductPlanArgs(BaseModel):
    """计划参数（bind 的超集；plan 只读不写）。"""
    ref_id: str = Field(..., min_length=1, max_length=128,
                        description="会话数据 ref（画幅数据的真实来源）")
    product_kind: str = Field(..., description=(
        "time_series（跨真实时间桶）| category_compare（类别值并置）| "
        "variable_compare（多字段并置）| detail_frames（主图+详图）"))
    layer_id: Optional[str] = Field(None, max_length=128,
                                    description="目标图层 id（缺省=首个图层）")
    field: Optional[str] = Field(None, max_length=64,
                                 description="共享分级取值字段")
    split_field: Optional[str] = Field(None, max_length=64,
                                       description="category_compare 的类别字段")
    fields: Optional[str] = Field(None, max_length=512,
                                  description="variable_compare 的字段列表（逗号分隔）")
    time_field: Optional[str] = Field(None, max_length=64,
                                      description="time_series 时间字段（缺省=自动探测）")
    frames_limit: int = Field(6, ge=2, le=12,
                              description="画幅数上界（资源有界；常见 2/3/4/6）")
    # server-owned：schema 隐藏 + dispatch 注入（registry 656/1429 行机制）。
    session_id: Optional[str] = Field(None, max_length=128)


class FrameProductBindArgs(FrameProductPlanArgs):
    title: Optional[str] = Field(None, max_length=160, description="产品标题")
    shared: Optional[bool] = Field(None, description=(
        "是否共享色带分级（缺省=time_series/category_compare 共享、"
        "variable_compare/detail_frames 独立；资格不满足时诚实回落独立并披露）"))
    method: Optional[str] = Field(None, max_length=32,
                                  description="共享分级方法（quantiles/equal_interval/natural_breaks/…）")
    k: Optional[int] = Field(None, ge=2, le=9, description="分级段数")
    palette: Optional[str] = Field(None, max_length=32, description="色板 id")
    semantics: Optional[str] = Field(None, max_length=16, description=(
        "声明数值语义 count|ratio|index|other（与数据推导矛盾时拒绝共享）"))
    unit: Optional[str] = Field(None, max_length=32, description="单位（进图例）")
    denominator: Optional[str] = Field(None, max_length=64, description=(
        "分母字段/口径（rate 语义共享分级的必备声明）"))
    expected_revision: Optional[int] = Field(None, ge=0, description="CAS 基线（可选）")
    force: bool = Field(False, description="已绑定时强制重建")


class FrameProductUpdateArgs(BaseModel):
    patch: dict = Field(..., description=(
        "键级补丁：active_frame / frames（整表，frame_id 稳定）/ grid / "
        "sync_group（在 frames 内）等 —— 只改声明的键"))
    expected_revision: Optional[int] = Field(None, ge=0)
    # server-owned：schema 隐藏 + dispatch 注入。
    session_id: Optional[str] = Field(None, max_length=128)


def _parse_fields(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return [f.strip() for f in raw.split(",") if f.strip()]


def _bind_kwargs(args: FrameProductBindArgs) -> dict:
    return dict(
        product_kind=args.product_kind, layer_id=args.layer_id or None,
        field=args.field or None, split_field=args.split_field or None,
        fields=_parse_fields(args.fields), time_field=args.time_field or None,
        frames_limit=args.frames_limit, title=args.title or None,
        shared=args.shared, method=args.method or None, k=args.k,
        palette=args.palette or None, semantics=args.semantics or None,
        unit=args.unit or None, denominator=args.denominator or None,
        expected_revision=args.expected_revision, force=args.force,
    )


class _StatusArgs(BaseModel):
    session_id: Optional[str] = Field(None, max_length=128)


class _UnbindArgs(BaseModel):
    expected_revision: Optional[int] = Field(None, ge=0)
    session_id: Optional[str] = Field(None, max_length=128)


def register_frame_product_tools(registry: ToolRegistry) -> None:
    """注册多画幅产品工具族（Pi 与 legacy 同面）。"""

    @tool(
        registry,
        name="frame_product_plan",
        capabilities=["frame_product_plan"],
        description=(
            "为多画幅（小倍数）地图产品制定计划：从真实数据推导画幅序列"
            "（时间桶/类别值/字段/详图范围），返回带证据的帧计划与不满足的"
            "义务 —— 只读不写。单期数据、字段不足、类别值 <2 都会得到可"
            "修正的拒绝原因，绝不虚构年份或类别。"
        ),
        tier=2,
        domains=["cartography", "map"],
        args_model=FrameProductPlanArgs,
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("小倍数", "多画幅", "并置", "对比", "small multiples"),
        failure_modes=("missing_data", "invalid_args", "single_period",
                       "field_not_quantitative", "category_values_insufficient"),
    )
    async def frame_product_plan(
        ref_id: str, product_kind: str, layer_id: Optional[str] = None,
        field: Optional[str] = None, split_field: Optional[str] = None,
        fields: Optional[str] = None, time_field: Optional[str] = None,
        frames_limit: int = 6, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.frame_products import service as fps

        try:
            resolved, descriptor, fc = await fps._resolve_ref_fc(session_id, ref_id)
            if fc is None:
                return fps._refusal(
                    "ref_unavailable",
                    f"ref {ref_id!r} 不可读或不是 FeatureCollection")
            plan = fps.plan_from_data(
                fc, product_kind=product_kind,
                layer_id=layer_id or "", ref_id=resolved,
                content_revision=descriptor.get("content_revision"),
                field=field, split_field=split_field,
                fields=_parse_fields(fields), time_field=time_field,
                frames_limit=frames_limit,
            )
            if plan.get("ok"):
                # refs-only：帧计划去 FC 载荷。
                plan["frames"] = [
                    {k: v for k, v in f.items() if k != "shared_fc"}
                    for f in plan["frames"]
                ]
            return plan
        except Exception as e:  # noqa: BLE001 — 工具面统一错误契约
            logger.error("[frame_product_plan] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="frame_product_bind",
        capabilities=["frame_product_bind"],
        description=(
            "把会话数据绑定为多画幅地图产品：登记稳定画幅身份"
            "（layout.frames）+ 写入产品契约（frame_product）+ 计算跨画幅共享"
            "分级（真实数据并集只算一次；计数与率不共享色带，资格拒绝时回落"
            "独立图例并披露）。前置：目标图层已存在；单期/字段不足会被诚实"
            "拒绝，不产出空壳产品。"
        ),
        tier=2,
        domains=["cartography", "map"],
        args_model=FrameProductBindArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("小倍数", "多画幅", "绑定", "共享分级", "bind"),
        failure_modes=("missing_data", "invalid_args", "already_bound",
                       "layer_not_found", "single_period", "mutation_rejected"),
    )
    async def frame_product_bind(
        ref_id: str, product_kind: str, layer_id: Optional[str] = None,
        field: Optional[str] = None, split_field: Optional[str] = None,
        fields: Optional[str] = None, time_field: Optional[str] = None,
        frames_limit: int = 6, title: Optional[str] = None,
        shared: Optional[bool] = None, method: Optional[str] = None,
        k: Optional[int] = None, palette: Optional[str] = None,
        semantics: Optional[str] = None, unit: Optional[str] = None,
        denominator: Optional[str] = None,
        expected_revision: Optional[int] = None, force: bool = False,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.frame_products import service as fps

        try:
            args = FrameProductBindArgs(
                ref_id=ref_id, product_kind=product_kind, layer_id=layer_id,
                field=field, split_field=split_field, fields=fields,
                time_field=time_field, frames_limit=frames_limit, title=title,
                shared=shared, method=method, k=k, palette=palette,
                semantics=semantics, unit=unit, denominator=denominator,
                expected_revision=expected_revision, force=force)
            return await fps.bind_frame_product(session_id, ref_id,
                                                **_bind_kwargs(args))
        except Exception as e:  # noqa: BLE001
            logger.error("[frame_product_bind] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="frame_product_update",
        capabilities=["frame_product_update"],
        description=(
            "增量更新多画幅产品（键级 CAS patch）：active_frame（当前编辑/"
            "对话画幅）、frames（排序/标题/同步组/图例策略 —— frame_id 身份"
            "稳定）、grid 等。只改声明的键；悬空 frame 引用被拒绝。"
        ),
        tier=2,
        domains=["cartography", "map"],
        args_model=FrameProductUpdateArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("小倍数", "多画幅", "更新", "联动组", "update"),
        failure_modes=("invalid_args", "mutation_rejected", "not_bound"),
    )
    async def frame_product_update(
        patch: dict, expected_revision: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.frame_products import service as fps

        try:
            return await fps.update_frame_product(
                session_id, patch, expected_revision=expected_revision)
        except Exception as e:  # noqa: BLE001
            logger.error("[frame_product_update] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="frame_product_status",
        capabilities=["frame_product_status"],
        description=(
            "多画幅产品状态（refs-only）：激活画幅、同步组、逐帧绑定/解析"
            "指纹、共享分级与资格。供对话定位「这幅图」—— 不带的画幅语义"
            "问题先看 status 再精确 patch。"
        ),
        tier=2,
        domains=["cartography", "map"],
        args_model=_StatusArgs,
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("小倍数", "多画幅", "状态", "inspect"),
        failure_modes=(),
    )
    async def frame_product_status(session_id: str = "") -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.frame_products import service as fps

        try:
            return await fps.frame_product_status(session_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[frame_product_status] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="frame_product_unbind",
        capabilities=["frame_product_unbind"],
        description=(
            "解绑多画幅产品（移除产品语义；画幅身份登记保留，可重新绑定）。"
        ),
        tier=2,
        domains=["cartography", "map"],
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        args_model=_UnbindArgs,
        tags=("小倍数", "多画幅", "解绑", "unbind"),
        failure_modes=("mutation_rejected",),
    )
    async def frame_product_unbind(
        expected_revision: Optional[int] = None, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.frame_products import service as fps

        try:
            return await fps.unbind_frame_product(
                session_id, expected_revision=expected_revision)
        except Exception as e:  # noqa: BLE001
            logger.error("[frame_product_unbind] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}
