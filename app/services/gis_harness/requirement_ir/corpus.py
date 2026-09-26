"""F02 真实场景 corpus（任务书 §8：8 场景 + 多轮增量流）。

data-as-code：typed、确定性、版本化，供验收测试（tests/unit/gis_harness/
requirement_ir/test_corpus.py）与后续 evaluation 复用。场景为**期望契约**
（分类/stages/澄清/patch 面），不绑定具体工具选择。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.requirement_ir.contracts import TaskKind

MAX_EXPECT = 12


class CorpusTurn(BaseModel):
    """一轮用户输入 + 对 IR 的期望断言面。"""

    model_config = ConfigDict(extra="forbid")

    query: str
    expect_kind: Optional[TaskKind] = None
    expect_stages: Tuple[TaskKind, ...] = Field((), max_length=MAX_EXPECT)
    expect_patch_paths: Tuple[str, ...] = ()     # diff/归因应出现的 path 前缀
    expect_clarification_codes: Tuple[str, ...] = ()  # 应出现的澄清 code（子集即可）
    assume_document: bool = False    # 该轮以"会话已有文档"为语境（edit 判定输入）
    note: str = ""


class CorpusScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    turns: List[CorpusTurn] = Field(default_factory=list, max_length=8)
    synonym_variant: str = ""    # 同义改写（语义核不变 → requirement_digest 不变）


CORPUS: Tuple[CorpusScenario, ...] = (
    CorpusScenario(
        id="chengdu_primary_schools",
        description="成都小学分布成图（地图语义，非统计）",
        synonym_variant="看看成都市的小学分布，做一张地图",
        turns=[CorpusTurn(
            query="成都的小学分布情况，做一张图",
            expect_kind="map",
        )],
    ),
    CorpusScenario(
        id="admin_stat_heatmap",
        description="行政区统计 + 热力表达（ choropleth/heatmap 表达约束）",
        synonym_variant="统计成都市各区小学数量，画个热力图",
        turns=[CorpusTurn(
            query="统计成都各区小学数量，做成热力图",
            expect_kind="map",
            expect_patch_paths=(),
        )],
    ),
    CorpusScenario(
        id="temporal_change",
        description="时序变化（趋势 → 时序歧义：无显式区间时问时间窗）",
        turns=[CorpusTurn(
            query="分析成都2019到2024年绿地覆盖率逐年变化趋势",
            expect_kind="analysis",
        )],
    ),
    CorpusScenario(
        id="landuse_categories",
        description="土地利用类别构成（分类统计）",
        turns=[CorpusTurn(
            query="统计这片区域各地类土地利用占比",
            expect_kind="analysis",
            expect_clarification_codes=("aoi_unresolved",),
        )],
    ),
    CorpusScenario(
        id="two_measure_compare",
        description="两指标比较（双指标聚合）",
        turns=[CorpusTurn(
            query="对比成都各片区的人口密度和小学密度",
            expect_kind="analysis",
        )],
    ),
    CorpusScenario(
        id="analysis_only",
        description="只分析不画图（no-map 护栏必须压制 map）",
        turns=[CorpusTurn(
            query="只分析不画图，统计成都各区医院数量",
            expect_kind="analysis",
        )],
    ),
    CorpusScenario(
        id="analyze_then_map",
        description="先分析后制图（显式时序 stages）",
        turns=[CorpusTurn(
            query="先分析成都各区小学密度，然后再画图",
            expect_kind="map",
            expect_stages=("analysis", "map"),
        )],
    ),
    CorpusScenario(
        id="export_publication",
        description="导出出版图（交付面语义）",
        turns=[CorpusTurn(
            query="把当前的区县统计图导出成出版用的PDF",
            expect_kind="edit",
            assume_document=True,
            note="『导出成』落在既有成果上 → 增量修订（output.formats + publish 义务）；无文档语境时同话语回落 export 语义",
        )],
    ),
    CorpusScenario(
        id="multiturn_edits",
        description="多轮增量：改成各区统计 / 隐藏道路 / 换蓝色 / 再导出 PDF",
        turns=[
            CorpusTurn(query="成都的小学分布情况，做一张图", expect_kind="map"),
            CorpusTurn(
                query="改成各区统计",
                expect_kind="edit",
                assume_document=True,
                expect_patch_paths=("task.task_type",),
            ),
            CorpusTurn(
                query="隐藏道路图层",
                expect_kind="edit",
                assume_document=True,
                expect_patch_paths=("representation.hidden_layer_ids",),
            ),
            CorpusTurn(
                query="换成蓝色",
                expect_kind="edit",
                assume_document=True,
                expect_patch_paths=("representation.palette",),
            ),
            CorpusTurn(
                query="再导出PDF",
                expect_kind="edit",
                assume_document=True,
                expect_patch_paths=("output.formats",),
            ),
        ],
    ),
)


def get_scenario(scenario_id: str) -> Optional[CorpusScenario]:
    for scenario in CORPUS:
        if scenario.id == scenario_id:
            return scenario
    return None


__all__ = ["CORPUS", "CorpusScenario", "CorpusTurn", "get_scenario"]
