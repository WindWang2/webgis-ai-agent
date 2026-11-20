"""G46 同代冻结 —— 薄 re-export（不复制 G41 的冻结实现）。

复用 ``bivariate_reliability_authoring.snapshot`` 的 ``freeze_source``/
``extract_feature_collection``/``content_fingerprint``：强版本 descriptor
双查 + session_lock + 单次整份深拷贝 + ABA 拒绝。本域把它们作为
``linked_exploration`` 的快照合同直接消费（WP03：所有图表、地图过滤、
匹配计数、表格与导出引用同一 source+revision+fingerprint）。
"""
from __future__ import annotations

from app.services.bivariate_reliability_authoring.errors import AuthoringError
from app.services.bivariate_reliability_authoring.snapshot import (
    FrozenSource,
    freeze_source,
)
from app.services.linked_exploration.errors import LinkedExplorationError

__all__ = ["FrozenSource", "freeze_source", "freeze_source_typed"]


async def freeze_source_typed(
    session_id: str,
    ref_id: str,
    *,
    owner_token: str | None = None,
    trusted_dispatch: bool = False,
) -> FrozenSource:
    """freeze_source + 域错误适配（AuthoringError → LinkedExplorationError）。

    保留原错误码（NO_STRONG_REVISION / SOURCE_MUTATION_DURING_READ /
    SOURCE_UNAVAILABLE / SOURCE_NOT_FEATURECOLLECTION / SOURCE_TOO_LARGE）——
    它们就是本域对源冻结的 typed 失败面。
    """
    try:
        return await freeze_source(
            session_id, ref_id,
            owner_token=owner_token, trusted_dispatch=trusted_dispatch)
    except AuthoringError as e:
        raise LinkedExplorationError(
            e.code, e.message, status_code=e.status_code,
            correction_hint=e.correction_hint, detail=e.detail) from e
