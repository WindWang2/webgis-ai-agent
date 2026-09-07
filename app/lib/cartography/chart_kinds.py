"""Chart Kind Registry — 浮动统计图表系统的后端契约（Design System V4）.

图表面板（``chart_panel``）在 V4 升级为一等浮动统计组件：

- **kind 词表**：每个 kind 是图表面板的一个显式 variant（组件目录口径），
  声明其数据形状、live 渲染引擎（recharts/自绘 SVG）与导出能力等级；
- **状态机**：``hidden / visible / collapsed / expanded / floating /
  docked / anchored`` 七态及合法迁移 —— 用户普通 UI、Agent 工具调用、
  serialization/replay 三方共用同一状态真值；
- **Agent 操作词表**：move/resize/pin/collapse/close/restore/
  switch_chart_type/switch_field/filter/highlight —— 后端只登记合法操作
  与前置状态（描述性契约），执行在前端 chart 命令通道。

诚实原则：导出能力等级必须与前端 export-chrome 实际实现对账
（full = canvas 逐 kind 绘制；degraded = 近似绘制且披露；unsupported =
不进导出画布、导出侧回落为数据表披露）。本模块是唯一登记处。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ChartState = Literal[
    "hidden", "visible", "collapsed", "expanded", "floating", "docked",
    "anchored",
]

# 七态全集（词表锁定，前端状态机与 serialization 共用）
CHART_STATES: frozenset = frozenset({
    "hidden", "visible", "collapsed", "expanded", "floating", "docked",
    "anchored",
})

# 状态机合法迁移（有向：from → to）
CHART_STATE_TRANSITIONS: frozenset = frozenset({
    ("hidden", "visible"),
    ("visible", "collapsed"),
    ("visible", "expanded"),
    ("visible", "floating"),
    ("visible", "docked"),
    ("visible", "anchored"),
    ("collapsed", "expanded"),
    ("collapsed", "hidden"),
    ("expanded", "collapsed"),
    ("expanded", "hidden"),
    ("floating", "collapsed"),
    ("floating", "hidden"),
    ("docked", "collapsed"),
    ("docked", "hidden"),
    ("anchored", "collapsed"),
    ("anchored", "hidden"),
    ("floating", "docked"),
    ("floating", "anchored"),
    ("docked", "floating"),
    ("anchored", "floating"),
    ("docked", "anchored"),
})


def can_transition(from_state: str, to_state: str) -> bool:
    """状态迁移合法性（有向）：hidden 只能经 visible 回场；
    floating/docked/anchored 三态互转；任何态可直接折叠/隐藏。"""
    if from_state == to_state:
        return True
    return (from_state, to_state) in CHART_STATE_TRANSITIONS


# ── Agent / 用户共享的操作词表 ─────────────────────────────────────────
AGENT_CHART_OPERATIONS: Dict[str, List[str]] = {
    # operation → 合法前置状态（空 = 任意态）
    "move": [],
    "resize": ["floating"],
    "pin": ["floating", "docked", "anchored"],
    "collapse": ["visible", "expanded", "floating", "docked", "anchored"],
    "expand": ["collapsed", "visible"],
    "close": [],                      # → hidden
    "restore": ["hidden"],
    "switch_chart_type": [],
    "switch_field": [],
    "filter": [],
    "highlight": [],                  # 联动 selection store，不改状态
}

ExportLevel = Literal["full", "degraded", "unsupported"]

# 数据形状：单序列 name/value；xy = 数值 x/y 点；matrix = 行×列热矩阵；
# table = 有序排名行；kpi = 指标集
DataShape = Literal["series", "xy", "matrix", "table", "kpi"]


class ChartKindDescriptor(BaseModel):
    """一个图表 kind 的机器可读契约。"""

    id: str
    name_zh: str
    data_shape: DataShape = "series"
    # live 渲染引擎：recharts（声明式）或 custom-svg（自绘，如箱线/热矩阵）
    live_engine: Literal["recharts", "custom-svg", "planned"] = "recharts"
    # 导出能力真值（与 frontend/lib/map-kit/export-chrome.ts 对账）
    export_level: ExportLevel = "full"
    export_note_zh: str = ""
    # 交互能力（selection 联动等）
    selection_linkage: bool = False
    aliases: List[str] = Field(default_factory=list)
    notes_zh: List[str] = Field(default_factory=list)


CHART_KINDS: List[ChartKindDescriptor] = [
    ChartKindDescriptor(
        id="bar", name_zh="柱状图", data_shape="series",
        selection_linkage=True,
    ),
    ChartKindDescriptor(
        id="horizontal_bar", name_zh="条形图", data_shape="series",
        selection_linkage=True, aliases=["hbar"],
    ),
    ChartKindDescriptor(
        id="grouped_bar", name_zh="分组柱状图", data_shape="table",
        selection_linkage=True,
        notes_zh=["多序列按类目并排；数据形状为行=类目、列=序列的表"],
    ),
    ChartKindDescriptor(
        id="stacked_bar", name_zh="堆叠柱状图", data_shape="table",
        selection_linkage=True,
    ),
    ChartKindDescriptor(id="line", name_zh="折线图", data_shape="series"),
    ChartKindDescriptor(
        id="area", name_zh="面积图", data_shape="series",
    ),
    ChartKindDescriptor(
        id="scatter", name_zh="散点图", data_shape="xy",
        selection_linkage=True,
    ),
    ChartKindDescriptor(
        id="histogram", name_zh="直方图", data_shape="series",
        live_engine="recharts",
        notes_zh=["分箱在提交数据前由后端 classify 分位数/等距分箱完成，"
                  "前端只画柱；保证与地图分级同一断点语义"],
    ),
    ChartKindDescriptor(
        id="box_plot", name_zh="箱线图", data_shape="series",
        live_engine="custom-svg",
        export_level="full",
        notes_zh=["五数概括（min/q1/median/q3/max）自绘 SVG；"
                  "recharts 无箱线原生支持"],
    ),
    ChartKindDescriptor(
        id="violin", name_zh="小提琴图", data_shape="series",
        live_engine="planned", export_level="unsupported",
        export_note_zh="核密度估计分布渲染本分支未实现 —— 诚实 planned，不伪装",
        notes_zh=["未实现：请退回 box_plot 或 histogram"],
    ),
    ChartKindDescriptor(
        id="pie", name_zh="饼图", data_shape="series", selection_linkage=True,
    ),
    ChartKindDescriptor(
        id="donut", name_zh="环形图", data_shape="series",
        selection_linkage=True, aliases=["doughnut"],
    ),
    ChartKindDescriptor(
        id="radar", name_zh="雷达图", data_shape="table",
    ),
    ChartKindDescriptor(
        id="rose", name_zh="玫瑰图（风玫瑰/极区柱）", data_shape="series",
        live_engine="recharts",
        notes_zh=["RadialBar 近似极区柱；角度 only 按值等分"],
    ),
    ChartKindDescriptor(
        id="timeseries", name_zh="时间序列图", data_shape="series",
        aliases=["time_series"],
        notes_zh=["x 为时间轴（ISO 日期/序数）的折线，带时间刻度抽稀"],
    ),
    ChartKindDescriptor(
        id="cumulative", name_zh="累计曲线图", data_shape="series",
        notes_zh=["面积/折线 + 前缀和；累计口径必须在副标题披露"],
    ),
    ChartKindDescriptor(
        id="heat_matrix", name_zh="热矩阵", data_shape="matrix",
        live_engine="custom-svg",
        notes_zh=["行×列色阵（混淆矩阵/OD 矩阵/交叉表）；色阶取 "
                  "COLOR_PALETTES 同源 ramp"],
    ),
    ChartKindDescriptor(
        id="kpi_card", name_zh="KPI 指标卡", data_shape="kpi",
        live_engine="custom-svg",
    ),
    ChartKindDescriptor(
        id="ranking_list", name_zh="排名列表", data_shape="table",
        live_engine="custom-svg",
        selection_linkage=True,
    ),
]

CHART_KIND_IDS: frozenset = frozenset(k.id for k in CHART_KINDS)
_CHART_KIND_BY_ALIAS: Dict[str, str] = {
    alias: k.id for k in CHART_KINDS for alias in ([k.id, *k.aliases])
}


def resolve_chart_kind(kind_or_alias: str) -> Optional[ChartKindDescriptor]:
    kid = _CHART_KIND_BY_ALIAS.get(kind_or_alias)
    if kid is None:
        return None
    for k in CHART_KINDS:
        if k.id == kid:
            return k
    return None


def chart_kind_ids() -> List[str]:
    return sorted(CHART_KIND_IDS)


def validate_chart_kind_registry() -> List[str]:
    issues: List[str] = []
    seen: set = set()
    for k in CHART_KINDS:
        if k.id in seen:
            issues.append(f"chart kind {k.id}: 重复 id")
        seen.add(k.id)
        if k.aliases:
            for a in k.aliases:
                if a in seen or a in CHART_KIND_IDS:
                    issues.append(f"chart kind {k.id}: 别名 {a} 与既有 id 冲突")
        if k.live_engine == "planned" and k.export_level != "unsupported":
            issues.append(
                f"chart kind {k.id}: live 未实现时导出等级必须 unsupported")
        if k.live_engine == "planned" and not k.notes_zh:
            issues.append(f"chart kind {k.id}: planned 必须登记说明")
    return issues


__all__ = [
    "ChartState",
    "CHART_STATES",
    "can_transition",
    "AGENT_CHART_OPERATIONS",
    "ChartKindDescriptor",
    "CHART_KINDS",
    "CHART_KIND_IDS",
    "resolve_chart_kind",
    "chart_kind_ids",
    "validate_chart_kind_registry",
]
