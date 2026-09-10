"""V6 typed logical plan IR（ADR-0118 W1）。

计划树是 V6 优化器的结构化真相：节点可序列化、canonical 化、确定性哈希。
语义载体（谓词 AST）**引用** ``query/predicates.py`` 的同一批模型 —— 本模块
不复制查询语义，只给联邦查询一个可枚举/可估价/可解释的形状。

哈希口径：``plan_hash`` 只覆盖**语义与结构**（节点种类、谓词、键、投影、
limit/CRS）；``estimated_rows`` 等成本提示是优化器输入而非计划本身，不进
哈希 —— 提示更新不应让同一查询的计划指纹漂移。
"""

from __future__ import annotations

import hashlib
import json
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Annotated

from app.services.data_fabric.query.predicates import (
    Predicate,
    predicate_from_dict,
    predicate_to_canonical_dict,
)


def _canonical_aggregate_request(req):
    """aggregate_request 的确定性规范化（None 透传；否则排序键序列化）。"""
    if not isinstance(req, dict):
        return None
    return {
        "group_by": sorted(req.get("group_by") or []),
        "aggregates": sorted(
            req.get("aggregates") or [], key=lambda a: json.dumps(a, sort_keys=True)
        ),
    }


def _plan_fingerprint(canonical: Dict[str, Any]) -> str:
    payload = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class _LogicalBase(BaseModel):
    """计划节点基类：extra=forbid（拼写错误即构造失败）、确定性 canonical。"""

    model_config = ConfigDict(extra="forbid")

    def canonical_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    def plan_hash(self) -> str:
        """确定性计划指纹（sha256 前 16 hex；口径见模块 docstring）。"""
        return _plan_fingerprint(self.canonical_dict())


class LogicalScan(_LogicalBase):
    """叶子：单源拉取。``where``/``bbox``/``fields``/``fetch_limit`` 对应
    adapter 契约的逐字段语义（与 QuerySpec 的 extras 一一对应）。"""

    kind: Literal["scan"] = "scan"
    source_id: str
    dataset_id: str
    where: Optional[Predicate] = None  # 可编译/可求值的谓词 AST
    where_raw: Optional[str] = None  # 不可解析的原始过滤（诚实保留，不猜测）
    bbox: Optional[List[float]] = None  # 共享空间裁剪（adapter 契约字段）
    fields: Optional[List[str]] = None  # None = 不投影（全列）
    fetch_limit: Optional[int] = None  # None = 不设源级 limit
    crs: Optional[str] = None  # 源几何 CRS（costing 感知）
    estimated_rows: Optional[int] = None  # 成本提示（不进 plan_hash）
    # ── V7（ADR-0119 W8/W9 additive）──
    # server-side CRS 变换：扫描请求携带 output_crs（adapter caps
    # output_crs_pushdown=True 才会由枚举器写入；None = 本地交付语义）。
    output_crs: Optional[str] = None
    # 安全聚合下推（aggregate_join 证明通过时写入右 scan）：{"group_by": [...],
    # "aggregates": [{func, field}]}；scan 返回组行（result.data）。
    aggregate_request: Optional[Dict[str, Any]] = None

    def canonical_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "kind": self.kind,
            "source_id": self.source_id,
            "dataset_id": self.dataset_id,
            "where": predicate_to_canonical_dict(self.where)
            if self.where is not None
            else None,
            "where_raw": self.where_raw,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "fields": sorted(self.fields) if self.fields is not None else None,
            "fetch_limit": self.fetch_limit,
            "crs": self.crs,
            "output_crs": self.output_crs,
            "aggregate_request": _canonical_aggregate_request(self.aggregate_request),
        }
        return d


class LogicalFilter(_LogicalBase):
    """残留本地过滤（scan 谓词之外的语义过滤）。"""

    kind: Literal["filter"] = "filter"
    predicate: Predicate
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "predicate": predicate_to_canonical_dict(self.predicate),
            "input": self.input.canonical_dict(),
        }


class LogicalProject(_LogicalBase):
    kind: Literal["project"] = "project"
    fields: List[str]
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "fields": sorted(self.fields),
            "input": self.input.canonical_dict(),
        }


class LogicalJoin(_LogicalBase):
    """二元连接。``join_kind`` 承载 V5 三种语义（attribute/spatial/aggregate）。"""

    kind: Literal["join"] = "join"
    join_kind: Literal["attribute_join", "spatial_join", "aggregate_join"]
    left: "LogicalNode"
    right: "LogicalNode"
    join_field_left: Optional[str] = None
    join_field_right: Optional[str] = None
    spatial_op: Optional[Literal["within", "intersects"]] = None
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None
    # ── V7（ADR-0119 W9 additive）──
    # 安全聚合下推已启用（R-C1 五条件证明通过；右 scan 携带
    # aggregate_request，执行器拉组行后精确投影 —— 输出形状与本地内核逐位一致）。
    aggregate_pushdown: bool = False

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "join_kind": self.join_kind,
            "left": self.left.canonical_dict(),
            "right": self.right.canonical_dict(),
            "join_field_left": self.join_field_left,
            "join_field_right": self.join_field_right,
            "spatial_op": self.spatial_op,
            "group_by_right": sorted(self.group_by_right)
            if self.group_by_right
            else None,
            "aggregates": (
                sorted(self.aggregates, key=lambda a: json.dumps(a, sort_keys=True))
                if self.aggregates
                else None
            ),
            "aggregate_pushdown": self.aggregate_pushdown,
        }


