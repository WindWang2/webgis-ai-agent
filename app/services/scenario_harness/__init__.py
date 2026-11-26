"""Scenario Harness 公共面（G05）。

分层（防重复施工）：
- 契约 → ``contracts.py``（状态词表/基准/锁定/采纳/对比 schema）；
- 注册表 → ``store.py``（父会话 map_state 分键存取，持锁写）；
- 用户锁定 → ``locks.py``（显式 pin + 释放审计）；
- 共享基准/候选生命周期 → ``baseline.py`` / ``candidates.py``；
- 基准漂移失效 → ``staleness.py``；
- 选择性采纳 → ``adopt.py``；
- 情境投影 → ``projection.py``（situation additive facts / 指代解析）。

红线：隔离机制只有 whatif 分支一套；权威状态只有父会话 MapSpec 一份；
注册表零 payload。
"""

from app.services.scenario_harness.contracts import (
    AdoptionItem,
    AdoptionReceipt,
    AdoptSelection,
    BaselineDatasetAnchor,
    BaselineMove,
    CandidateDiffRow,
    CandidateImpact,
    CandidateState,
    DeltaGroup,
    DeltaIntent,
    ImpactLevel,
    LockKind,
    ScenarioBaseline,
    ScenarioCandidate,
    ScenarioCompareResult,
    ScenarioLock,
    group_of_kind,
)
from app.services.scenario_harness.locks import (
    release_lock,
    set_lock,
    list_active_locks,
    find_blocking_lock,
)
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    load_registry,
    mint_id,
    utc_now_iso,
)

__all__ = [
    "AdoptionItem",
    "AdoptionReceipt",
    "AdoptSelection",
    "BaselineDatasetAnchor",
    "BaselineMove",
    "CandidateDiffRow",
    "CandidateImpact",
    "CandidateState",
    "DeltaGroup",
    "DeltaIntent",
    "ImpactLevel",
    "LockKind",
    "RegistryError",
    "ScenarioBaseline",
    "ScenarioCandidate",
    "ScenarioCompareResult",
    "ScenarioLock",
    "find_blocking_lock",
    "group_of_kind",
    "list_active_locks",
    "load_registry",
    "mint_id",
    "get_registry_entry",
    "release_lock",
    "set_lock",
    "utc_now_iso",
]
