"""CheckpointStore — MapSpec snapshot, materialization, and rollback.

Extracted from MapSpecStore (architecture review Candidate #2). Owns the
checkpoint lifecycle: writing a self-contained snapshot (mapspec.json +
materialized ref payloads) and recovering one on rollback.

Pure-ish by design (decision ii): functions take a loaded MapSpec and a
session_dir / session_data_manager as arguments — no back-reference to the
store. Per decision p, rollback RETURNS the restored MapSpec rather than
persisting it; the store (the sole write authority) saves the result. This
concentrates the single write in one place and makes snapshot/rollback testable
without a full session fixture.
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
  """Write a self-contained checkpoint of the MapSpec.

  Copies the MapSpec doc and materializes the payload behind every ref: it
  references, so the snapshot is replayable without the live session store
  (spec Story 31). inlineData sources travel inside the mapspec doc copy.
  """
  ckpt_id = checkpoint_id or f"ckpt_{int(time.time() * 1000)}"
  ckpt_dir = session_dir / "checkpoints" / ckpt_id
  ckpt_dir.mkdir(parents=True, exist_ok=True)

  # 1. Copy the MapSpec doc (carries inlineData sources with it).
  with open(ckpt_dir / "mapspec.json", "w", encoding="utf-8") as f:
    json.dump(mapspec, f, ensure_ascii=False, indent=2)

  # 2. Materialize payloads behind ref: references (Decision 3 & Story 31).
  session_id_for_refs = session_dir.name  # session_dir is .../<session_id>
  materialized_refs: Dict[str, Any] = {}
  for source in mapspec.get("sources", {}).values():
    # source-shape knowledge routes through mapspec_source (ADR-0008). ref()
    # returns the url/dataPath string if present, else None — known overload:
    # real URLs and ref: cursors share the field; the startswith check below
    # decides which to materialize.
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
  """Recover a checkpoint. Returns the restored MapSpec; caller persists it.

  Restores materialized ref: payloads into the session store and reads back the
  snapshot's MapSpec. Does NOT save — the store owns the write (decision p).
  Returns {'success', 'mapspec'} on success or {'success': False, 'message'}.
  """
  ckpt_dir = session_dir / "checkpoints" / checkpoint_id
  if not ckpt_dir.exists():
    return {"success": False, "message": f"Checkpoint '{checkpoint_id}' not found"}

  # 1. Read the snapshot's MapSpec.
  mapspec_file = ckpt_dir / "mapspec.json"
  with open(mapspec_file, "r", encoding="utf-8") as f:
    mapspec = json.load(f)

  # 2. Restore materialized ref: payloads into the session store.
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
