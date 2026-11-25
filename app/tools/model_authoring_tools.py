"""模型构建器工具面（可视化 GIS 模型构建器 · Pi/Agent 生产入口）。

八个 tier-2 工具（与 REST 同一 ModelAuthoringService 链路，零旁路）：

- ``webgis_model_builder_capabilities``   可声明节点目录（只读）
- ``webgis_model_builder_create_draft``   结构化文档 → 草稿（CAS rev=1）
- ``webgis_model_builder_inspect``        草稿 + typed findings 投影
- ``webgis_model_builder_patch``          受限图补丁（add/remove/set 系列，
                                          无自由代码；服务端白名单裁决）
- ``webgis_model_builder_validate``       静态验证（只读）
- ``webgis_model_builder_publish``        校验零 error 才可发布（semver）
- ``webgis_model_builder_instantiate_run`` 实例化+绑定+运行（既有 runtime）
- ``webgis_model_builder_list_models``    项目目录（发布指针+版本）

纪律：写操作过项目写权限；owner 域从 ToolExecutionContext 派生；返回
载荷有界（文档摘要/findings/实例投影——绝不含原始数据载荷）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.failure_observation import observe_optional_failure
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_DOCS_BYTES = 256 * 1024
_MAX_PATCH_OPS = 16


def _caller_identity() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """(user_id, org_id, session_id)——与 project_tools 同源。"""
    from app.services.gis_harness.failure_observation import (
        observe_optional_failure as _ob,
    )
    try:
        from app.services.provenance.context import get_tool_execution_context

        ctx = get_tool_execution_context()
        if ctx is not None:
            return ctx.user_id, ctx.org_id, ctx.session_id
    except Exception:  # noqa: BLE001 — context unavailable → anonymous
        _ob("model-authoring-caller-identity")
    return None, None, None


def _owner_scope(user_id: Optional[str], session_id: Optional[str]) -> str:
    from app.services.geocompute.executor import owner_scope_for

    return owner_scope_for({"user_id": user_id} if user_id else None,
                           session_id or None)


def _authorize_project_write(project_id: str, user_id: Optional[str],
                             org_id: Optional[int]) -> bool:
    if not project_id:
        return False
    try:
        from app.core.database import SessionLocal
        from app.services.project_service import ProjectService

        with SessionLocal() as db:
            return ProjectService.get_project_for_write(
                db, project_id, user_id=user_id, org_id=org_id) is not None
    except Exception:  # noqa: BLE001 — fail closed
        observe_optional_failure("model-authoring-project-authz")
        return False


def _err(code: str, detail: str = "", **extra: Any) -> dict:
    out: Dict[str, Any] = {"success": False, "error": code,
                           "correction_hint": detail}
    if extra:
        out.update(extra)
    return out


def _validate_document_dict(document: Dict[str, Any]) -> Any:
    from app.schemas.model_authoring_schema import ModelDocument

    return ModelDocument.model_validate(document)


def register_model_authoring_tools(registry: ToolRegistry) -> None:
    """注册 8 个模型构建器工具（append-only；不改既有工具）。"""

    # ── 1：能力目录 ──────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_capabilities",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "模型构建器能力目录：可声明的 analysis 能力（native 状态）、"
            "transform 算子词表、数据角色词表。用于装配用户 GIS 模型草稿前"
            "确认节点词汇。只读。"
        ),
        param_descriptions={},
        side_effect="read_only",
        data_mutations=[],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "capabilities", "目录"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=(),
    )
    async def webgis_model_builder_capabilities() -> dict:
        from app.schemas.model_authoring_schema import EXECUTABLE_TRANSFORM_OPS

        def _load() -> List[Dict[str, Any]]:
            from app.lib.gis.capability_registry import get_capability_registry

            reg = get_capability_registry()
            out: List[Dict[str, Any]] = []
            for cap_id in reg.all_ids():
                desc = reg.get(cap_id)
                status = str(getattr(desc, "status", "") or "")
                if status != "native":
                    continue
                out.append({
                    "capability": cap_id,
                    "inputs": [str(t) for t in (
                        getattr(desc, "input_artifact_types", []) or [])][:6],
                    "outputs": [str(t) for t in (
                        getattr(desc, "output_artifact_types", []) or [])][:6],
                })
            return sorted(out, key=lambda e: str(e["capability"]))[:64]

        caps = await asyncio.to_thread(_load)
        roles: List[str] = []
        try:
            from app.services.gis_harness.workflow_schema import DATA_ROLES

            roles = list(DATA_ROLES)
        except Exception:  # noqa: BLE001
            observe_optional_failure("model-authoring-data-roles")
        return {"success": True, "transform_ops": list(EXECUTABLE_TRANSFORM_OPS),
                "data_roles": roles, "capabilities": caps}

    # ── 2：创建草稿 ──────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_create_draft",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "创建模型草稿：结构化 nodes/edges/params 文档（词表由 capabilities "
            "工具给出）→ 项目内唯一 slug 草稿（CAS revision=1）。只保存声明，"
            "不执行。文档含错误级 finding 也允许创建（草稿=未完成），发布才拒绝。"
            "\n节点词表：kind ∈ data_input|transform|analysis|cartography|output；"
            "data_input 必带 role；analysis 必带 capability；transform 必带 "
            "transform_op ∈ buffer|clip|dissolve|overlay|filter|reproject。"
            "边：data_input 源的 from_port = 角色名；其余源缺省 'out'。"
            "参数引用语法：节点参数值 \"<code>$param:参数名</code>\"。"
        ),
        param_descriptions={
            "project_id": "项目 id（写权限校验）",
            "model_slug": "模型 slug（^[a-z][a-z0-9_]{2,40}$，项目内唯一）",
            "document": "模型文档 JSON（ModelDocument v1）",
        },
        side_effect="state_mutation",
        data_mutations=["project_memory_write"],
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "草稿", "draft"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_model_builder_create_draft(
        project_id: str, model_slug: str, document: str,
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, _sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN",
                        "项目不存在或无写权限")
        try:
            raw = json.loads(document) if isinstance(document, str) else document
        except (TypeError, ValueError) as e:
            return _err("INVALID_ARGS", f"document JSON: {e}")
        import json as _json

        if len(_json.dumps(raw, ensure_ascii=False, default=str)) > _MAX_DOCS_BYTES:
            return _err("DOCUMENT_TOO_LARGE", f"exceeds {_MAX_DOCS_BYTES} bytes")
        try:
            doc = _validate_document_dict(raw)
        except Exception as e:  # noqa: BLE001 — pydantic 摘要有界回显
            return _err("MODEL_SCHEMA_INVALID", str(e)[:300])
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            ModelStoreError,
            get_service,
        )

        svc = get_service()
        try:
            draft = await svc.create_draft(
                project_id=project_id, document=doc,
                model_slug=model_slug, created_by=user_id or "")
        except ModelStoreError as e:
            return _err(e.code, e.detail)
        except ModelAuthoringError as e:
            return _err(e.code, e.detail)
        validation = await svc.validate_model(project_id, model_slug)
        return {"success": True,
                "model_slug": draft["model_slug"],
                "revision_no": draft["revision_no"],
                "document_fingerprint": draft["document_fingerprint"],
                "valid": validation["valid"],
                "findings": validation["findings"][:16]}

    # ── 3：检视草稿 ──────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_inspect",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "检视模型草稿：声明摘要（节点/边/参数清单）、typed findings、"
            "发布指针与版本历史。只读。"
        ),
        param_descriptions={
            "project_id": "项目 id",
            "model_slug": "模型 slug",
        },
        side_effect="read_only",
        data_mutations=[],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "inspect", "检视"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def webgis_model_builder_inspect(
        project_id: str, model_slug: str,
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, _sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无权限")
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            get_service,
        )

        svc = get_service()
        draft = await svc.get_draft(project_id, model_slug)
        if draft is None:
            return _err("DRAFT_NOT_FOUND", model_slug)
        doc = draft.get("document") or {}
        nodes = [
            {"node_id": n.get("node_id"), "kind": n.get("kind"),
             "role": n.get("role", ""), "capability": n.get("capability", ""),
             "transform_op": n.get("transform_op", "")}
            for n in doc.get("nodes", [])[:32]
        ]
        edges = [
            {"from": f"{e.get('from_node')}.{e.get('from_port', 'out')}",
             "to": f"{e.get('to_node')}.{e.get('to_port', 'in')}"}
            for e in doc.get("edges", [])[:64]
        ]
        try:
            validation = await svc.validate_model(project_id, model_slug)
            findings = validation["findings"]
            valid = validation["valid"]
        except ModelAuthoringError as e:
            return _err(e.code, e.detail)
        return {
            "success": True,
            "model_slug": model_slug,
            "name": draft["name"],
            "revision_no": draft["revision_no"],
            "document_fingerprint": draft["document_fingerprint"],
            "published": {"package_id": draft.get("latest_published_package_id") or "",
                          "version": draft.get("latest_published_version") or ""},
            "params": [
                {"name": p.get("name"), "kind": p.get("kind"),
                 "required": p.get("required", True),
                 "default": (p.get("default") or {}).get("value")}
                for p in doc.get("params", [])[:16]
            ],
            "nodes": nodes, "edges": edges,
            "output_node_ids": doc.get("output_node_ids", []),
            "valid": valid, "findings": findings[:16],
        }

    # ── 4：受限图补丁 ────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_patch",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "对草稿应用受限补丁（CAS；无自由代码/SQL/网络）。op 词表："
            "add_node{node}/remove_node{node_id}/add_edge{edge}/"
            "remove_edge{from_node,to_node}/set_param{name,spec}/"
            "remove_param{name}/set_node_param{node_id,key,value}/"
            "remove_node_param{node_id,key}/set_layout{node_id,x,y}/"
            "set_meta{name?,description?}。服务端逐 op 白名单裁决，"
            "任一 op 非法即整体拒绝（不部分应用）。"
        ),
        param_descriptions={
            "project_id": "项目 id",
            "model_slug": "模型 slug",
            "ops": "补丁 op 列表 JSON（≤16 个）",
            "expected_revision": "CAS 期望修订号（来自 inspect）",
        },
        side_effect="state_mutation",
        data_mutations=["project_memory_write"],
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "patch", "编辑"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args",),
    )
    async def webgis_model_builder_patch(
        project_id: str, model_slug: str, ops: str, expected_revision: int,
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, _sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无写权限")
        try:
            op_list = json.loads(ops) if isinstance(ops, str) else ops
        except (TypeError, ValueError) as e:
            return _err("INVALID_ARGS", f"ops JSON: {e}")
        if not isinstance(op_list, list) or not (1 <= len(op_list) <= _MAX_PATCH_OPS):
            return _err("INVALID_ARGS",
                        f"ops must be 1..{_MAX_PATCH_OPS} items")
        from app.schemas.model_authoring_schema import (
            ModelDocument,
            ModelEdge,
            ModelLayoutPos,
            ModelNode,
            ModelParamSpec,
        )
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            ModelStoreError,
            get_service,
        )

        svc = get_service()
        draft = await svc.get_draft(project_id, model_slug)
        if draft is None:
            return _err("DRAFT_NOT_FOUND", model_slug)
        try:
            doc = ModelDocument.model_validate(draft.get("document") or {})
        except Exception as e:  # noqa: BLE001
            return _err("DOCUMENT_CORRUPT", str(e)[:200])
        try:
            doc = _apply_patch(doc, op_list, ModelNode, ModelEdge,
                               ModelParamSpec, ModelLayoutPos)
        except _PatchError as e:
            return _err(e.code, e.detail)
        except Exception as e:  # noqa: BLE001 — pydantic 校验即 op 非法
            return _err("PATCH_OP_INVALID", str(e)[:300])
        try:
            updated = await svc.update_document(
                project_id=project_id, model_slug=model_slug, document=doc,
                expected_revision=expected_revision)
        except ModelStoreError as e:
            return _err(e.code, e.detail)
        except ModelAuthoringError as e:
            return _err(e.code, e.detail)
        findings = await svc.validate_model(project_id, model_slug)
        return {"success": True, "revision_no": updated["revision_no"],
                "document_fingerprint": updated["document_fingerprint"],
                "valid": findings["valid"],
                "findings": findings["findings"][:16]}

    # ── 5：静态验证 ──────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_validate",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "静态验证模型草稿：环/端口兼容/能力可用性/参数约束/数据角色/"
            "输出义务 → typed findings（error 阻断发布）。只读，不执行。"
        ),
        param_descriptions={
            "project_id": "项目 id",
            "model_slug": "模型 slug",
        },
        side_effect="read_only",
        data_mutations=[],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "validate", "验证"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def webgis_model_builder_validate(
        project_id: str, model_slug: str,
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, _sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无权限")
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            get_service,
        )

        try:
            out = await get_service().validate_model(project_id, model_slug)
        except ModelAuthoringError as e:
            return _err(e.code, e.detail)
        return {"success": True, "valid": out["valid"],
                "findings": out["findings"][:24],
                "document_fingerprint": out["document_fingerprint"]}

    # ── 6：发布 ──────────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_publish",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "发布模型为不可变版本化包（umodel_ 前缀；既有 PackageRegistry "
            "draft→published）。error 级 finding 未清零时 typed 拒绝。发布后"
            "历史版本不可被旧草稿改写（immutable）。"
        ),
        param_descriptions={
            "project_id": "项目 id（写权限）",
            "model_slug": "模型 slug",
            "change": "版本推进：major|minor|patch（缺省 minor）",
        },
        side_effect="state_mutation",
        data_mutations=["project_memory_write"],
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "publish", "发布", "技能"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_model_builder_publish(
        project_id: str, model_slug: str, change: str = "minor",
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, _sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无写权限")
        if change not in ("major", "minor", "patch"):
            return _err("INVALID_VERSION_CHANGE",
                        "change must be major|minor|patch")
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            get_service,
        )

        owner = _owner_scope(user_id, session_id)
        org_str = str(org_id) if org_id else None
        try:
            out = await get_service().publish_model(
                project_id=project_id, model_slug=model_slug,
                owner_scope=owner, org_id=org_str, change=change)
        except ModelAuthoringError as e:
            return _err(e.code, e.detail, **(
                {"findings": e.extra.get("findings", [])}
                if e.extra.get("findings") else {}))
        return {"success": True, "package_id": out["package_id"],
                "version": out["version"], "fingerprint": out["fingerprint"]}

    # ── 7：实例化 + 绑定 + 运行 ──────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_instantiate_run",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "运行已发布模型：实例化（包 semver）+ 数据输入绑定（session ref）"
            "+ 参数注入 + 经既有 runtime driver 执行。未发布模型 typed 拒绝；"
            "ref 必须本会话可读；参数覆盖经声明校验。返回实例投影与逐节点"
            "绑定回执（运行态解释用 webgis_workflow_runtime_inspector 类只读"
            "面或 REST events）。"
        ),
        param_descriptions={
            "project_id": "项目 id（写权限；运行归该项目）",
            "model_slug": "已发布模型 slug",
            "input_bindings": "数据绑定 JSON：{data_input node_id: session ref}",
            "param_values": "参数覆盖 JSON：{参数名: 值}（缺省用声明 default）",
            "version": "包版本（空 = 最新发布版）",
            "deadline_s": "运行预算秒（≤300，缺省 60）",
        },
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("模型构建器", "model builder", "run", "运行", "实例化"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "execution_failed"),
    )
    async def webgis_model_builder_instantiate_run(
        project_id: str, model_slug: str,
        input_bindings: Optional[str] = None,
        param_values: Optional[str] = None,
        version: str = "",
        deadline_s: float = 60.0,
        session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, ctx_sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无写权限")
        sess = session_id or ctx_sess or ""
        if not sess:
            return _err("SESSION_REQUIRED",
                        "数据绑定需要会话上下文（session ref 按会话隔离）")

        def _loads(v: Optional[str]) -> Dict[str, Any]:
            if not v:
                return {}
            try:
                out = json.loads(v)
                return out if isinstance(out, dict) else {}
            except (TypeError, ValueError):
                return {}

        bindings = _loads(input_bindings)
        params = _loads(param_values)
        from app.services.model_authoring.service import (
            ModelAuthoringError,
            get_service,
        )

        svc = get_service()
        owner = _owner_scope(user_id, sess)
        org_str = str(org_id) if org_id else None
        try:
            inst = await svc.instantiate_model(
                project_id=project_id, model_slug=model_slug,
                owner_scope=owner, session_id=sess, org_id=org_str,
                version=version, param_values=params,
                input_bindings=bindings)
            run = await svc.run_model_instance(
                instance_id=inst["instance"]["instance_id"],
                owner_scope=owner, deadline_s=deadline_s)
        except ModelAuthoringError as e:
            return _err(e.code, e.detail)
        node_states = [
            {"node_id": n.get("node_id"), "state": n.get("state", ""),
             "error_code": n.get("error_code", "")}
            for n in (inst["instance"].get("nodes") or [])[:64]
        ]
        return {
            "success": True,
            "instance_id": inst["instance"].get("instance_id", ""),
            "status": run.get("run", {}).get("status", ""),
            "package": inst["package"],
            "params_resolved": inst["params_resolved"],
            "bindings": inst["bindings"],
            "nodes": node_states,
            "run_summary": {k: v for k, v in (run.get("run") or {}).items()
                            if k in ("status", "succeeded", "failed",
                                     "cancelled", "deadline_expired", "wall_s")},
        }

    # ── 8：项目目录 ──────────────────────────────────────────────────

    @tool(
        registry, name="webgis_model_builder_list_models",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "项目模型目录：草稿+发布指针+版本清单（受限 scope——本项目与"
            "当前 owner；无全局可见性）。只读。"
        ),
        param_descriptions={
            "project_id": "项目 id",
        },
        side_effect="read_only",
        data_mutations=[],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("模型构建器", "model builder", "catalog", "目录", "技能"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def webgis_model_builder_list_models(
        project_id: str, session_id: Optional[str] = None,
    ) -> dict:
        user_id, org_id, ctx_sess = _caller_identity()
        if not _authorize_project_write(project_id, user_id, org_id):
            return _err("PROJECT_FORBIDDEN", "项目不存在或无权限")
        from app.services.model_authoring.service import get_service

        owner = _owner_scope(user_id, ctx_sess)
        entries = await get_service().list_model_catalog(
            project_id=project_id, owner_scope=owner, limit=50)
        return {"success": True, "models": entries[:50]}


class _PatchError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _apply_patch(doc: Any, op_list: List[Any], ModelNode: Any, ModelEdge: Any,
                 ModelParamSpec: Any, ModelLayoutPos: Any) -> Any:
    """白名单 op 逐个应用到 ModelDocument（纯函数；非法 op typed 拒绝）。"""
    for raw in op_list:
        if not isinstance(raw, dict):
            raise _PatchError("PATCH_OP_INVALID", "op must be object")
        op = str(raw.get("op") or "")
        if op == "add_node":
            node = ModelNode.model_validate(raw.get("node") or {})
            if any(n.node_id == node.node_id for n in doc.nodes):
                raise _PatchError("NODE_EXISTS", node.node_id)
            doc.nodes.append(node)
        elif op == "remove_node":
            nid = str(raw.get("node_id") or "")
            before = len(doc.nodes)
            doc.nodes = [n for n in doc.nodes if n.node_id != nid]
            doc.edges = [e for e in doc.edges
                         if e.from_node != nid and e.to_node != nid]
            doc.output_node_ids = [o for o in doc.output_node_ids if o != nid]
            doc.layout.pop(nid, None)
            if len(doc.nodes) == before:
                raise _PatchError("NODE_NOT_FOUND", nid)
        elif op == "add_edge":
            edge = ModelEdge.model_validate(raw.get("edge") or {})
            ids = {n.node_id for n in doc.nodes}
            if edge.from_node not in ids or edge.to_node not in ids:
                raise _PatchError("EDGE_NODE_UNKNOWN",
                                  f"{edge.from_node}->{edge.to_node}")
            if any(e.from_node == edge.from_node and e.to_node == edge.to_node
                   and e.from_port == edge.from_port
                   and e.to_port == edge.to_port for e in doc.edges):
                raise _PatchError("EDGE_EXISTS", "duplicate edge")
            doc.edges.append(edge)
        elif op == "remove_edge":
            fn, tn = str(raw.get("from_node") or ""), str(raw.get("to_node") or "")
            before = len(doc.edges)
            doc.edges = [e for e in doc.edges
                         if not (e.from_node == fn and e.to_node == tn)]
            if len(doc.edges) == before:
                raise _PatchError("EDGE_NOT_FOUND", f"{fn}->{tn}")
        elif op == "set_param":
            spec = ModelParamSpec.model_validate(raw.get("spec") or {})
            doc.params = [p for p in doc.params if p.name != spec.name]
            doc.params.append(spec)
            if len(doc.params) > 16:
                raise _PatchError("TOO_MANY_PARAMS", "params exceed 16")
        elif op == "remove_param":
            name = str(raw.get("name") or "")
            doc.params = [p for p in doc.params if p.name != name]
        elif op == "set_node_param":
            nid, key = str(raw.get("node_id") or ""), str(raw.get("key") or "")
            node = next((n for n in doc.nodes if n.node_id == nid), None)
            if node is None:
                raise _PatchError("NODE_NOT_FOUND", nid)
            from app.schemas.model_authoring_schema import BoundedValue

            node.params.values[key] = BoundedValue(value=raw.get("value"))
        elif op == "remove_node_param":
            nid, key = str(raw.get("node_id") or ""), str(raw.get("key") or "")
            node = next((n for n in doc.nodes if n.node_id == nid), None)
            if node is not None:
                node.params.values.pop(key, None)
        elif op == "set_layout":
            nid = str(raw.get("node_id") or "")
            if not any(n.node_id == nid for n in doc.nodes):
                raise _PatchError("NODE_NOT_FOUND", nid)
            doc.layout[nid] = ModelLayoutPos(
                x=float(raw.get("x") or 0), y=float(raw.get("y") or 0))
        elif op == "set_meta":
            if raw.get("name") is not None:
                doc.name = str(raw["name"])[:120]
            if raw.get("description") is not None:
                doc.description = str(raw["description"])[:2000]
        else:
            raise _PatchError("PATCH_OP_UNKNOWN", op or "(empty)")
    from app.schemas.model_authoring_schema import ModelDocument

    return ModelDocument.model_validate(doc.model_dump(mode="json"))


__all__ = ["register_model_authoring_tools"]