class LogicalAggregate(_LogicalBase):
    kind: Literal["aggregate"] = "aggregate"
    group_by: List[str]
    aggregates: List[Dict[str, Any]]
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "group_by": sorted(self.group_by),
            "aggregates": sorted(
                self.aggregates, key=lambda a: json.dumps(a, sort_keys=True)
            ),
            "input": self.input.canonical_dict(),
        }


class LogicalSort(_LogicalBase):
    kind: Literal["sort"] = "sort"
    order_by: List[Dict[str, Any]]  # 顺序即语义（多级排序），不 canonical 重排
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "order_by": self.order_by,
            "input": self.input.canonical_dict(),
        }


class LogicalLimit(_LogicalBase):
    kind: Literal["limit"] = "limit"
    limit: int
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "limit": self.limit,
            "input": self.input.canonical_dict(),
        }


class LogicalReproject(_LogicalBase):
    """CRS 变换算子（V6：变换成为可估价/可放置的计划节点）。"""

    kind: Literal["reproject"] = "reproject"
    from_crs: str
    to_crs: str
    placement: Literal["server", "local"]
    input: "LogicalNode"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "from_crs": self.from_crs,
            "to_crs": self.to_crs,
            "placement": self.placement,
            "input": self.input.canonical_dict(),
        }


LogicalNode = Annotated[
    Union[
        LogicalScan,
        LogicalFilter,
        LogicalProject,
        LogicalJoin,
        LogicalAggregate,
        LogicalSort,
        LogicalLimit,
        LogicalReproject,
    ],
    Field(discriminator="kind"),
]


def _rebuild() -> None:
    for m in (
        LogicalFilter,
        LogicalProject,
        LogicalJoin,
        LogicalAggregate,
        LogicalSort,
        LogicalLimit,
        LogicalReproject,
    ):
        m.model_rebuild()


_rebuild()


def logical_from_dict(d: Dict[str, Any]) -> Optional[LogicalNode]:
    """从 model_dump(mode="json") 的 dict 重建计划树（判别字段 kind）。"""
    if not isinstance(d, dict):
        return None
    return _NODE_MODEL_BY_KIND[d["kind"]].model_validate(d)  # type: ignore[index]


_NODE_MODEL_BY_KIND = {
    "scan": LogicalScan,
    "filter": LogicalFilter,
    "project": LogicalProject,
    "join": LogicalJoin,
    "aggregate": LogicalAggregate,
    "sort": LogicalSort,
    "limit": LogicalLimit,
    "reproject": LogicalReproject,
}


def iter_nodes(node: LogicalNode) -> Iterator[LogicalNode]:
    """先序遍历（确定性；供 explain/统计使用）。"""
    yield node
    inp = getattr(node, "input", None)
    if inp is not None:
        yield from iter_nodes(inp)
        return
    for side in ("left", "right"):
        child = getattr(node, side, None)
        if child is not None:
            yield from iter_nodes(child)


def _coerce_where(where: Any) -> tuple:
    """ChainSource.where → (Predicate | None, raw_str | None)。

    dict → 谓词 AST（解析失败诚实降级为 raw）；Predicate 实例直通；
    字符串/未知形状 → raw（绝不猜测语义）。
    """
    if where is None:
        return None, None
    if isinstance(where, str):
        return None, where
    if isinstance(where, dict):
        try:
            return predicate_from_dict(where), None
        except Exception:
            return None, json.dumps(where, ensure_ascii=False, sort_keys=True)
    if getattr(where, "op", None):
        return where, None  # 已是谓词实例
    return None, str(where)


def chain_to_logical(req: Any) -> LogicalNode:
    """FederatedChainRequest → 左深计划树（**given 序**；重排是 enumerator 的职责）。

    duck-typed 入参：避免 federation → federated 的模块级反向依赖
    （federation 在 engine 分派处惰性导入本包）。
    """
    sources = list(req.sources)
    joins = list(req.joins)
    if len(sources) < 2 or len(joins) != len(sources) - 1:
        # 形状不合法交给 V5 planner 的 typed 校验（同一错误码）；此处
        # 尽力而为地构建（两源以上才成树）。
        pass

    def _scan(i: int) -> LogicalScan:
        s = sources[i]
        where, where_raw = _coerce_where(s.where)
        return LogicalScan(
            source_id=s.source_id,
            dataset_id=s.dataset_id,
            where=where,
            where_raw=where_raw,
            bbox=list(req.bbox) if getattr(req, "bbox", None) else None,
            fields=list(s.fields) if s.fields else None,
            fetch_limit=getattr(req, "limit", None),
            crs=getattr(s, "srs", None),
            estimated_rows=s.estimated_rows,
        )

    if not joins:
        tree: LogicalNode = _scan(0)
    else:
        tree = _scan(0)
        for hop, j in enumerate(joins):
            tree = LogicalJoin(
                join_kind=j.kind,
                left=tree,
                right=_scan(hop + 1),
                join_field_left=j.join_field_left,
                join_field_right=j.join_field_right,
                spatial_op=j.spatial_op,
                group_by_right=list(j.group_by_right) if j.group_by_right else None,
                aggregates=list(j.aggregates) if j.aggregates else None,
            )
    limit = getattr(req, "limit", None)
    if isinstance(limit, int) and limit > 0:
        tree = LogicalLimit(limit=limit, input=tree)
    return tree


__all__ = [
    "LogicalNode",
    "LogicalScan",
    "LogicalFilter",
    "LogicalProject",
    "LogicalJoin",
    "LogicalAggregate",
    "LogicalSort",
    "LogicalLimit",
    "LogicalReproject",
    "logical_from_dict",
    "iter_nodes",
    "chain_to_logical",
]
