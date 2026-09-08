"""Lakehouse disaster recovery — Spatial Lakehouse V6 (Wave 11, ADR-0118).

数据对象级 DR 组合层（不建第二存储）：

- ``verify_cube_store``：**工作目录**级完整性 —— 逐文件 digest 对照
  manifest（BlobStore 级校验在 ``data_object.verify_data_object``；
  会话工作副本是另一份现实，崩溃/篡改都可能）；
- ``repair_cube_store``：从 BlobStore 修复（``materialize_data_object``
  先全量验真后写盘 —— 半对象比无对象危险）；
- ``backup_cube_chunks``：chunk 文件 → BlobStore binary（内容寻址天然
  去重；未变 chunk 重备份 = CAS 命中零拷贝）；
- ``scan_missing_objects`` / ``scan_orphan_manifests``：BlobStore 枚举
  vs 引用集（破坏性清扫仍归 artifact_lifecycle/promotion GC 闸 —— 本
  模块只提供只读扫描与证据）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from app.services.lakehouse.data_object import (
    DataObjectError,
    is_data_object_id,
    materialize_data_object,
    resolve_data_object,
)
from app.services.lakehouse.cube_store import (
    collect_cube_entries,
)

logger = logging.getLogger(__name__)


class DRVerifyError(ValueError):
    """DR 校验前提不成立（store 缺失 / manifest 缺失）。"""

    code = "LAKEHOUSE_DR_UNVERIFIABLE"


def verify_cube_store(
    store_dir: Union[str, Path],
    *,
    data_object_id: str = "",
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """工作 cube store vs manifest 的逐文件 digest 比对。

    返回 ``{"state": "verified"|"corrupt"|"missing_files"|"unverifiable",
    "corrupt": [...], "missing": [...], "checked": n}``。
    """
    if manifest is None:
        if not is_data_object_id(data_object_id):
            raise DRVerifyError("need a manifest or a valid data_object_id")
        manifest = resolve_data_object(data_object_id)
        if manifest is None:
            raise DRVerifyError(f"manifest not found: {data_object_id[:16]}")
    base = Path(store_dir)
    if not base.is_dir():
        return {"state": "missing_files", "corrupt": [], "missing": ["<store>"],
                "checked": 0}
    expected = {b["path"]: str(b.get("sha256") or "")
                for b in (manifest.get("content_blobs") or [])}
    actual: Dict[str, str] = {}
    try:
        for rel, digest, _size in collect_cube_entries(base):
            actual[rel] = digest
    except Exception as e:  # noqa: BLE001 — 清点失败 = 不可验证（诚实）
        raise DRVerifyError(f"cannot inventory store: {e}") from e

    corrupt: List[str] = []
    missing: List[str] = []
    for rel, digest in expected.items():
        if rel not in actual:
            missing.append(rel)
        elif digest and actual[rel] != digest:
            corrupt.append(rel)
    state = "verified"
    if missing:
        state = "missing_files"
    elif corrupt:
        state = "corrupt"
    return {"state": state, "corrupt": sorted(corrupt), "missing": sorted(missing),
            "checked": len(expected)}


def repair_cube_store(
    store_dir: Union[str, Path],
    *,
    data_object_id: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """损坏/缺失文件从 BlobStore 修复（验真优先；owner 校验强制）。"""
    base = Path(store_dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
        written = materialize_data_object(
            data_object_id, base,
            owner_session_id=session_id,
            owner_project_id=project_id,
        )
    except DataObjectError as e:
        logger.warning("[lakehouse.dr] repair refused for %s: %s", store_dir, e)
        return {"repaired": False, "reason": str(e)}
    return {"repaired": bool(written), "files": len(written)}


def backup_cube_chunks(
    store_dir: Union[str, Path],
    *,
    max_total_bytes: int = 512 * 1024 * 1024,
) -> Dict[str, Any]:
    """cube 工作副本的 chunk 文件 → BlobStore binary（内容寻址，CAS 去重）。

    预算内整备；超预算诚实 ``{"backed_up": False, "reason": "oversized"}``。
    """
    from app.services.durable_blob_store import get_filesystem_blob_store

    base = Path(store_dir)
    if not base.is_dir():
        return {"backed_up": False, "reason": "store_missing"}
    entries = collect_cube_entries(base)
    total = sum(n for _p, _d, n in entries)
    if total > max_total_bytes:
        return {"backed_up": False, "reason": "oversized", "byte_size": total}
    store = get_filesystem_blob_store()
    written = 0
    for rel, digest, _size in entries:
        result = store.put_blob(digest, (base / rel).read_bytes(), "binary")
        if result.put_new:
            written += 1
    return {
        "backed_up": True,
        "blobs": len(entries),
        "new_blobs": written,  # 其余 = 去重命中
        "byte_size": total,
    }


def scan_missing_objects(data_object_ids: Iterable[str]) -> Dict[str, Any]:
    """manifest 缺失 / blob 缺失扫描（只读；返回逐对象状态）。"""
    from app.services.lakehouse.data_object import verify_data_object

    report: Dict[str, str] = {}
    for did in data_object_ids:
        try:
            report[str(did)] = verify_data_object(str(did))
        except Exception as e:  # noqa: BLE001 — 单对象失败不阻断扫描
            report[str(did)] = f"error:{e}"
    return {
        "checked": len(report),
        "missing": sorted(k for k, v in report.items() if v != "verified"),
        "states": report,
    }


def scan_orphan_manifests(*, referenced_ids: Iterable[str]) -> List[str]:
    """BlobStore 里的 lakehouse manifest 枚举 vs 引用集 → 未见引用的 id。

    只读证据（破坏性清扫仍归既有 GC 闸）；解析失败/非 manifest 的 blob
    不算孤儿（不是 lakehouse 的东西，不归本扫描判生死）。
    """
    import json

    from app.services.durable_blob_store import get_filesystem_blob_store

    referenced = {str(r) for r in referenced_ids}
    store = get_filesystem_blob_store()
    orphans: List[str] = []
    for key, path in store.iter_blob_files():
        if key in referenced or len(key) != 64:
            continue
        if path.suffix != ".json":
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 不可解析 = 非 manifest
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("schema_version") == 1 and candidate.get("kind") in (
            "vector_parquet", "cog_raster", "zarr_cube",
        ) and isinstance(candidate.get("content_blobs"), list):
            if is_data_object_id(key):
                orphans.append(key)
    return sorted(orphans)
