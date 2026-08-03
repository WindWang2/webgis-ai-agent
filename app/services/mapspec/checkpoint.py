"""CheckpointStore (app/services/mapspec/checkpoint.py).

拥有 MapSpec 各种 Checkpoint Snapshot、Ref Materialization 与 Rollback 逻辑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.mapspec_source import ref as source_ref


async def snapshot(
    mapspec: Dict[str, Any],
    session_dir: Path,
    session_data_manager,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    """生成 MapSpec Checkpoint Snapshot 及其引用的 ref 数据物理副本"""
    ckpt_id = checkpoint_id or f"ckpt_{int(time.time() * 1000)}"
    ckpt_dir = session_dir / "checkpoints" / ckpt_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    with open(ckpt_dir / "mapspec.json", "w", encoding="utf-8") as f:
        json.dump(mapspec, f, ensure_ascii=False, indent=2)

    session_id_for_refs = session_dir.name
    materialized_refs: Dict[str, Any] = {}
    for source in mapspec.get("sources", {}).values():
        ref_candidate = source_ref(source) or ""
        if isinstance(ref_candidate, str) and ref_candidate.startswith("ref:"):
            ref_data = await session_data_manager.get(session_id_for_refs, ref_candidate)
            if ref_data is not None:
                materialized_refs[ref_candidate] = ref_data

    with open(ckpt_dir / "materialized_refs.json", "w", encoding="utf-8") as f:
        json.dump(materialized_refs, f, ensure_ascii=False, indent=2)

    meta = {
        "checkpoint_id": ckpt_id,
        "timestamp": time.time(),
        "ref_count": len(materialized_refs),
    }
    with open(ckpt_dir / "checkpoint_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "checkpoint_id": ckpt_id,
        "checkpoint_dir": str(ckpt_dir),
        "ref_count": len(materialized_refs),
        "summary": f"Checkpoint '{ckpt_id}' created with {len(materialized_refs)} materialized refs",
    }


async def rollback(
    session_dir: Path,
    checkpoint_id: str,
    session_data_manager,
) -> Dict[str, Any]:
    """回滚恢复 Checkpoint Snapshot"""
    ckpt_dir = session_dir / "checkpoints" / checkpoint_id
    if not ckpt_dir.exists():
        return {"success": False, "message": f"Checkpoint '{checkpoint_id}' not found"}

    mapspec_file = ckpt_dir / "mapspec.json"
    with open(mapspec_file, "r", encoding="utf-8") as f:
        mapspec = json.load(f)

    session_id_for_refs = session_dir.name
    refs_file = ckpt_dir / "materialized_refs.json"
    if refs_file.exists():
        with open(refs_file, "r", encoding="utf-8") as f:
            refs_data = json.load(f)
            for ref_id, payload in refs_data.items():
                await session_data_manager.overwrite(session_id_for_refs, ref_id, payload)

    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "mapspec": mapspec,
        "summary": f"Recovered checkpoint '{checkpoint_id}'",
    }
