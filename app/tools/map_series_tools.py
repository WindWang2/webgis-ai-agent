"""Map Series agent tools —— 系列地图计划的 Agent 原生入口（WP3）。

与 REST（``/api/v1/map-series``）共用同一 service/存储权威
（``app/services/map_series``）：Agent 经 ToolRegistry dispatch（Pi 长尾经
``webgis_execute`` 代理直达本模块），前端经 router —— 两面零语义漂移。

回执纪律（有界）：list ≤20 条 brief；inspect 携带完整 typed payload（系列
计划本身是有界引用文档）；consistency ≤100 findings；apply 返回逐图
receipts（绝不宣称原子批量）。成员写与传播 apply = state_mutation；
读 = pure。
"""
import logging
from typing import Optional

from app.services.map_series.errors import MapSeriesError
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _actor_context() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """调用方身份 —— 复用 design_spec_tools 的单一实现（不复制语义）。"""
    from app.tools.design_spec_tools import _actor_context as _ctx

    return _ctx()


def _guard(fn):
    """领域 typed error → 结构化回执。

    形状复制自 ``app/tools/design_spec_tools._guard``（WP1）；本域映射
    MapSeriesError。``functools.wraps`` 必须保留：@tool 在 _guard 之上对
    包装器做签名内省生成 schema，丢了 ``__wrapped__`` 会退化成 args/kwargs
    泛化面。
    """
    import functools

    from fastapi import HTTPException

    from app.services.map_series.errors import MapSeriesError

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except MapSeriesError as exc:
            payload = exc.error_payload()
            payload["correction_hint"] = _hint_for(exc)
            return payload
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "code": "map_series_unexpected_error",
                    "message": str(exc)[:300]}

    return wrapper


def _hint_for(exc: MapSeriesError) -> str:
    code = getattr(exc, "code", "")
    if code == "MAP_SERIES_NOT_FOUND":
        return "用 webgis_map_series_list 查看当前可见的系列 id"
    if code == "MAP_SERIES_NAME_CONFLICT":
        return "同 scope 内系列名唯一 —— 换名或查看既有系列后复用"
    if code == "MAP_SERIES_INVALID":
        return "按 violations 逐条修正 payload（成员 ≤50；禁止数据值/ref:/凭证字段）"
    if code == "MAP_SERIES_FORBIDDEN":
        return "只有创建者或同组织 admin 可写该系列"
    return "检查参数后重试"


