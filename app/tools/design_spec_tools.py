"""Design Spec agent tools —— 制图设计规范的 Agent 原生入口（WP1）。

与 REST（``/api/v1/design-specs``）共用同一 service/存储权威
（``app/services/design_specs``）：Agent 经 ToolRegistry dispatch（Pi 长尾经
``webgis_execute`` 代理直达本模块），前端经 router —— 两面零语义漂移。

回执纪律（有界）：列表 ≤20 条 brief；inspect/preview 携带完整 typed payload
（设计规范本身是有界引用文档）+ 校验投影；不携带任何数据值/凭证。
"""
import logging
from typing import Optional

from app.services.design_specs.errors import DesignSpecError
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _actor_context() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """调用方身份（provenance origin；匿名 = builtin-only）。"""
    from app.services.jobs.context import current_origin

    origin = current_origin()
    if origin is None:
        return None, None, None
    owner = getattr(origin, "owner_id", None)
    org_id = getattr(origin, "org_id", None)
    role = getattr(origin, "role", None)
    try:
        org_id = int(org_id) if org_id is not None else None
    except (TypeError, ValueError):
        org_id = None
    return owner, org_id, role


def _guard(fn):
    """领域 typed error → 结构化回执（同 print_layout 工具纪律）。

    ``functools.wraps`` 必须保留：@tool 在 _guard 之上对包装器做签名
    内省生成 schema，丢了 __wrapped__ 会退化成 args/kwargs 泛化面。
    """
    import functools

    from fastapi import HTTPException

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except DesignSpecError as exc:
            payload = exc.error_payload()
            payload["correction_hint"] = _hint_for(exc)
            return payload
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "code": "design_spec_unexpected_error",
                    "message": str(exc)[:300]}

    return wrapper


def _hint_for(exc: DesignSpecError) -> str:
    code = getattr(exc, "code", "")
    if code == "DESIGN_SPEC_NOT_FOUND":
        return "用 webgis_design_spec_list 查看当前可见的设计规范 id"
    if code == "DESIGN_SPEC_NAME_CONFLICT":
        return "同 scope 内规范名唯一 —— 换名或查看既有规范后复用"
    if code == "DESIGN_SPEC_INVALID":
        return "按 violations 逐条修正 payload（引用必须存在；禁止数据值/ref:/凭证字段）"
    return "检查参数后重试"


