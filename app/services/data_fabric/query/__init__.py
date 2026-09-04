"""Data Fabric V2 query runtime package (ADR-0094).

子模块：
- ``predicates``  类型化谓词 AST（安全编译链的唯一输入）
- ``models``      QuerySpecV2 / QueryPlan / capabilities / evidence
- ``normalize``   legacy QuerySpec → V2 归一化 + 受限 where 解析
- ``compilers``   AST → PostGIS SQL / CQL2 / ArcGIS where / FES XML
- ``capabilities``truthful capability 矩阵默认值
- ``planner``     capability-aware 确定性 planner（explain 数据源）
- ``evidence``    QueryEvidence 组装
- ``execution``   采样 / cursor / 流式预算 / 本地聚合原语
- ``federation``  受控两源联邦执行
"""
from app.services.data_fabric.query.models import (
    AdapterCapabilitiesV2,
    AggSpec,
    CursorPage,
    DatasetVersion,
    ExecutionBudget,
    ExecutionFragment,
    OffsetPage,
    OrderByItem,
    OutputSpec,
    QueryPlan,
    QuerySpecV2,
    QueryEvidence,
    ResultMode,
    SampleSpec,
    query_fingerprint,
)
from app.services.data_fabric.query.normalize import normalize_query_spec, parse_legacy_where
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import PredicateError

__all__ = [
    "AdapterCapabilitiesV2",
    "AggSpec",
    "CursorPage",
    "DatasetVersion",
    "ExecutionBudget",
    "ExecutionFragment",
    "OffsetPage",
    "OrderByItem",
    "OutputSpec",
    "QueryPlan",
    "QuerySpecV2",
    "QueryEvidence",
    "ResultMode",
    "SampleSpec",
    "query_fingerprint",
    "normalize_query_spec",
    "parse_legacy_where",
    "plan_query",
    "PredicateError",
]
