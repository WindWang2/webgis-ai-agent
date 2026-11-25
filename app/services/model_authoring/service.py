"""模型作者域服务门面（REST 与 Pi 工具的唯一入口）。

组合既有权威（零重写）：
- 草稿：ModelDraftStore（CAS）；
- 语义：validate_document / compile_document（纯函数）；
- 发布：PackageRegistry（draft→published→deprecated，owner/org 隔离）；
- 实例化：WorkflowRuntimeService.instantiate + ModelInstanceBinder +
  run_instance(node_params=...)（既有 driver/绑定门/复用/重算）。

模型只保存声明/版本/ref：发布不复制包内容（指针），实例化不复制文档
（包已指纹化），运行态全部读既有实例投影。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.schemas.model_authoring_schema import ModelDocument, ModelParamSpec
from app.services.model_authoring import contracts as MA
from app.services.model_authoring.store import (
    ModelDraftStore,
)

logger = logging.getLogger(__name__)

#: 实例化/运行参数值的有界约束（与 BoundedValue 纪律一致）。
_MAX_PARAM_VALUES = 16


class ModelAuthoringError(Exception):
    """模型域 typed 错误（code 稳定词表；HTTP/Pi 面直接映射）。"""

    def __init__(self, code: str, detail: str = "", extra: Optional[Dict[str, Any]] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.extra = extra or {}


def _require_document(draft: Dict[str, Any]) -> ModelDocument:
    try:
        return ModelDocument.model_validate(draft.get("document") or {})
    except Exception as e:  # noqa: BLE001 — 行内文档损坏按 typed 错误面
        raise ModelAuthoringError(
            "DOCUMENT_CORRUPT", str(e)[:300]) from e


def validate_param_values(
    params: List[ModelParamSpec], values: Dict[str, Any],
) -> Dict[str, Any]:
    """参数值裁决（user 提供值 ∪ default；required 缺失 typed 拒绝）。

    返回 name → resolved value。纪律与 runtime 参数解析同形（user >
    default），非法值**拒绝而非静默回落**（作者面不同于规划面 hint）。
    """
    if len(values) > _MAX_PARAM_VALUES:
        raise ModelAuthoringError(
            "TOO_MANY_PARAMS", f"{len(values)} exceeds {_MAX_PARAM_VALUES}")
    known = {p.name: p for p in params}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ModelAuthoringError(
            "UNKNOWN_PARAM", ",".join(unknown[:8]))
    resolved: Dict[str, Any] = {}
    missing: List[str] = []
    for p in params:
        raw = values.get(p.name, p.default.value if p.default else None)
        if raw is None:
            if p.required:
                missing.append(p.name)
            continue
        resolved[p.name] = _check_value(p, raw)
    if missing:
        raise ModelAuthoringError(
            "REQUIRED_PARAM_MISSING", ",".join(missing[:8]))
    return resolved


def _check_value(p: ModelParamSpec, raw: Any) -> Any:
    if p.kind == "number":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH", f"{p.name} expects number")
        v = float(raw)
        if p.minimum is not None and v < p.minimum:
            raise ModelAuthoringError(
                "PARAM_OUT_OF_RANGE", f"{p.name} < minimum {p.minimum}")
        if p.maximum is not None and v > p.maximum:
            raise ModelAuthoringError(
                "PARAM_OUT_OF_RANGE", f"{p.name} > maximum {p.maximum}")
        return v
    if p.kind == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH", f"{p.name} expects integer")
        if p.minimum is not None and raw < p.minimum:
            raise ModelAuthoringError(
                "PARAM_OUT_OF_RANGE", f"{p.name} < minimum {p.minimum}")
        if p.maximum is not None and raw > p.maximum:
            raise ModelAuthoringError(
                "PARAM_OUT_OF_RANGE", f"{p.name} > maximum {p.maximum}")
        return raw
    if p.kind == "boolean":
        if not isinstance(raw, bool):
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH", f"{p.name} expects boolean")
        return raw
    if p.kind == "enum":
        if raw not in p.choices:
            raise ModelAuthoringError(
                "PARAM_NOT_IN_CHOICES", f"{p.name} not in choices")
        return str(raw)
    if p.kind == "time_window":
        import re

        s = str(raw)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", s):
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH",
                f"{p.name} expects ISO interval 'YYYY-MM-DD/YYYY-MM-DD'")
        return s
    if p.kind == "field":
        s = str(raw)
        if not s or len(s) > 64:
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH", f"{p.name} expects field name ≤64")
        return s
    if p.kind == "unit":
        s = str(raw)
        if len(s) > 24:
            raise ModelAuthoringError(
                "PARAM_TYPE_MISMATCH", f"{p.name} expects unit ≤24 chars")
        return s
    # string
    s = str(raw)
    if len(s) > 256:
        raise ModelAuthoringError(
            "PARAM_TYPE_MISMATCH", f"{p.name} exceeds 256 chars")
    return s


def resolve_node_params(
    doc: ModelDocument, values: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """文档节点参数 + `$param:` 引用 → {wire node_id: effective params}。

    语义与 driver 的合并方向一致（node.params ∪ 运行时覆盖）：键是
    **wire node_id**（transform 节点 = ``transform:<op>:<slug>``，与
    compile 的 wire 命名同规则）—— driver 以 wire id 查 ``node_params``。
    """
    resolved = validate_param_values(doc.params, values)
    out: Dict[str, Dict[str, Any]] = {}
    for node in doc.nodes:
        effective: Dict[str, Any] = {}
        for key, bv in node.params.values.items():
            v = bv.value
            if isinstance(v, str) and v.startswith("$param:"):
                pname = v[len("$param:"):]
                if pname in resolved:
                    effective[key] = resolved[pname]
                # 未解析（可选参数无值）→ 键省略（诚实缺省）
            else:
                effective[key] = v
        if effective:
            out[wire_node_id(node)] = effective
    return out


def wire_node_id(node: Any) -> str:
    """作者节点 → 运行时 wire node_id（与 compile._wire_node_id 同规则）。"""
    if getattr(node, "kind", "") == "transform":
        op = str(getattr(node, "transform_op", "") or "")
        return f"transform:{op}:{node.node_id}"[:64]
    return node.node_id[:64]


class ModelAuthoringService:
    """门面：REST 路由与 Pi 工具共享（同一服务同链路）。"""

    def __init__(
        self,
        *,
        store: Optional[ModelDraftStore] = None,
        runtime: Optional[Any] = None,
        registry: Optional[Any] = None,
    ):
        self._store = store
        self._runtime = runtime
        self._registry = registry

    # ── 惰性依赖（与 workflow_runtime.service 同纪律）────────────────

    @property
    def store(self) -> ModelDraftStore:
        if self._store is None:
            self._store = ModelDraftStore()
        return self._store

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            from app.services.workflow_runtime.service import (
                WorkflowRuntimeService,
            )

            self._runtime = WorkflowRuntimeService()
        return self._runtime

    @property
    def registry(self) -> Any:
        if self._registry is None:
            self._registry = self.runtime.registry
        return self._registry

    # ── 草稿面 ────────────────────────────────────────────────────────

    async def create_draft(
        self, *, project_id: str, document: ModelDocument, model_slug: str,
        created_by: str = "",
    ) -> Dict[str, Any]:
        if len(document.nodes) == 0:
            raise ModelAuthoringError(
                "MODEL_EMPTY", "draft must contain at least one node")
        dump = document.model_dump(mode="json")
        # 草稿允许携带 error 级 finding（草稿=未完成）；发布/实例化才拒绝。
        return await asyncio.to_thread(
            self.store.create_draft,
            project_id=project_id,
            model_slug=model_slug,
            document=dump,
            document_fingerprint=document.document_fingerprint(),
            name=document.name,
            description=document.description,
            source_kind=document.source.kind,
            source_ref=document.source.ref,
            template_id=document.template_id,
            created_by=created_by,
        )

    async def get_draft(self, project_id: str, model_slug: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.store.get_draft, project_id, model_slug)

    async def list_drafts(
        self, project_id: str, *, limit: int = 50, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_drafts, project_id, limit=limit, offset=offset)

    async def update_document(
        self, *, project_id: str, model_slug: str, document: ModelDocument,
        expected_revision: int, name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        dump = document.model_dump(mode="json")
        return await asyncio.to_thread(
            self.store.update_document,
            project_id=project_id,
            model_slug=model_slug,
            document=dump,
            document_fingerprint=document.document_fingerprint(),
            name=name if name is not None else _draft_name(document),
            description=description,
            expected_revision=expected_revision,
        )

    async def delete_draft(
        self, *, project_id: str, model_slug: str, expected_revision: int,
    ) -> bool:
        return await asyncio.to_thread(
            self.store.delete_draft,
            project_id=project_id, model_slug=model_slug,
            expected_revision=expected_revision)

    async def validate_model(
        self, project_id: str, model_slug: str,
    ) -> Dict[str, Any]:
        draft = await self.get_draft(project_id, model_slug)
        if draft is None:
            raise ModelAuthoringError("DRAFT_NOT_FOUND", model_slug)
        doc = _require_document(draft)
        findings = await asyncio.to_thread(_run_validate, doc)
        errors = [f for f in findings if f.severity == MA.SEVERITY_ERROR]
        return {
            "model_slug": model_slug,
            "valid": not errors,
            "document_fingerprint": draft["document_fingerprint"],
            "revision_no": draft["revision_no"],
            "findings": [f.to_bounded() for f in findings],
        }

    # ── 发布面（既有 PackageRegistry；模型=受限声明式项目 scope）──────

    async def publish_model(
        self, *, project_id: str, model_slug: str, owner_scope: str,
        org_id: Optional[str] = None, change: str = "minor",
    ) -> Dict[str, Any]:
        draft = await self.get_draft(project_id, model_slug)
        if draft is None:
            raise ModelAuthoringError("DRAFT_NOT_FOUND", model_slug)
        doc = _require_document(draft)
        # 版本先于编译裁决（包是编译输入：同文档不同版本 → 不同包行）
        pkg_id = f"umodel_{model_slug}"[:64]
        versions = await asyncio.to_thread(
            self.registry.list_versions, pkg_id, owner_scope=owner_scope)
        published = [v for v in versions if v.get("status") == "published"]
        from app.services.gis_harness.workflow_v4.package import next_version

        prev = published[-1]["version"] if published else None
        try:
            version = next_version(prev, change=change)
        except ValueError as e:
            raise ModelAuthoringError("INVALID_VERSION_CHANGE", str(e)) from e
        from app.services.model_authoring.compile import compile_document

        compiled, findings = await asyncio.to_thread(
            compile_document, doc, model_slug=model_slug, version=version)
        errors = [f for f in findings if f.severity == MA.SEVERITY_ERROR]
        if compiled is None or errors:
            raise ModelAuthoringError(
                "MODEL_INVALID",
                "validation errors block publish",
                {"findings": [f.to_bounded() for f in findings[:16]]})
        try:
            row = await asyncio.to_thread(
                self.registry.register, compiled.package,
                owner_scope=owner_scope, project_id=project_id,
                org_id=org_id)
        except Exception as e:  # noqa: BLE001 — PackageConflict 等归一 typed
            raise ModelAuthoringError(
                "PACKAGE_CONFLICT", str(e)[:200]) from e
        pub = await asyncio.to_thread(
            self.registry.publish, row["package_id"], row["version"],
            owner_scope=owner_scope)
        if pub is None:
            raise ModelAuthoringError(
                "PUBLISH_FAILED",
                f"package {row['package_id']}@{row['version']} not found")
        updated = await asyncio.to_thread(
            self.store.record_publication,
            project_id=project_id, model_slug=model_slug,
            package_id=row["package_id"], version=row["version"],
            expected_revision=int(draft["revision_no"]))
        return {
            "model_slug": model_slug,
            "package_id": row["package_id"],
            "version": row["version"],
            "fingerprint": row["fingerprint"],
            "next_draft_revision": updated["revision_no"],
            "findings": [f.to_bounded() for f in findings],
        }

    # ── 实例化 / 运行（既有 runtime adapter）──────────────────────────

    async def instantiate_model(
        self, *, project_id: str, model_slug: str, owner_scope: str,
        session_id: str, org_id: Optional[str] = None, version: str = "",
        param_values: Optional[Dict[str, Any]] = None,
        input_bindings: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        draft = await self.get_draft(project_id, model_slug)
        if draft is None:
            raise ModelAuthoringError("DRAFT_NOT_FOUND", model_slug)
        doc = _require_document(draft)
        package_id = draft.get("latest_published_package_id") or ""
        if version or not package_id:
            pkg_id = f"umodel_{model_slug}"[:64]
        else:
            pkg_id = package_id
        # 发布存在性（require_published；未发布 → typed 拒绝）
        row = await asyncio.to_thread(
            self.registry.resolve, pkg_id, owner_scope=owner_scope,
            version=version, require_published=True)
        if row is None:
            raise ModelAuthoringError(
                "MODEL_NOT_PUBLISHED",
                f"{model_slug} has no published version"
                + (f" {version}" if version else ""))
        param_values = param_values or {}
        resolved = await asyncio.to_thread(
            validate_param_values, doc.params, param_values)
        node_params = await asyncio.to_thread(resolve_node_params, doc, resolved)
        from app.services.workflow_runtime.registry import PackageConflict  # noqa: F401

        inst = await asyncio.to_thread(
            self.runtime.instantiate, row["package_id"],
            owner_scope=owner_scope, session_id=session_id,
            version=row["version"], project_id=project_id, org_id=org_id)
        inst_id = inst["instance_id"]
        bindings: Dict[str, Dict[str, Any]] = {}
        if input_bindings:
            from app.services.model_authoring.bindings import (
                ModelInstanceBinder,
            )

            binder = ModelInstanceBinder(self.runtime)
            bindings = await binder.bind_inputs(
                inst_id, owner_scope=owner_scope, session_id=session_id,
                bindings=dict(input_bindings))
        nodes = await asyncio.to_thread(self.runtime.store.get_nodes, inst_id)
        from app.services.workflow_runtime import projection as PR

        projection = await asyncio.to_thread(
            PR.instance_projection, inst, nodes)
        return {
            "model_slug": model_slug,
            "package": {"package_id": row["package_id"],
                        "version": row["version"],
                        "fingerprint": row["fingerprint"]},
            "params_resolved": resolved,
            "node_params": node_params,
            "bindings": bindings,
            "instance": projection,
        }

    async def run_model_instance(
        self, *, instance_id: str, owner_scope: str,
        param_values: Optional[Dict[str, Any]] = None,
        project_id: str = "", deadline_s: float = 60.0,
    ) -> Dict[str, Any]:
        """运行已实例化的模型（参数覆盖经 node_params 注入既有 driver）。"""
        inst = await asyncio.to_thread(
            self.runtime.store.get_instance, instance_id, owner_scope)
        if inst is None:
            raise ModelAuthoringError("INSTANCE_NOT_FOUND", instance_id)
        node_params: Dict[str, Dict[str, Any]] = {}
        if param_values is not None and project_id:
            draft_pkg_id = str(inst.get("package_id") or "")
            slug = draft_pkg_id[len("umodel_"):] if draft_pkg_id.startswith("umodel_") else ""
            if slug:
                draft = await self.get_draft(project_id, slug)
                if draft is not None:
                    doc = _require_document(draft)
                    node_params = await asyncio.to_thread(
                        resolve_node_params, doc, param_values)
        result = await self.runtime.run_instance(
            instance_id, owner_scope=owner_scope,
            deadline_s=max(1.0, min(float(deadline_s), 300.0)),
            node_params=node_params or None)
        # runtime.run_instance 返回 RunResult{"run": RunSummary, "instance"}；
        # 解包为扁平 summary（避免双层嵌套——调用方读 run["run"]["status"]）。
        summary = result.get("run") if isinstance(result, dict) else None
        return {"run": summary if isinstance(summary, dict) else {},
                "instance": result.get("instance") if isinstance(result, dict) else None,
                "node_params": node_params}

    # ── 目录 / 版本（受限 scope 检索：本项目 + 本 owner）──────────────

    async def list_model_catalog(
        self, *, project_id: str, owner_scope: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        drafts = await asyncio.to_thread(
            self.store.list_drafts, project_id, limit=limit)
        out: List[Dict[str, Any]] = []
        for d in drafts:
            pkg_id = d.get("latest_published_package_id") or ""
            entry: Dict[str, Any] = {
                "model_slug": d["model_slug"],
                "name": d["name"],
                "description": d.get("description") or "",
                "document_fingerprint": d["document_fingerprint"],
                "published": bool(pkg_id),
                "latest_published_version": d.get("latest_published_version") or "",
                "template_id": d.get("template_id") or "",
                "updated_at": d.get("updated_at") or "",
            }
            if pkg_id:
                versions = await asyncio.to_thread(
                    self.registry.list_versions, pkg_id, owner_scope=owner_scope)
                entry["versions"] = [
                    {"version": v["version"], "status": v.get("status") or "",
                     "fingerprint": v.get("fingerprint") or ""}
                    for v in versions[:8]
                ]
            out.append(entry)
        return out

    # ── 导入 / 导出（小型模型文档；导入只建草稿，绝不自动执行）────────

    async def export_model(
        self, project_id: str, model_slug: str,
    ) -> Dict[str, Any]:
        draft = await self.get_draft(project_id, model_slug)
        if draft is None:
            raise ModelAuthoringError("DRAFT_NOT_FOUND", model_slug)
        doc = _require_document(draft)
        from app.services.model_authoring.export import export_document

        return await asyncio.to_thread(export_document, doc)

    async def import_model(
        self, *, project_id: str, payload: Dict[str, Any],
        model_slug: str, created_by: str = "",
    ) -> Dict[str, Any]:
        from app.services.model_authoring.export import parse_import_document

        doc = await asyncio.to_thread(parse_import_document, payload)
        dump = doc.model_dump(mode="json")
        return await asyncio.to_thread(
            self.store.create_draft,
            project_id=project_id,
            model_slug=model_slug,
            document=dump,
            document_fingerprint=doc.document_fingerprint(),
            name=doc.name,
            description=doc.description,
            source_kind="manual",
            source_ref="import",
            template_id=doc.template_id,
            created_by=created_by,
        )


def _draft_name(document: ModelDocument) -> str:
    return document.name


def _run_validate(doc: ModelDocument):
    from app.services.model_authoring.validate import validate_document

    return validate_document(doc)


__all__ = [
    "ModelAuthoringService",
    "ModelAuthoringError",
    "validate_param_values",
    "resolve_node_params",
    "get_service",
    "reset_service",
]

# ── 进程单例（与 workflow_runtime.service 同纪律；测试可 reset 注入）──

_service: Optional[ModelAuthoringService] = None


def get_service() -> ModelAuthoringService:
    global _service
    if _service is None:
        _service = ModelAuthoringService()
    return _service


def reset_service() -> None:
    global _service
    _service = None
