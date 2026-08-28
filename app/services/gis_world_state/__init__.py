"""GISWorldState —— 统一世界状态读模型 + GISMutation 门面（C2 基础）。

设计约束（ADR-0072）：
- **演化而非重造**：desired state 真相源仍是后端 MapSpec（磁盘 + Redis），
  mutation 权威仍是 MapSpecLifecycleEngine。本包提供的是：
  1) `build_world_state` —— 把 mapspec / map_state / observation / review /
     provenance 投影成**单一有界快照**（agent 感知与 API 的统一读模型）；
  2) `apply_gis_mutation` —— 所有 mutation 的**统一入口门面**：origin 策略
     （user interaction wins 的服务端强制）、provenance 记录、CAS 语义
     透传——底层一次调用 engine，绝不双写。
- Zero Big Data in Context：快照绝不携带 FeatureCollection / raster payload，
  只携带 ref/描述/计数字段（有界）。
- provenance 是有界环形（默认 64 条/session），持久在 map_state 的
  `_gis_provenance` 键，best-effort（失败不阻断 mutation 语义）。
"""
from app.services.gis_world_state.provenance import (
    ProvenanceEntry,
    append_provenance,
    get_provenance,
    PROVENANCE_LIMIT,
)
from app.services.gis_world_state.state import build_world_state
from app.services.gis_world_state.mutation import (
    apply_gis_mutation,
    UserPresentationGuardError,
)

__all__ = [
    "ProvenanceEntry",
    "append_provenance",
    "get_provenance",
    "PROVENANCE_LIMIT",
    "build_world_state",
    "apply_gis_mutation",
    "UserPresentationGuardError",
]
