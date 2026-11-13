"""base 快照证据 —— 提案基础的不可变 BlobStore CAS 引用（G32 WP03）。

提案的 base 不是"当时的 session ref"（随时会被后续编辑改写），而是创建
时刻载荷的 canonical JSON 快照：BlobStore.put_json 内容寻址（sha256 key，
put-if-absent，多提案共享同一 base 零重复存储）+ 记录 content_revision /
fingerprint（G14 同源指纹）。读取时 digest 校验 —— 不符返回 None（typed
SnapshotUnavailable），绝不静默顶替。

首次未物化 `_wfid` 的源：快照保留未物化形态；候选求值对快照做确定性
`materialize_wfids`（同 G14 preview 路径）。当前源若已按**同一 base 指纹**
物化，身份可证一致；否则（源被另一次真实编辑物化）拒绝自动 rebase
（conflicts.identity_unprovable）—— 见 conflicts.py。
"""
from __future__ import annotations

import json
from typing import Any, Dict

from app.schemas.feature_proposal_schema import BaseEvidence
from app.services.feature_proposals.contracts import (
    MAX_SNAPSHOT_BYTES,
    SnapshotUnavailable,
)


def _blob_store():
    from app.services.durable_blob_store import get_filesystem_blob_store

    return get_filesystem_blob_store()


def capture_base_snapshot(
    session_id: str,
    ref_id: str,
    payload: Any,
    *,
    content_revision: int,
    fingerprint: str,
    materialized: bool,
) -> BaseEvidence:
    """把当前载荷写入 BlobStore CAS，返回精确 base 证据。

    超过 MAX_SNAPSHOT_BYTES 的载荷 typed 拒绝（editable dataset ≤20k 要素，
    正常远低于此 —— 超限是异常形态，诚实拒绝而不是截断）。
    """
    from app.lib.data.fingerprints import canonical_dumps

    try:
        data = canonical_dumps(payload).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotUnavailable(
            "snapshot_not_serializable", f"payload cannot be canonically serialized: {exc}"
        )
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise SnapshotUnavailable(
            "snapshot_too_large",
            "base snapshot exceeds the proposal budget",
            {"bytes": len(data), "budget": MAX_SNAPSHOT_BYTES},
        )
    store = _blob_store()
    import time

    key, sha = store.put_json(payload)
    return BaseEvidence(
        ref_id=ref_id,
        content_revision=int(content_revision),
        fingerprint=str(fingerprint),
        snapshot_key=key,
        snapshot_sha256=sha,
        snapshot_bytes=len(data),
        materialized=bool(materialized),
        captured_at=time.time(),
    )


def load_base_snapshot(base: BaseEvidence) -> Any:
    """读取 base 快照并校验 digest —— 不符即 typed 拒绝（绝不顶替）。"""
    store = _blob_store()
    data = store.get_blob(base.snapshot_key, expected_sha256=base.snapshot_sha256)
    if data is None:
        # digest 不符或对象消失（CAS key 即内容摘要，key 在 = 内容必符；
        # 仍可能被外部删除 —— 两者都不可恢复）。
        raise SnapshotUnavailable(
            "snapshot_unavailable",
            "base snapshot missing or digest mismatch — cannot verify proposal base",
            {"snapshot_key": base.snapshot_key, "ref_id": base.ref_id},
        )
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SnapshotUnavailable(
            "snapshot_corrupt", f"base snapshot bytes are not valid JSON: {exc}"
        )


def base_snapshot_exists(base: BaseEvidence) -> bool:
    store = _blob_store()
    return store.exists(base.snapshot_key)


def snapshot_identity_summary(base: BaseEvidence) -> Dict[str, Any]:
    """有界证据投影（不搬载荷）。"""
    return {
        "ref_id": base.ref_id,
        "content_revision": base.content_revision,
        "fingerprint": base.fingerprint,
        "snapshot_key": base.snapshot_key,
        "snapshot_sha256": base.snapshot_sha256,
        "snapshot_bytes": base.snapshot_bytes,
        "materialized": base.materialized,
        "captured_at": base.captured_at,
    }
