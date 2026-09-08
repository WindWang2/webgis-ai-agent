"""统一标量聚合累加器（Wave 5 / ADR-0101 D8 续）。

此前仓库存在两份语义相近但实现分叉的聚合器：``streaming.stream_aggregate``
（标量累加，O(组数) 内存）与 ``query.execution.compute_aggregates``（按组
缓冲整行，O(行数) 内存）。Wave 5 引入 Arrow lane 前必须收敛为一套语义 ——
本模块就是那"一个真相"：

- ``count``（无字段）= 组内行数；``count(field)`` = 非 null 计数；
- 数值聚合（sum/avg/stddev）**排除 bool 与不可转 float 的值**；
- ``min``/``max`` 双轨（round-1 review CRITICAL，pre-W5 语义对齐）：数值轨
  （bool 与不可转 float 的值不参与）+ 字符串轨（ISO 日期串等按 Python
  字典序 = 时间序比较）；finalize 时**有数值取数值，否则取字符串**
  —— 可排序的非数值列（日期、名称）不再悄悄变 None；
- ``stddev`` 为样本口径（n-1，对齐 Postgres STDDEV_SAMP；n<2 → None），
  按 Welford 单遍算法累计（数值稳定：UTM 量级 ~5e5 的坐标列不再因
  naive ``E[x²]−E[x]²`` 灾难性抵消丢精度）；
- ``distinct_count`` 有界：去重集合容量到 ``DISTINCT_COUNT_CAP`` 即停并置
  ``approximate`` 标记（诚实近似，绝不无界内存）；

两个既有驱动（``stream_aggregate`` / ``compute_aggregates``）以及 Arrow lane
的 ``arrow_ops.arrow_aggregate_batches`` 全部委托到 ``AggregateDriver``。
各调用点的对外契约（行形状 / 列名 / 空输入行为）由驱动旗标保持：

- ``stream_aggregate``：空输入产出**零**行（聚合是终结操作）；
- ``compute_aggregates``：空输入的**无分组**聚合仍输出一行（与 SQL
  ``SELECT count(*)`` 一致）—— 经 ``emit_empty_global_row`` 旗标区分；
- 行列名各按其现行约定（stream 风格恒带 ``count`` 与 ``func_*``；
  compute 风格仅在请求时命名 ``count`` / ``func_field``）。

本模块纯 stdlib，不依赖 pyarrow（dict lane 与 Arrow lane 共用）。

与 V4 基线的语义差异（round-2 review MINOR：统一两套实现时有意收敛的
分叉，如实列出，绝不宣称"行为不变"）：

- **可转数值的字符串改走数值轨**：``min("9", "10")`` 按 V4 字符串字典序
  返回 ``"10"``，本实现因 ``float("9")`` 可转而走数值轨返回 ``9`` ——
  字典序比较 → 数值比较的语义变化；
- **bool 被 min/max 排除**（与 sum/avg/stddev 同一排除口径）—— V4 部分
  路径把 ``True/False`` 当 ``1/0`` 混入最值；
- **数值 min/max 恒返回 float**：``min(1, 2)`` → ``1.0``（float(value)
  归一）—— V4 保留原始 int 类型；
- **混合 str/int 不再抛 TypeError**：各值按可转性分流（数值进数值轨、
  其余可排序字符串进字符串轨、互不跨轨比较，finalize 有数值取数值否则
  取字符串）—— V4 的 ``min([1, "a"])`` 直接崩溃。

收敛原则：能算就算、算不了诚实跳过/分流；语义由 data-plane parity 测试
（``tests/unit/test_data_plane_vector_v4.py`` /
``tests/unit/test_data_plane_arrow_v5.py``）钉住。
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.data_fabric.errors import DataFabricError, QUERY_BUDGET_EXCEEDED

#: distinct_count 去重集合容量上界（到顶即置 approximate，诚实近似）。
DISTINCT_COUNT_CAP = 10_000

#: group_by 组基数容量上界（round-1 review PERF MINOR-3）：``_groups`` 字典
#: 在高基数 group_by（如逐点 id）下无界增长 = O(组数) 内存无红线。到顶即
#: 抛 typed 错误诚实拒绝（env 可调；0/非法值回退默认）。
DEFAULT_GROUP_CAP = 100_000
_ENV_GROUP_CAP = "WEBGIS_FABRIC_AGGREGATE_GROUP_CAP"


def group_count_cap() -> int:
    """每次驱动构造时读取（测试/部署可按查询调参，无需进程重启）。"""
    raw = os.environ.get(_ENV_GROUP_CAP, "")
    try:
        return max(int(float(raw)), 1)
    except (TypeError, ValueError):
        return DEFAULT_GROUP_CAP


class AggregateGroupCapExceeded(DataFabricError):
    """group_by 组基数越过容量上界（PERF MINOR-3 的诚实 typed 拒绝）。

    结果行是喂给工具/前端的纯 dict —— 没有天然的 metadata 通道，伪造一行
    ``__overflow__`` 会把虚假分组混进数值输出。因此按上游错误分类学抛
    typed 错误，``code`` 复用 ``QUERY_BUDGET_EXCEEDED``：与 StreamingBudget
    的行/字节/顶点预算同一处理面（compute_aggregates / stream_aggregate /
    Arrow lane 三个 lane 都从 ``AggregateDriver.update`` 抛出 —— 一种行为，
    差分 parity 测试钉住）。
    """

    code = QUERY_BUDGET_EXCEEDED

#: 支持的聚合函数（与 AggSpec 的 Literal 一致）。
_SUPPORTED_FUNCS = ("count", "sum", "avg", "min", "max", "stddev", "distinct_count")


def _hashable(v: Any) -> Any:
    """list → tuple（集合成员化；与原 compute_aggregates 同口径）。"""
    if isinstance(v, list):
        return tuple(v)
    return v


class AggRequest:
    """规范化的聚合请求（dict 与 AggSpec 的最小公共形状）。"""

    __slots__ = ("func", "field")

    def __init__(self, func: str, field: Optional[str]):
        self.func = func
        self.field = field


def normalize_agg_specs(aggs: Sequence[Any]) -> List[AggRequest]:
    """dict（``{"func","field"}``）或 AggSpec 序列 → AggRequest 列表。"""
    out: List[AggRequest] = []
    for a in aggs:
        if isinstance(a, dict):
            out.append(AggRequest(a.get("func"), a.get("field")))
        else:
            out.append(AggRequest(getattr(a, "func", None), getattr(a, "field", None)))
    return out


class ScalarAccumulator:
    """单个 (func, field) 聚合在一个分组内的标量状态。

    值语义与原 ``streaming._accumulate``/``_finalize`` 逐条对齐（Wave 5 把它
    定为唯一真相）：bool 永不参与数值聚合；数值尝试 ``float(v)``（不可转则
    跳过）；``count(field)`` 计非 null；``distinct_count`` 有界并带诚实近似。
    ``min``/``max`` 双轨见模块 docstring（数值轨 + 字符串轨）；``stddev``
    走 Welford (n, mean, M2)。
    """

    __slots__ = ("func", "field", "n", "_sum", "_mean", "_m2",
                 "_num_min", "_num_max", "_str_min", "_str_max",
                 "_seen", "_distinct_truncated")

    def __init__(self, func: Optional[str], field: Optional[str] = None):
        if func not in _SUPPORTED_FUNCS:
            raise ValueError(
                f"unsupported aggregate func {func!r} (supported: {_SUPPORTED_FUNCS})")
        self.func = func
        self.field = field
        self.n = 0
        self._sum = 0.0
        self._mean = 0.0
        self._m2 = 0.0
        self._num_min: Optional[float] = None
        self._num_max: Optional[float] = None
        self._str_min: Optional[str] = None
        self._str_max: Optional[str] = None
        self._seen: Optional[set] = set() if func == "distinct_count" else None
        self._distinct_truncated = False

    def update(self, value: Any) -> None:
        if self.func == "count":
            # count(field) 与本地聚合器同语义：非 null 计数（评审 MINOR F6）。
            # count(None) 不在此累加 —— 组行数由驱动统一计数。
            if self.field is not None and value is not None:
                self.n += 1
            return
        if self.func == "distinct_count":
            if value is None:
                return
            if len(self._seen) >= DISTINCT_COUNT_CAP:
                # 有界诚实：到顶停采并置近似标记（绝不无界内存）。
                self._distinct_truncated = True
                return
            try:
                self._seen.add(value)
            except TypeError:
                self._seen.add(_hashable(value))
            return
        if value is None or isinstance(value, bool):
            # bool 不参与数值聚合（与本地聚合器同一排除口径）。
            return
        if self.func in ("min", "max"):
            # 双轨（pre-W5 语义对齐）：可转 float 的值进数值轨；其余可排序
            # 值（str，含 ISO 日期串）进字符串轨 —— O(1)/组，绝不缓冲全值。
            try:
                fv = float(value)
            except (TypeError, ValueError):
                if isinstance(value, str):
                    self._str_min = value if self._str_min is None else min(self._str_min, value)
                    self._str_max = value if self._str_max is None else max(self._str_max, value)
                return
            self.n += 1
            self._num_min = fv if self._num_min is None else min(self._num_min, fv)
            self._num_max = fv if self._num_max is None else max(self._num_max, fv)
            return
        try:
            fv = float(value)
        except (TypeError, ValueError):
            return
        self.n += 1
        self._sum += fv
        # Welford（round-1 review MAJOR）：单遍数值稳定的矩累计，
        # O(组数) 内存；替代 naive ``E[x²]−E[x]²``（UTM 量级灾难性抵消）。
        delta = fv - self._mean
        self._mean += delta / self.n
        self._m2 += delta * (fv - self._mean)

    @property
    def approximate(self) -> bool:
        """distinct_count 触及容量上界 → 结果是下界近似（诚实标记）。"""
        return self._distinct_truncated

    def finalize(self, group_rows: int) -> Any:
        if self.func == "count":
            return group_rows if self.field is None else self.n
        if self.func == "distinct_count":
            return len(self._seen) if self._seen is not None else 0
        if self.func == "min":
            # 有数值取数值，否则字符串轨（日期/名称列不再悄悄变 None）。
            if self._num_min is not None:
                return self._num_min
            return self._str_min
        if self.func == "max":
            if self._num_max is not None:
                return self._num_max
            return self._str_max
        if self.n == 0:
            return None
        if self.func == "sum":
            return self._sum
        if self.func == "avg":
            return self._sum / self.n
        # stddev：样本口径与 PG STDDEV_SAMP 对齐：n<2 → None（Welford M2）
        if self.n < 2:
            return None
        var = max(0.0, self._m2 / (self.n - 1))
        return math.sqrt(var)


class _GroupState:
    __slots__ = ("key", "rows", "accs")

    def __init__(self, key: Tuple, accs: List[ScalarAccumulator]):
        self.key = key
        self.rows = 0
        self.accs = accs


class AggregateDriver:
    """按组驱动的聚合累加器（stream / compute / Arrow 三个 lane 共用）。

    - ``update(props)``：喂入一行扁平属性映射（键 = 分组字段与聚合字段）；
    - ``finalize_rows(style)``：终结为结果行。
      ``style="stream"`` 复刻 ``stream_aggregate`` 行形状（恒带 ``count``，
      聚合名 ``func_field_or_star``，空输入零行）；
      ``style="compute"`` 复刻 ``compute_aggregates`` 行形状（仅在请求时
      命名，``emit_empty_global_row=True`` 时空输入的无分组聚合出一行哨兵）。
    """

    def __init__(
        self,
        aggregates: Sequence[Any],
        group_by: Optional[Sequence[str]] = None,
        *,
        emit_empty_global_row: bool = False,
    ):
        self.requests = normalize_agg_specs(aggregates)
        for req in self.requests:
            if req.func not in _SUPPORTED_FUNCS:
                raise ValueError(
                    f"unsupported aggregate func {req.func!r} (supported: {_SUPPORTED_FUNCS})")
        self.group_by = list(group_by or [])
        self.emit_empty_global_row = emit_empty_global_row
        self._groups: Dict[Tuple, _GroupState] = {}
        self._group_cap = group_count_cap()

    def update(self, props: Any) -> None:
        key = tuple(props.get(g) for g in self.group_by)
        st = self._groups.get(key)
        if st is None:
            if len(self._groups) >= self._group_cap:
                # PERF MINOR-3：无界组基数的诚实红线（无 group_by 的全局
                # 聚合恒为单组，永不触顶）。
                raise AggregateGroupCapExceeded(
                    f"group cardinality exceeded cap ({self._group_cap})",
                    details={
                        "cap": self._group_cap,
                        "hint": (
                            "raise WEBGIS_FABRIC_AGGREGATE_GROUP_CAP, "
                            "coarsen group_by, or pre-aggregate upstream"
                        ),
                    },
                )
            st = _GroupState(key, [ScalarAccumulator(r.func, r.field) for r in self.requests])
            self._groups[key] = st
        st.rows += 1
        for req, acc in zip(self.requests, st.accs):
            acc.update(props.get(req.field))

    @property
    def is_empty(self) -> bool:
        return not self._groups

    def finalize_rows(self, style: str = "compute") -> List[Dict[str, Any]]:
        if style not in ("stream", "compute"):
            raise ValueError(f"unknown finalize style {style!r}")
        out: List[Dict[str, Any]] = []
        for st in self._groups.values():
            row: Dict[str, Any] = {}
            if self.group_by:
                for g, v in zip(self.group_by, st.key):
                    row[g] = v
            if style == "stream":
                row["count"] = st.rows
            for req, acc in zip(self.requests, st.accs):
                if style == "stream":
                    name = f"{req.func}_{req.field or '*'}"
                    # stream 契约原样：count(None) 从不在此命名（组行数在
                    # "count" 键下），历史行为输出 None 占位。
                    value = None if (req.func == "count" and req.field is None) \
                        else acc.finalize(st.rows)
                else:
                    name = req.func if req.field is None else f"{req.func}_{req.field}"
                    value = acc.finalize(st.rows)
                row[name] = value
                if acc.approximate:
                    # 有界 distinct 的诚实标记（仅在触顶时出现）。
                    row[f"{name}_approximate"] = True
            out.append(row)
        if self.emit_empty_global_row and not self.group_by and not out:
            # 空输入的全局聚合仍输出一行（与 SQL SELECT count(*) 一致）。
            row = {}
            for req in self.requests:
                name = req.func if req.field is None else f"{req.func}_{req.field}"
                row[name] = 0 if req.func == "count" else None
            out.append(row)
        return out


__all__ = [
    "DISTINCT_COUNT_CAP",
    "DEFAULT_GROUP_CAP",
    "AggregateGroupCapExceeded",
    "AggRequest",
    "ScalarAccumulator",
    "AggregateDriver",
    "group_count_cap",
    "normalize_agg_specs",
]
