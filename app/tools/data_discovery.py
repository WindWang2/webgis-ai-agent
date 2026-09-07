"""数据发现工具 —— Agent 的结构化数据目录面（§十八；V3 data foundation）。

设计约束：
- **ref + profile + summary，绝不是数据本体**（§三十二 large-data
  policy）：所有工具只返回有界摘要（catalog summaries / profile
  summary / quality summary / lineage view summary），硬上限由各契约
  自带；
- 会话归属走 ``_resolve_session_id`` 运行时上下文校验（与
  upload_tools 同一防注入纪律）—— LLM 提供的 session_id 不可信；
- 只读：本模块不生产/修改任何产物；
- 任何后端故障返回诚实错误 dict（success/False + reason），绝不静默
  返回空结果冒充「没有数据」。
"""
import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)


def register_data_discovery_tools(registry: ToolRegistry) -> None:
    """注册数据发现工具集。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="list_datasets",
        description=(
            "列出当前会话可用的全部数据资产（分析产物、上传文件、远程数据集），"
            "返回类型/角色/状态/范围/要素数的紧凑清单。✅ 用于：分析前了解手头有哪些数据。"
        ),
    )
    async def list_datasets(session_id: Optional[str] = None) -> dict:
        from app.services.data_catalog.catalog import CatalogFilter, get_data_catalog

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": True, "datasets": [], "count": 0,
                    "message": "当前会话没有数据资产。可先上传数据或执行查询/分析。"}
        result = await get_data_catalog().search(
            session_id=sid, filter_=CatalogFilter(), limit=50
        )
        return {
            "success": True,
            "datasets": result.summaries(max_entries=50),
            "count": result.total_matched,
            "truncated": result.truncated,
            "sources": [s.model_dump() for s in result.sources_queried],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="search_datasets",
        description=(
            "按条件搜索数据资产：关键词/类型（vector、raster、table、chart_data…）/"
            "角色（source、observation、boundary、result…）/CRS/字段名/标签。"
            "✅ 用于：数据较多时精确定位要用的数据。"
        ),
        args_model=_build_search_args_model(),
    )
    async def search_datasets(
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        from app.services.data_catalog.catalog import CatalogFilter, get_data_catalog

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": True, "datasets": [], "count": 0,
                    "message": "当前会话没有数据资产。"}
        try:
            flt = CatalogFilter(**{k: v for k, v in kwargs.items() if v})
        except Exception as e:  # noqa: BLE001 — 非法过滤值走校验错误路径
            return {"success": False, "code": "INVALID_FILTER", "error": str(e)}
        result = await get_data_catalog().search(session_id=sid, filter_=flt, limit=20)
        return {
            "success": True,
            "datasets": result.summaries(max_entries=20),
            "count": result.total_matched,
            "truncated": result.truncated,
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="profile_dataset",
        description=(
            "剖析一个数据集：行数/几何类型/范围/字段类型与统计（min/max/均值）/缺失率/"
            "时间字段/坐标问题，并给出质量诊断（missing CRS、null-heavy 字段、出界坐标等）。"
            "返回有界画像，不返回数据本体。✅ 用于：分析前确认数据是否满足条件。"
        ),
        param_descriptions={
            "ref_id": "数据引用（list_datasets 返回的 id，形如 ref:geojson-…）",
            "deep": "是否深扫（默认 false 用轻量描述符；true 才有均值/唯一值统计）",
            "propose_repairs": "是否附修复提案（plan-only：只映射诊断码 → 修复操作词表，绝不执行）",
        },
    )
    async def profile_dataset(
        ref_id: str,
        deep: bool = False,
        propose_repairs: bool = False,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.lib.data.quality import run_quality_checks
        from app.services.data_profile.profiler import get_dataset_profiler

        sid = _resolve_session_id(session_id)
        if not sid or not ref_id:
            return {"success": False, "code": "NOT_FOUND",
                    "error": "缺少有效的会话或数据引用"}
        profiler = get_dataset_profiler()
        profile = await profiler.profile_session_ref(sid, ref_id, deep=bool(deep))
        if profile is None:
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"未找到数据引用 {ref_id}（可能已过期）"}
        report = run_quality_checks(profile)
        # §三十二 large-data policy：按要素数估档，给出允许的访问形态
        from app.lib.data.large_data import access_policy, classify_features

        size_class = classify_features(profile.vector.row_count if profile.vector else None)
        out = {
            "success": True,
            "profile": profile.summary(),
            "quality": report.summary(),
            "size_class": size_class.value,
            "access_policy": access_policy(size_class).value,
        }
        if propose_repairs:
            # Wave-4 缝（审计 R5）：质量诊断 → 修复提案（plan-only，不执行）
            from app.services.data_ingest.repair_planning import propose_repairs

            out["repair_proposals"] = [
                p.to_bounded_dict() for p in propose_repairs(report)
            ]
        return out

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="describe_artifact",
        description=(
            "描述一个分析产物：V3 契约视图（类型/角色/生命周期/持久层/指纹/血缘父母/"
            "生产者/诊断）。✅ 用于：确认某个产物是什么、是否过期、能否复用。"
        ),
        param_descriptions={
            "ref_id": "产物 id（list_datasets / 上次分析结果返回的 ref）",
        },
    )
    async def describe_artifact(
        ref_id: str,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_lifecycle.service import get_lifecycle_service

        sid = _resolve_session_id(session_id)
        if not sid or not ref_id:
            return {"success": False, "code": "NOT_FOUND",
                    "error": "缺少有效的会话或产物 id"}
        contract = await get_lifecycle_service().get_contract(sid, ref_id)
        if contract is None:
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"账本中不存在产物 {ref_id}"}
        return {
            "success": True,
            "artifact": contract.summary(),
            "quality_hint": contract.quality_status_hint(),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="find_artifacts_by_role",
        description=(
            "按逻辑角色查找数据资产：source（来源）/ observation（观测）/"
            "boundary（边界）/ mask（掩膜）/ intermediate（中间）/ result（结果）/"
            "export（导出）等。✅ 用于：工作流按角色取数。"
        ),
        param_descriptions={"role": "角色名（source/observation/boundary/…）"},
    )
    async def find_artifacts_by_role(
        role: str,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_catalog.catalog import CatalogFilter, get_data_catalog

        sid = _resolve_session_id(session_id)
        if not sid or not role:
            return {"success": True, "datasets": [], "count": 0}
        result = await get_data_catalog().search(
            session_id=sid, filter_=CatalogFilter(role=str(role)), limit=20
        )
        return {
            "success": True,
            "datasets": result.summaries(max_entries=20),
            "count": result.total_matched,
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="get_lineage",
        description=(
            "查询数据血缘：某个产物的上游来源、下游依赖与替换链（谁生成了它、"
            "它被谁使用、它替换了谁）。✅ 用于：解释数据从哪来、评估上游变化影响。"
        ),
        param_descriptions={
            "ref_id": "产物 id（list_datasets / 分析结果返回的 ref）",
            "depth": "血缘展开深度（默认 3，最大 8）",
        },
    )
    async def get_lineage(
        ref_id: str,
        depth: int = 3,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_catalog.lineage_query import session_lineage

        sid = _resolve_session_id(session_id)
        if not sid or not ref_id:
            return {"success": False, "code": "NOT_FOUND",
                    "error": "缺少有效的会话或产物 id"}
        view = await session_lineage(
            sid, ref_id, max_depth=max(1, min(int(depth or 3), 8))
        )
        if view is None:
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"账本中不存在产物 {ref_id}"}
        return {"success": True, "lineage": view.to_summary()}


def _build_search_args_model() -> type[BaseModel]:
    """search_datasets 的显式参数模型（schema 层枚举，防 LLM 拼错轴）。"""
    from app.lib.data import vocabulary as vocab

    class SearchDatasetsArgs(BaseModel):
        session_id: Optional[str] = Field(
            default=None, description="会话 id（通常无需传入，由运行时注入）"
        )
        keyword: Optional[str] = Field(default=None, description="关键词（名称/来源/生产者）")
        category: Optional[str] = Field(
            default=None,
            description="数据类型",
            json_schema_extra={"enum": vocab.all_categories()},
        )
        role: Optional[str] = Field(
            default=None,
            description="逻辑角色",
            json_schema_extra={"enum": [r.value for r in vocab.LogicalRole]},
        )
        crs: Optional[str] = Field(default=None, description="坐标系（如 EPSG:4326）")
        field: Optional[str] = Field(default=None, description="必须存在的字段名")
        scope: Optional[str] = Field(
            default=None,
            description="来源范围",
            json_schema_extra={"enum": ["session", "upload", "fabric"]},
        )

    return SearchDatasetsArgs