def register_design_spec_tools(registry: ToolRegistry) -> None:
    """注册设计规范工具（WP1 权威面；WP2/WP4 增量 extract/recommend/apply）。"""

    @tool(
        registry, name="webgis_design_spec_list",
        tier=2, domains=["cartography"],
        description=(
            "列出当前调用者可见的制图设计规范（builtin ∪ 本人 ∪ 同组织）。"
            "设计规范是可复用的制图引用与约束文档（主题/色带/分类策略/组件要求/"
            "字体/输出 profile/数据角色/覆盖策略），供系列地图统一表达。"
            "\n何时用：开始系列制图前查看可用规范；或确认某名字是否已存在。"
        ),
        param_descriptions={"limit": "返回上限（1-20，默认 10）"},
        side_effect="pure", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("设计规范", "制图", "系列地图"),
        failure_modes=("invalid_args",),
    )
    @_guard
    def webgis_design_spec_list(limit: int = 10) -> dict:
        from app.core.database import SessionLocal

        from app.services.design_specs.versioning import list_specs

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            rows = list_specs(db, owner, org_id, limit=max(1, min(int(limit), 20)))
            return {
                "success": True,
                "items": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "status": r.status,
                        "is_builtin": bool(r.is_builtin),
                        "current_version": r.current_version,
                        "structural_digest": r.structural_digest,
                        "description": (r.description or "")[:120],
                    }
                    for r in rows
                ],
            }

    @tool(
        registry, name="webgis_design_spec_inspect",
        tier=2, domains=["cartography"],
        description=(
            "查看一个设计规范的完整定义：typed payload + 引用校验投影"
            "（主题/色带/模型/组件/字体/纸张是否真实存在于权威 registry）。"
            "\n何时用：应用或传播前核对规范内容与约束；排查引用违规。"
        ),
        param_descriptions={"spec_id": "设计规范 id（webgis_design_spec_list 获得）"},
        side_effect="pure", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("设计规范", "制图"),
        failure_modes=("invalid_args", "not_found"),
    )
    @_guard
    def webgis_design_spec_inspect(spec_id: str) -> dict:
        from app.core.database import SessionLocal

        from app.services.design_specs.contract import validate_design_spec_payload
        from app.services.design_specs.versioning import (
            design_spec_visible,
            get_spec,
        )

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            spec = get_spec(db, str(spec_id))
            if not design_spec_visible(spec, owner, org_id):
                return {"success": False, "code": "DESIGN_SPEC_NOT_FOUND",
                        "message": "design spec not found",
                        "correction_hint": "规范不存在或对当前调用者不可见"}
            report = validate_design_spec_payload(spec.payload or {})
            return {
                "success": True,
                "spec": {
                    "id": spec.id,
                    "name": spec.name,
                    "status": spec.status,
                    "current_version": spec.current_version,
                    "structural_digest": spec.structural_digest,
                    "retired": spec.retired_at is not None,
                },
                "payload": spec.payload,
                "reference_validation": {
                    "ok": report["ok"],
                    "violations": report["violations"],
                    "warnings": report["warnings"],
                },
            }

    @tool(
        registry, name="webgis_design_spec_preview",
        tier=2, domains=["cartography"],
        description=(
            "预览设计规范的有效投影：继承链深合并结果、支持的渲染目标"
            "（主题声明 × 字体/纸张真实性）、引用冲突、结构与解析 digest。只读。"
            "\n何时用：应用前确认规范可用性；比较不同版本的生效内容。"
        ),
        param_descriptions={
            "spec_id": "设计规范 id",
            "version": "版本号（缺省 = 当前版；整数）",
        },
        side_effect="pure", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        tags=("设计规范", "预览"),
        failure_modes=("invalid_args", "not_found"),
    )
    @_guard
    def webgis_design_spec_preview(spec_id: str, version: Optional[int] = None) -> dict:
        from app.core.database import SessionLocal

        from app.services.design_specs.preview import build_design_spec_preview
        from app.services.design_specs.versioning import design_spec_visible, get_spec

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            spec = get_spec(db, str(spec_id))
            if not design_spec_visible(spec, owner, org_id):
                return {"success": False, "code": "DESIGN_SPEC_NOT_FOUND",
                        "message": "design spec not found",
                        "correction_hint": "规范不存在或对当前调用者不可见"}
            result = build_design_spec_preview(db, str(spec_id), version=version)
            # 有界化：payload 由 inspect 携带；preview 聚焦投影与冲突
            result.pop("effective_payload", None)
            result.setdefault("success", True)
            return result

    @tool(
        registry, name="webgis_design_spec_create",
        tier=2, domains=["cartography"],
        description=(
            "新建制图设计规范（version 1，校验 fail-closed）。payload 是 "
            "design_spec.v1 typed 文档：name/color(theme_id,palettes)/"
            "classification(default_method,k,shared_required)/typography"
            "(font_resource)/components(required,forbidden)/output(targets,"
            "paper_profile,dpi)/data_roles(name,value_type,measurement,unit,"
            "comparable_across_maps)/overrides(user_overridable,locked)/lineage。"
            "\n红线：引用必须真实存在（对照权威 registry）；禁止携带数据断点/"
            "inline GeoJSON/ref: 游标/凭证字段 —— 违规返回逐条 violations。"
            "\n何时用：从零起草规范；或接 webgis_map_design_extract 的草案。"
        ),
        param_descriptions={"payload": "design_spec.v1 typed 字典（见描述）"},
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("project_memory_write",),
        tags=("设计规范", "创建"),
        failure_modes=("invalid_args", "conflict"),
    )
    @_guard
    def webgis_design_spec_create(payload: dict) -> dict:
        from app.core.database import SessionLocal

        from app.services.design_specs.versioning import create_spec

        owner, org_id, role = _actor_context()
        if not owner:
            return {"success": False, "code": "DESIGN_SPEC_FORBIDDEN",
                    "message": "creating a design spec requires an authenticated user",
                    "correction_hint": "登录后重试；匿名只能查看内置规范"}
        with SessionLocal() as db:
            spec = create_spec(db, payload, creator_id=owner, org_id=org_id,
                               created_by=owner)
            return {
                "success": True,
                "spec": {
                    "id": spec.id,
                    "name": spec.name,
                    "status": spec.status,
                    "current_version": spec.current_version,
                    "structural_digest": spec.structural_digest,
                },
            }

    @tool(
        registry, name="webgis_design_spec_version",
        tier=2, domains=["cartography"],
        description=(
            "设计规范版本操作：落新版本（可继承父版本深合并）、发布、退役、"
            "失效单版本。action=create_version|publish|retire|deprecate_version。"
            "\n何时用：修订规范（新版本）；定稿（publish，校验门）；"
            "弃用（retire 软退役，既有系列 pin 仍可解析）。"
        ),
        param_descriptions={
            "spec_id": "设计规范 id",
            "action": "create_version | publish | retire | deprecate_version",
            "payload": "action=create_version 时的新版本 payload（typed）",
            "parent_version_id": "create_version 可选父版本（同规范内继承）",
            "note": "retire/deprecate 的说明（≤500）",
        },
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("project_memory_write",),
        tags=("设计规范", "版本"),
        failure_modes=("invalid_args", "conflict", "forbidden"),
    )
    @_guard
    def webgis_design_spec_version(
        spec_id: str,
        action: str,
        payload: Optional[dict] = None,
        parent_version_id: Optional[str] = None,
        note: str = "",
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.design_specs.versioning import (
            create_version,
            deprecate_version,
            design_spec_can_write,
            design_spec_visible,
            get_spec,
            publish_spec,
            retire_spec,
        )

        owner, org_id, role = _actor_context()
        with SessionLocal() as db:
            spec = get_spec(db, str(spec_id))
            if not design_spec_visible(spec, owner, org_id):
                return {"success": False, "code": "DESIGN_SPEC_NOT_FOUND",
                        "message": "design spec not found",
                        "correction_hint": "规范不存在或对当前调用者不可见"}
            if not design_spec_can_write(spec, owner, org_id, role):
                return {"success": False, "code": "DESIGN_SPEC_FORBIDDEN",
                        "message": "spec is read-only for this caller",
                        "correction_hint": "只有创建者或同组织 admin 可写"}

            if action == "create_version":
                if payload is None:
                    return {"success": False, "code": "invalid_args",
                            "message": "action=create_version 需要 payload"}
                row = create_version(
                    db, str(spec_id), payload,
                    parent_version_id=parent_version_id, created_by=owner,
                )
                return {"success": True, "action": action, "version": row.version,
                        "structural_digest": row.structural_digest}
            if action == "publish":
                spec = publish_spec(db, str(spec_id))
                return {"success": True, "action": action, "status": spec.status}
            if action == "retire":
                spec = retire_spec(db, str(spec_id), note=str(note or ""))
                return {"success": True, "action": action, "status": spec.status}
            if action == "deprecate_version":
                row = deprecate_version(db, str(spec_id), None, note=str(note or ""))
                return {"success": True, "action": action, "version": row.version,
                        "deprecated": row.deprecated_at is not None}
            return {
                "success": False, "code": "invalid_args",
                "message": f"unknown action: {action}",
                "correction_hint": "create_version | publish | retire | deprecate_version",
            }
