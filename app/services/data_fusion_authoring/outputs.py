"""融合运行输出包 —— staging / 原子发布 / DataObject / session 台账。

单一可写副本纪律（与 G38 outputs.py 同款）：
- 终态包只落 ``DATA_DIR/data-fusion/<run_id>/`` 一处；staging → final
  原子改名；发布失败不留假完成 run（失败即 discard + typed 记录）；
- run 匹配包（pairs.json/unmatched.json/match_stats.json）在 run 完成
  时冻结；commit 包（fused_fc.json/quality_report.json/receipt.json）
  在 commit 时追加发布 —— 同目录两段式，均只写一次；
- DataObject CAS 发布三态可区分（published/oversized/failed —— 诚实
  降级，本体不受影响）；session 台账登记增值不阻断。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("data_fusion.outputs")


def fusion_root() -> Path:
    """融合终态包根（DATA_DIR/data-fusion；单一可写位置）。"""
    from app.core.config import settings

    base = Path(getattr(settings, "DATA_DIR", "./data"))
    root = base / "data-fusion"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_dir(run_id: str) -> Path:
    d = fusion_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def staging_dir(run_id: str) -> Path:
    return run_dir(run_id) / "staging"


def final_dir(run_id: str) -> Path:
    return run_dir(run_id) / "final"


def new_run_id() -> str:
    import uuid

    return uuid.uuid4().hex[:40]


def discard_staging(run_id: str) -> None:
    shutil.rmtree(staging_dir(run_id), ignore_errors=True)


def discard_final(run_id: str) -> None:
    shutil.rmtree(final_dir(run_id), ignore_errors=True)


def discard_run(run_id: str) -> None:
    shutil.rmtree(run_dir(run_id), ignore_errors=True)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── 匹配包（run 段） ─────────────────────────

def write_match_bundle(
    run_id: str,
    *,
    pairs: List[Dict[str, Any]],
    match_stats: Dict[str, Any],
    source_cards: Dict[str, Any],
    plan_snapshot: Dict[str, Any],
    side_keys: Optional[Dict[str, List[str]]] = None,
    side_briefs: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    budget_event: Optional[Dict[str, Any]] = None,
) -> Path:
    """run 完成时冻结匹配包（staging 内；commit 由 executor 原子发布）。"""
    d = staging_dir(run_id)
    _write_json(d / "pairs.json", {"pairs": pairs})
    _write_json(d / "match_stats.json", match_stats)
    _write_json(d / "source_cards.json", source_cards)
    _write_json(d / "plan_snapshot.json", plan_snapshot)
    if side_keys is not None:
        _write_json(d / "side_keys.json", side_keys)
    if side_briefs is not None:
        _write_json(d / "side_briefs.json", side_briefs)
    if budget_event:
        _write_json(d / "budget_event.json", budget_event)
    return d


def read_match_bundle(run_id: str) -> Optional[Dict[str, Any]]:
    """读取匹配包（commit/分页/审阅查询共用）。缺包 → None（typed 上层）。

    commit 后 staging 原子改名为 final —— 两处都可读（同一内容）。
    """
    d = staging_dir(run_id)
    if not (d / "pairs.json").exists():
        d2 = final_dir(run_id)
        if not (d2 / "pairs.json").exists():
            return None
        d = d2
    with open(d / "pairs.json", encoding="utf-8") as f:
        pairs = json.load(f)
    with open(d / "match_stats.json", encoding="utf-8") as f:
        stats = json.load(f)
    cards: Dict[str, Any] = {}
    if (d / "source_cards.json").exists():
        with open(d / "source_cards.json", encoding="utf-8") as f:
            cards = json.load(f)
    side_keys: Dict[str, List[str]] = {}
    if (d / "side_keys.json").exists():
        with open(d / "side_keys.json", encoding="utf-8") as f:
            side_keys = json.load(f)
    side_briefs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if (d / "side_briefs.json").exists():
        with open(d / "side_briefs.json", encoding="utf-8") as f:
            side_briefs = json.load(f)
    return {
        "pairs": pairs.get("pairs", []),
        "match_stats": stats,
        "source_cards": cards,
        "side_keys": side_keys,
        "side_briefs": side_briefs,
    }


# ───────────────────────── commit 包（发布段） ─────────────────────────

def write_commit_staging(
    run_id: str,
    *,
    feature_collection: Dict[str, Any],
    quality_report: Dict[str, Any],
    lineage: Dict[str, Any],
) -> str:
    """commit 包（除收据外）落 staging；返回 fused_fc.json 的文件字节哈希
    —— 收据哈希与核验哈希同源（先写文件后哈希，绝不用内存序列化另算）。"""
    d = staging_dir(run_id)
    _write_json(d / "fused_fc.json", feature_collection)
    _write_json(d / "quality_report.json", quality_report)
    _write_json(d / "lineage.json", lineage)
    return _sha256_file(d / "fused_fc.json")


def write_commit_receipt(run_id: str, receipt: Dict[str, Any]) -> None:
    """收据最后落盘（携带 fc_sha256 后）；发布前最后一步。"""
    _write_json(staging_dir(run_id) / "receipt.json", receipt)


def atomic_publish(run_id: str) -> Path:
    """staging → final 原子改名（commit 边界；失败不留半包）。"""
    src = staging_dir(run_id)
    dst = final_dir(run_id)
    if dst.exists():
        raise FileExistsError(f"final already published: {run_id}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return dst


def read_receipt(run_id: str) -> Optional[Dict[str, Any]]:
    p = final_dir(run_id) / "receipt.json"
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — 收据不可读 = typed 失效由上层判定
        return None


def verify_published(run_id: str) -> Dict[str, Any]:
    """内容可核验性检查（幂等复用前的 J28 同款纪律）。"""
    d = final_dir(run_id)
    fc = d / "fused_fc.json"
    receipt_p = d / "receipt.json"
    if not fc.exists() or not receipt_p.exists():
        raise FileNotFoundError("commit package incomplete")
    receipt = read_receipt(run_id) or {}
    sha = _sha256_file(fc)
    declared = receipt.get("fc_sha256")
    if declared and declared != sha:
        raise ValueError(f"fc sha mismatch: {sha} != {declared}")
    return {"sha256": sha, "receipt": receipt}


def load_output_content(run_id: str) -> Optional[Dict[str, Any]]:
    p = final_dir(run_id) / "fused_fc.json"
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def result_page(run_id: str, cursor: int, limit: int) -> Dict[str, Any]:
    """融合结果分页（读权威文件；稳定序 = 组装序）。"""
    fc = load_output_content(run_id)
    if fc is None:
        raise FileNotFoundError("output not published")
    feats = fc.get("features") or []
    start = max(0, int(cursor))
    end = min(len(feats), start + max(1, int(limit)))
    return {
        "features": feats[start:end],
        "cursor": end,
        "total": len(feats),
        "has_more": end < len(feats),
    }


# ───────────────────────── durable 发布 ─────────────────────────

def publish_data_object_for_run(
    run_id: str,
    *,
    owner_scope: Dict[str, str],
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """commit 包 → durable DataObject（CAS；单一内容后端）。"""
    from app.services.lakehouse.data_object import (
        DataObjectTooLargeError,
        publish_data_object,
    )

    d = final_dir(run_id)
    files = {
        p.name: p for p in sorted(d.iterdir())
        if p.is_file() and p.name not in ("manifest.json",)
    }
    if not files:
        return {"state": "failed", "error": "no output files"}
    try:
        identity = publish_data_object(
            files,
            kind="data_fusion",
            owner_scope=owner_scope,
            payload={"run_id": run_id},
            producer={"tool": "data_fusion_authoring", "run_id": run_id},
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
        )
        return {
            "state": "published",
            "data_object_id": identity.data_object_id,
            "manifest_location": identity.manifest_location,
            "content_sha256": identity.content_sha256,
            "byte_size": identity.byte_size,
            "deduped": identity.deduped,
        }
    except DataObjectTooLargeError:
        return {"state": "oversized"}
    except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
        logger.warning("[data-fusion] data object publish failed for %s: %s",
                       run_id, e)
        return {"state": "failed", "error": str(e)[:200]}


async def register_session_artifact(
    session_id: str, run_id: str, descriptor: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Optional[str]:
    """session 台账登记（ref:data-fusion/<run_id>；增值记录，绝不阻断）。"""
    try:
        from app.services.artifact_registry import register_artifact

        ref = f"ref:data-fusion/{run_id}"
        await register_artifact(
            session_id,
            artifact_id=ref,
            artifact_type="data_fusion",
            producer_capability="data_fusion_authoring",
            producer_tool="data_fusion_commit",
            descriptor=descriptor,
            metadata=metadata,
        )
        return ref
    except Exception as e:  # noqa: BLE001
        logger.warning("[data-fusion] ledger registration skipped for %s: %s",
                       run_id, e)
        return None
