"""GIS Situation（方向 2，ADR-0180）—— 结构化会话世界情境。

与相邻模块的分工（防重复施工）：
- ``gis_world_state``：mutation 门面 + pull 型读模型工具
  （``webgis_world_state``，agent 主动拉取的快照）；
- ``chat.v6_context_blocks``：既有三层文本块（node/workflow/map situation
  摘要），保持不变；
- 本包：per-turn **push 编译** —— 严格契约（``GISSituation``，事实带
  source/status/revision/ref）、单一编译器（partial 降级）、分组 diff
  （revision 单调守卫）、前端交互观察摄入、有界确定性投影（Pi turn
  env_block 位）、查询 API 与一致性检查。

红线：绝无 payload（ref-only）、显式 unknown、确定性（同 store 输入同
输出）、投影有界（byte cap + omitted 留痕）、快照只前进。
"""
from app.services.gis_situation.contract import (
    COMPILER_VERSION,
    GISSituation,
    SituationEvidence,
    SituationIdentity,
    SituationRevision,
)
from app.services.gis_situation.diff import (
    SituationDelta,
    advance_snapshot,
    diff_situation,
    load_snapshot,
)
from app.services.gis_situation.facts import (
    FACT_STATUSES,
    SitFact,
    known,
    stale,
    unavailable,
    unknown,
)
from app.services.gis_situation.compiler import (
    compile_situation,
    situation_enabled,
)
from app.services.gis_situation.projection import (
    SITUATION_BLOCK_MAX_BYTES,
    SituationProjection,
    render_situation_for_context,
)

__all__ = [
    "COMPILER_VERSION",
    "GISSituation",
    "SituationEvidence",
    "SituationIdentity",
    "SituationRevision",
    "SituationDelta",
    "SitFact",
    "FACT_STATUSES",
    "known",
    "unknown",
    "stale",
    "unavailable",
    "compile_situation",
    "situation_enabled",
    "diff_situation",
    "load_snapshot",
    "advance_snapshot",
    "SITUATION_BLOCK_MAX_BYTES",
    "SituationProjection",
    "render_situation_for_context",
]
