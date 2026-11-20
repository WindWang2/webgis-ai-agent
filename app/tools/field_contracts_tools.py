"""G44 Agent 工具面：业务字段合同的 inspect / draft / assess / result。

信任边界（WP10/F28）：
- inspect/observe：纯读（观测画像 × 声明对照）；
- draft/upsert：创建/修订 **草稿** 行（DB 写、不激活、不改 edit policy）——
  激活/退役是 owner 显式动作，仅 REST 提供；
- assess：启动完整评估 durable job（幂等；取消/崩溃 → incomplete 收据）；
- result：评估状态 + 违规分页引用（不把全量违规塞进上下文）。
要素编辑本身走既有 vector_feature_edit_preview/commit（同一 central validator，
模型读到的约束与提交使用的 revision 对齐）。tier ≤2；回执有界。
"""

from typing import Any, Dict, Optional

from app.tools.registry import ToolRegistry, tool

_MAX_FIELDS = 64
_MAX_CROSS_RULES = 64


def register_field_contracts_tools(registry: ToolRegistry) -> None:
    """注册业务字段合同工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_field_contract_inspect",
        side_effect="cacheable_read",
        network=False,
        deterministic=False,
        tags=["字段合同", "field contract", "数据质量", "业务规则"],
        latency_class="fast",
        memory_class="light",
        description=(
            "查看数据集（ref:…）的业务字段合同状态：active 合同（字段/类型/必填/"
            "取值域/唯一性/条件规则，含 revision/digest 与激活证据）或当前草稿；"
            "附带观测字段画像（类型面 + observed vocabulary，仅证据不作业务域）。"
            "binding 状态 ok/stale 显式披露。模型据此提出合规属性编辑。"
        ),
        param_descriptions={
            "ref_id": "数据集 ref（ref:…）",
            "session_id": "会话 id（运行时注入，无需传）",
        },
    )
    async def webgis_field_contract_inspect(
        ref_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        from app.services.field_contracts import service as fc_service
        from app.services.field_contracts.enforcement import contract_binding_status
        from app.services.field_contracts.service import FieldContractError

        try:
            observe = await fc_service.observe_fields(session_id, ref_id)
        except FieldContractError as exc:
            return {"success": False, "code": exc.code.upper(), "message": str(exc)[:200]}
        binding = await contract_binding_status(session_id, ref_id)
        contracts = await fc_service.list_contracts(session_id, ref_id)
        active = next((c for c in contracts if c.get("status") == "active"), None)
        draft = next(
            (c for c in contracts if c.get("status") == "draft"), None
        )
        return {
            "success": True,
            "ref_id": ref_id,
            "binding_status": binding.get("status"),
            "active_contract": (
                {
                    "contract_id": active["contract_id"],
                    "revision": active.get("revision"),
                    "digest": (active.get("normalized_digest") or "")[:16],
                    "title": active.get("title"),
                    "rules": active.get("rules"),
                    "activation": active.get("activation"),
                }
                if active
                else None
            ),
            "draft_contract": (
                {
                    "contract_id": draft["contract_id"],
                    "revision": draft.get("revision"),
                    "rules": draft.get("rules"),
                }
                if draft
                else None
            ),
            "observed_fields": {
                k: v for k, v in list((observe.get("observed") or {}).items())[:32]
            },
            "declared_note": observe.get("note"),
            "feature_count": observe.get("feature_count"),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_field_contract_draft",
        side_effect="state_mutation",
        network=False,
        data_mutations=["session_state"],
        deterministic=False,
        tags=["字段合同", "field contract", "草稿", "业务规则"],
        latency_class="medium",
        memory_class="light",
        description=(
            "创建或修订数据集（ref:…）的业务字段合同**草稿**（不激活、不改数据、"
            "不改 edit policy）。规则词表：字段类型 string/number/integer/boolean/"
            "date/datetime + required/nullable/empty_string + enum(≤256)/区间(闭/开)/"
            "长度；跨字段 conditional_required/compare/unique(1-4 字段，null 政策 "
            "ignore|participate)。激活需要 owner 在界面完成完整评估（零违反）后显式"
            "执行 —— 本工具与 Pi 均不能激活。"
        ),
        param_descriptions={
            "ref_id": "数据集 ref（ref:…）",
            "document": (
                "合同文档 {version:1, title, fields:[{field,type,required,...}], "
                "cross_rules:[{rule_id,kind,...}]}（≤64 字段 / ≤64 跨字段规则）"
            ),
            "contract_id": "修订既有草稿时传（连同 expected_revision）；新建省略",
            "expected_revision": "修订草稿的 CAS 修订号（新建省略）",
            "session_id": "会话 id（运行时注入，无需传）",
        },
    )
    async def webgis_field_contract_draft(
        ref_id: str,
        document: Dict[str, Any],
        contract_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        from app.services.field_contracts import service as fc_service
        from app.services.field_contracts.service import FieldContractError

        if not isinstance(document, dict):
            return {"success": False, "code": "INVALID_ARGS", "message": "document must be an object"}
        fields = document.get("fields")
        if not isinstance(fields, list) or not fields or len(fields) > _MAX_FIELDS:
            return {"success": False, "code": "INVALID_ARGS",
                    "message": f"document.fields must be 1-{_MAX_FIELDS} field rules"}
        cross = document.get("cross_rules") or []
        if not isinstance(cross, list) or len(cross) > _MAX_CROSS_RULES:
            return {"success": False, "code": "INVALID_ARGS",
                    "message": f"document.cross_rules must be a list of at most {_MAX_CROSS_RULES}"}
        try:
            if contract_id:
                if not isinstance(expected_revision, int):
                    return {"success": False, "code": "INVALID_ARGS",
                            "message": "expected_revision is required when revising a draft"}
                result = await fc_service.revise_contract(
                    session_id, contract_id,
                    expected_revision=expected_revision,
                    document=document, actor="agent",
                )
            else:
                result = await fc_service.create_contract(
                    session_id, ref_id=ref_id, document=document, actor="agent",
                )
        except FieldContractError as exc:
            return {
                "success": False,
                "code": exc.code.upper(),
                "message": str(exc)[:200],
                "detail": exc.detail,
            }
        return {
            "success": True,
            "contract_id": result["contract_id"],
            "revision": result.get("revision"),
            "status": result.get("status"),
            "digest": (result.get("normalized_digest") or "")[:16],
            "message": (
                "draft saved (not active); owner must run a full assessment with zero "
                "violations and activate explicitly — this tool cannot activate"
            ),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_field_contract_assess",
        side_effect="state_mutation",
        network=False,
        data_mutations=["session_state"],
        deterministic=False,
        tags=["字段合同", "field contract", "完整评估", "数据质量"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "对合同（contract_id）启动完整数据版本评估（durable job，幂等：合同"
            " revision × 数据指纹 × 规则版本）。逐行规则 + 组合唯一性（受控哈希）；"
            "收据含总数/检查数/违反数/完整性；违规分页经 webgis_field_contract_result。"
            "取消/崩溃 → incomplete，绝不产生可激活证明。"
        ),
        param_descriptions={
            "contract_id": "合同 id（草稿或 active）",
            "session_id": "会话 id（运行时注入，无需传）",
        },
    )
    async def webgis_field_contract_assess(
        contract_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        from app.services.field_contracts import service as fc_service
        from app.services.field_contracts.service import FieldContractError

        try:
            result = await fc_service.start_assessment(
                session_id, contract_id, actor="agent", background=True
            )
        except FieldContractError as exc:
            return {
                "success": False,
                "code": exc.code.upper(),
                "message": str(exc)[:200],
                "detail": exc.detail,
            }
        assessment = result.get("assessment") or {}
        return {
            "success": True,
            "assessment_id": assessment.get("id"),
            "status": assessment.get("status"),
            "job_id": result.get("job_id"),
            "poll": result.get("poll"),
            "message": "assessment queued; poll webgis_field_contract_result with assessment_id",
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_field_contract_result",
        side_effect="cacheable_read",
        network=False,
        deterministic=False,
        tags=["字段合同", "field contract", "评估结果", "违规"],
        latency_class="fast",
        memory_class="light",
        description=(
            "读评估收据（assessment_id）：状态（completed 才可作激活证据）、verified"
            " 数据指纹/规则 digest、违反计数（按 code/rule），以及违规分页（cursor，"
            "≤100/页；每条带 _wfid 或 source row 定位）。只分页展示，总数与后续页"
            "可追溯；overflow 只计数。"
        ),
        param_descriptions={
            "assessment_id": "评估收据 id",
            "cursor": "分页游标（首页省略）",
            "limit": "页大小（≤100，默认 20）",
            "session_id": "会话 id（运行时注入，无需传）",
        },
    )
    async def webgis_field_contract_result(
        assessment_id: str,
        cursor: Optional[str] = None,
        limit: int = 20,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        from app.services.field_contracts import service as fc_service
        from app.services.field_contracts.service import FieldContractError

        try:
            assessment = await fc_service.get_assessment(session_id, assessment_id)
            page = await fc_service.assessment_findings_page(
                session_id, assessment_id,
                cursor=cursor, limit=max(1, min(int(limit), 100)),
            )
        except FieldContractError as exc:
            return {"success": False, "code": exc.code.upper(), "message": str(exc)[:200]}
        counts = assessment.get("counts") or {}
        return {
            "success": True,
            "assessment_id": assessment_id,
            "status": assessment.get("status"),
            "verified": assessment.get("verified"),
            "counts": {
                k: counts.get(k)
                for k in (
                    "total_features", "checked_features", "violations_total",
                    "overflow_count", "complete", "unique_conflict_groups",
                )
                if k in counts
            },
            "counts_by_code": counts.get("counts_by_code"),
            "findings": page.get("findings"),
            "filtered_total": page.get("filtered_total"),
            "total_violations": page.get("total_violations"),
            "next_cursor": page.get("next_cursor"),
            "overflow_note": page.get("overflow_note"),
        }