def register_map_series_tools(registry: ToolRegistry) -> None:
    """注册系列地图工具（WP3 权威面；WP4/WP5 增量前端面与生产执行）。"""

    @tool(
        registry, name="webgis_map_series_list",
        tier=2, domains=["cartography"],
        description=(
            "列出当前调用者可见的系列地图计划（builtin ∪ 本人 ∪ 同组织）。"
            "系列计划 pin 一个设计规范并登记成员（各自 session 的地图引用）、"
            "可比性契约与输出预算；成员真实地图状态仍在各自会话。"
            "\n何时用：开始系列一致性/传播前查看系列 id；确认某名字是否已存在。"
        ),
        param_descriptions={"limit": "返回上限（1-20，默认 10）"},
        side_effect="pure", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("系列地图", "制图", "一致性"),
        failure_modes=("invalid_args",),
    )
    @_guard
    async def webgis_map_series_list(limit: int = 10) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.store import list_series

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            rows = list_series(db, owner, org_id, limit=max(1, min(int(limit), 20)))
            return {
                "success": True,
                "items": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "status": r.status,
                        "is_builtin": bool(r.is_builtin),
                        "design_spec_id": r.design_spec_id,
                        "spec_version": r.spec_version,
                        "current_version": r.current_version,
                        "member_count": len((r.payload or {}).get("members") or []),
                        "structural_digest": r.structural_digest,
                        "description": (r.description or "")[:120],
                    }
                    for r in rows
                ],
            }

    @tool(
        registry, name="webgis_map_series_inspect",
        tier=2, domains=["cartography"],
        description=(
            "查看一个系列地图计划的完整定义：typed payload（成员/可比性/维度/"
            "输出预算）、设计规范 pin 与成员会话可达性（不读地图内容）。"
            "\n何时用：一致性/传播前核对成员与 pin；排查不可解析成员。"
        ),
        param_descriptions={"series_id": "系列 id（webgis_map_series_list 获得）"},
        side_effect="pure", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("系列地图", "检查"),
        failure_modes=("invalid_args", "not_found"),
    )
    @_guard
    async def webgis_map_series_inspect(series_id: str) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.store import (
            get_series,
            map_series_visible,
        )

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            series = get_series(db, str(series_id))
            if not map_series_visible(series, owner, org_id):
                return {"success": False, "code": "MAP_SERIES_NOT_FOUND",
                        "message": "map series not found",
                        "correction_hint": "系列不存在或对当前调用者不可见"}
            return {
                "success": True,
                "series": {
                    "id": series.id,
                    "name": series.name,
                    "status": series.status,
                    "design_spec_id": series.design_spec_id,
                    "spec_version": series.spec_version,
                    "current_version": series.current_version,
                    "structural_digest": series.structural_digest,
                    "retired": series.retired_at is not None,
                },
                "payload": series.payload,
            }

    @tool(
        registry, name="webgis_map_series_create",
        tier=2, domains=["cartography"],
        description=(
            "新建系列地图计划（version 1，校验 fail-closed）。payload 是 "
            "map_series.v1 typed 文档：name/design_spec_id（必填，WP1 权威）/"
            "design_spec_version（可选 pin）/members（≤50：member_id/title/"
            "session_id/role/dimension_tags/data_bindings/locked_fields）/"
            "comparability/dimension/output。"
            "\n红线：不建第二地图状态（成员只是 session 引用）；"
            "禁止携带数据断点/inline GeoJSON/ref: 游标/凭证字段。"
            "\n何时用：多图统一表达（分区/时序/指标系列）前先立计划。"
        ),
        param_descriptions={"payload": "map_series.v1 typed 字典（见描述）"},
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("project_memory_write",),
        tags=("系列地图", "创建"),
        failure_modes=("invalid_args", "conflict"),
    )
    @_guard
    async def webgis_map_series_create(payload: dict) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.store import create_series

        owner, org_id, role = _actor_context()
        if not owner:
            return {"success": False, "code": "MAP_SERIES_FORBIDDEN",
                    "message": "creating a map series requires an authenticated user",
                    "correction_hint": "登录后重试；匿名只能查看内置系列"}
        with SessionLocal() as db:
            series = create_series(db, payload, creator_id=owner, org_id=org_id,
                                   created_by=owner)
            return {
                "success": True,
                "series": {
                    "id": series.id,
                    "name": series.name,
                    "status": series.status,
                    "design_spec_id": series.design_spec_id,
                    "spec_version": series.spec_version,
                    "current_version": series.current_version,
                    "structural_digest": series.structural_digest,
                },
            }

    @tool(
        registry, name="webgis_map_series_add_member",
        tier=2, domains=["cartography"],
        description=(
            "向系列地图计划增/改单个成员（按 member_id upsert；整计划重校验）"
            "或删除成员（action=remove）。成员是 session 引用 + 表达计划字段，"
            "不是地图拷贝 —— 真实地图状态仍在各自会话。"
            "\n何时用：系列建好后逐个挂接成员地图；或调整成员角色/绑定/锁。"
        ),
        param_descriptions={
            "series_id": "系列 id",
            "member": "成员字典（member_id/title/session_id 必填）",
            "action": "upsert（默认）| remove",
            "member_id": "action=remove 时的成员 id",
        },
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("project_memory_write",),
        tags=("系列地图", "成员"),
        failure_modes=("invalid_args", "conflict", "not_found"),
    )
    @_guard
    async def webgis_map_series_add_member(
        series_id: str,
        member: Optional[dict] = None,
        action: str = "upsert",
        member_id: Optional[str] = None,
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.store import (
            map_series_can_write,
            remove_member,
            upsert_member,
        )

        owner, org_id, role = _actor_context()
        with SessionLocal() as db:
            series = get_series_checked(db, str(series_id), owner, org_id)
            if not map_series_can_write(series, owner, org_id, role):
                return {"success": False, "code": "MAP_SERIES_FORBIDDEN",
                        "message": "series is read-only for this caller",
                        "correction_hint": "只有创建者或同组织 admin 可写"}
            if action == "remove":
                if not member_id:
                    return {"success": False, "code": "invalid_args",
                            "message": "action=remove 需要 member_id"}
                remove_member(db, str(series_id), str(member_id))
                return {"success": True, "action": "remove", "member_id": member_id}
            if member is None:
                return {"success": False, "code": "invalid_args",
                        "message": "action=upsert 需要 member 字典"}
            series = upsert_member(db, str(series_id), member)
            return {
                "success": True,
                "action": "upsert",
                "member_count": len((series.payload or {}).get("members") or []),
            }

    @tool(
        registry, name="webgis_map_series_consistency",
        tier=2, domains=["cartography"],
        description=(
            "运行系列地图跨成员一致性检查（只读）：规范 pin 可解析性、可比角色"
            "单位、共享分类/色带一致性、标题/来源/图例组件缺席、资产版本漂移、"
            "视域分歧（披露）、锁定字段冲突。结构化 findings，每条可定位到 "
            "member/component。"
            "\n何时用：传播前体检；发布系列前核查。"
        ),
        param_descriptions={
            "series_id": "系列 id",
            "intents": "可选传播意图（检查 locked_fields 冲突；形状同传播 preview）",
        },
        side_effect="pure", deterministic=True, network=False,
        latency_class="medium", memory_class="light", scale_class="medium",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("系列地图", "一致性"),
        failure_modes=("invalid_args", "not_found"),
    )
    @_guard
    async def webgis_map_series_consistency(
        series_id: str, intents: Optional[dict] = None
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.consistency import run_series_consistency

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            get_series_checked(db, str(series_id), owner, org_id)
            result = await run_series_consistency(db, str(series_id), intents=intents)
            return {"success": True, **result}

    @tool(
        registry, name="webgis_map_series_propagation_preview",
        tier=2, domains=["cartography"],
        description=(
            "预览传播意图对系列成员的影响（只读）：受影响成员、每成员将变更的"
            "字段、锁定字段（user-wins skip 披露）、不可解析成员、逐成员 "
            "eligibility。intents typed：{palette?/classification?/components?/"
            "typography?}，不携带数据引用。"
            "\n何时用：apply 前核对影响面；向用户解释哪些图会被改、哪些被锁跳过。"
        ),
        param_descriptions={
            "series_id": "系列 id",
            "intents": "传播意图 typed 字典",
            "spec_version": "可选：按该系列版本的计划传播（缺省当前）",
        },
        side_effect="pure", deterministic=True, network=False,
        latency_class="medium", memory_class="light", scale_class="medium",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("系列地图", "传播", "预览"),
        failure_modes=("invalid_args", "not_found"),
    )
    @_guard
    async def webgis_map_series_propagation_preview(
        series_id: str, intents: dict, spec_version: Optional[int] = None
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.propagation import build_propagation_preview

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            get_series_checked(db, str(series_id), owner, org_id)
            result = await build_propagation_preview(
                db, str(series_id), spec_version=spec_version, intents=intents
            )
            return {"success": True, **result}

    @tool(
        registry, name="webgis_map_series_propagation_apply",
        tier=2, domains=["cartography"],
        description=(
            "把传播意图逐成员应用到各自会话（逐图 CAS；绝不宣称原子批量）。"
            "返回逐图 receipts {member_id, status: committed|superseded|failed|"
            "skipped_locked, revision, error?}；单成员失败不影响后续；被用户锁定"
            "的成员/字段 skip 并披露。intents 只表达设计投影（palette/分类方法/"
            "组件/版式），绝不跨图搬运数据引用。"
            "\n何时用：preview 确认后执行；对失败/superseded 成员按 hint 重试。"
        ),
        param_descriptions={
            "series_id": "系列 id",
            "intents": "传播意图 typed 字典（与 preview 同构）",
            "spec_version": "可选：按该系列版本的计划传播（缺省当前）",
            "expected_revisions": "可选：{member_id: 期望 revision}（CAS；过期如实 superseded）",
        },
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="medium", memory_class="light", scale_class="medium",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("project_memory_write",),
        tags=("系列地图", "传播", "应用"),
        failure_modes=("invalid_args", "not_found", "forbidden"),
    )
    @_guard
    async def webgis_map_series_propagation_apply(
        series_id: str,
        intents: dict,
        spec_version: Optional[int] = None,
        expected_revisions: Optional[dict] = None,
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.propagation import apply_propagation
        from app.services.map_series.store import map_series_can_write

        owner, org_id, role = _actor_context()
        if not owner:
            return {"success": False, "code": "MAP_SERIES_FORBIDDEN",
                    "message": "propagation apply requires an authenticated user",
                    "correction_hint": "登录后重试"}
        with SessionLocal() as db:
            series = get_series_checked(db, str(series_id), owner, org_id)
            if not map_series_can_write(series, owner, org_id, role):
                return {"success": False, "code": "MAP_SERIES_FORBIDDEN",
                        "message": "series is read-only for this caller",
                        "correction_hint": "只有创建者或同组织 admin 可执行传播"}
            result = await apply_propagation(
                db, str(series_id),
                spec_version=spec_version,
                intents=intents,
                actor=owner,
                expected_revisions=expected_revisions,
            )
            return {"success": True, **result}


def get_series_checked(db, series_id: str, owner, org_id):
    """可见性判定（不可见与不存在同回执 not_found —— 工具侧不泄露存在性）。"""
    from app.services.map_series.errors import MapSeriesNotFoundError
    from app.services.map_series.store import get_series, map_series_visible

    series = get_series(db, series_id)
    if not map_series_visible(series, owner, org_id):
        raise MapSeriesNotFoundError(f"map series '{series_id}' not found")
    return series


__all__ = ["register_map_series_tools"]
