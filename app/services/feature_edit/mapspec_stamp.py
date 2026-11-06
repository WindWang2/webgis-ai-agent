"""MapSpec 数据新鲜度盖章的调用面（feature_edit → gis_world_state 门面）。

孤立此模块是为了让 executor 不在 import 期耦合 mapspec 栈；盖章语义
全部经 RefreshDataRevisionIntent（mutation_registry 单一登记点）。
"""
from __future__ import annotations

from typing import Any

from app.services.mapspec.intents import RefreshDataRevisionIntent


async def apply_data_revision_stamp(
    session_id: str, ref_id: str, content_revision: int
) -> Any:
    """把 ref 的新 content_revision 盖到引用它的 MapSpec 源上。

    origin="system"（服务端内部维护动作，非用户/agent 决策），无
    expected_revision（不与用户 CAS 竞争 —— 戳是派生事实，晚到无害）。
    """
    from app.services.gis_world_state import apply_gis_mutation

    return await apply_gis_mutation(
        session_id,
        RefreshDataRevisionIntent(ref_id=ref_id, content_revision=int(content_revision)),
        origin="system",
        actor="feature_edit",
        reason=f"feature-edit stamp ref={ref_id[:48]} rev={content_revision}",
    )
