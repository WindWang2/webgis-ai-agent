"""Survey store — DATA_DIR JSON 原子存储 + revision CAS（WP3 持久化）。

先例：field_compute receipt.py（per-session JSON + 原子替换 + 稳定键）
与 modelops evaluations 目录（owner scope 分层）。**不加 DB 表**——
5 线并行下迁移是冲突热点；调查状态用文件事实源 + 乐观锁足够
（单机产品语义，与 modelops artifact 同级）。

布局：
  <root>/<owner_kind>-<owner_id>/surveys/<survey_id>/design.json
                                                   /samples.json
                                                   /records.jsonl   (append-only)
                                                   /state.json      (revision CAS)
                                                   /estimates/<est_id>.json
  <root>/<owner_kind>-<owner_id>/frames/<frame_digest>.json

- ``state.json`` 持单调 revision 计数器（CAS：expected_revision 不符
  → SURVEY_REVISION_CONFLICT）；records.jsonl append-only（历史保留）；
- 写全走 tmp + os.replace 原子替换；append 走行级追加（进程内锁）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.survey_sampling.contracts import SurveySamplingError

logger = logging.getLogger(__name__)


def _storage_root() -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR.resolve() / "survey_sampling"


def owner_dir(owner_scope: Dict[str, str]) -> Path:
    """owner_scope（恰好一维、值非空）→ 存储目录（fail-closed）。"""
    keys = sorted(owner_scope.items())
    if len(keys) != 1 or keys[0][0] not in ("session_id", "project_id"):
        raise SurveySamplingError(
            "UNAUTHORIZED",
            "owner_scope must be exactly one of session_id/project_id",
            status_code=403)
    kind, value = keys[0]
    if not value:
        raise SurveySamplingError(
            "UNAUTHORIZED",
            f"owner_scope.{kind} must be non-empty",
            status_code=403)
    return _storage_root() / f"{kind}-{value}"


import re as _re

#: 合法 digest/estimate_id：hex 16..64（uuid4 hex16 / sha256 hex64 覆盖）
_SAFE_ID_RE = _re.compile(r"^[0-9a-f]{16,64}$")


def _safe_id(value: str, what: str) -> str:
    """digest/estimate_id 白名单（防路径注入/跨 owner 读取，R1 P1-1）。"""
    if not _SAFE_ID_RE.match(value or ""):
        raise SurveySamplingError(
            "SURVEY_NOT_FOUND",
            f"invalid {what} (expected 16..64 hex chars)",
            status_code=404)
    return value


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


_LOCKS: Dict[str, asyncio.Lock] = {}


def _survey_lock(survey_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(survey_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[survey_id] = lock
    return lock


def save_frame_payload(owner_scope: Dict[str, str], payload: Dict[str, Any]) -> str:
    """冻结框 payload → frames/<digest>.json（content-addressed 幂等）。"""
    digest = payload["frame_digest"]
    path = owner_dir(owner_scope) / "frames" / f"{digest}.json"
    if not path.exists():                      # CAS 内容寻址：存在即同内容
        _atomic_write_json(path, payload)
    return digest


def get_frame_payload(owner_scope: Dict[str, str], digest: str) -> Optional[Dict[str, Any]]:
    _safe_id(str(digest), "frame_digest")     # R1 P1-1：路径注入阻断
    return _read_json(owner_dir(owner_scope) / "frames" / f"{digest}.json")


def list_frame_payloads(owner_scope: Dict[str, str]) -> List[Dict[str, Any]]:
    frames = owner_dir(owner_scope) / "frames"
    if not frames.exists():
        return []
    out = []
    for p in sorted(frames.glob("*.json")):
        payload = _read_json(p)
        if payload:
            out.append(payload)
    return out


def _survey_dir(owner_scope: Dict[str, str], survey_id: str) -> Path:
    if not survey_id or any(c in survey_id for c in "/\\."):
        raise SurveySamplingError(
            "SURVEY_NOT_FOUND", f"invalid survey id {survey_id!r}",
            status_code=404)
    return owner_dir(owner_scope) / "surveys" / survey_id


def save_survey_document(
    owner_scope: Dict[str, str], survey_id: str,
    filename: str, payload: Dict[str, Any],
) -> None:
    """design.json / samples.json 写入（原子；filename 白名单）。"""
    if filename not in ("design.json", "samples.json", "state.json"):
        raise ValueError(f"filename {filename!r} not writable here")
    _atomic_write_json(_survey_dir(owner_scope, survey_id) / filename, payload)


def get_survey_document(
    owner_scope: Dict[str, str], survey_id: str, filename: str,
) -> Optional[Dict[str, Any]]:
    if filename not in ("design.json", "samples.json", "state.json"):
        raise ValueError(f"filename {filename!r} not readable here")
    return _read_json(_survey_dir(owner_scope, survey_id) / filename)


async def append_records(
    owner_scope: Dict[str, str], survey_id: str,
    records: List[Dict[str, Any]],
) -> None:
    """调查记录 append-only 追加（进程内锁 + 行级 JSONL）。"""
    async with _survey_lock(survey_id):
        def _append() -> None:
            path = _survey_dir(owner_scope, survey_id) / "records.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False,
                                       separators=(",", ":")) + "\n")
        await asyncio.to_thread(_append)


def load_records(owner_scope: Dict[str, str], survey_id: str) -> List[Dict[str, Any]]:
    path = _survey_dir(owner_scope, survey_id) / "records.jsonl"
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("survey %s: skipping corrupt record line",
                               survey_id)
    return out


async def append_and_assign_versions(
    owner_scope: Dict[str, str], survey_id: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """追加记录并在**同一把锁内**分配版本号（R1 P2-2：版本竞态消除）。

    ``rows`` 携带除 ``version`` 外的全部字段；返回带分配版本的副本
    （append-only 顺序 = 调用顺序）。
    """
    async with _survey_lock(survey_id):
        def _append() -> List[Dict[str, Any]]:
            max_versions: Dict[str, int] = {}
            for rec in load_records(owner_scope, survey_id):
                sid = str(rec.get("sample_id"))
                max_versions[sid] = max(max_versions.get(sid, 0),
                                        int(rec.get("version", 0)))
            assigned: List[Dict[str, Any]] = []
            path = _survey_dir(owner_scope, survey_id) / "records.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for row in rows:
                    sid = str(row.get("sample_id"))
                    version = max_versions.get(sid, 0) + 1
                    max_versions[sid] = version
                    rec = dict(row)
                    rec["version"] = version
                    assigned.append(rec)
                    f.write(json.dumps(rec, ensure_ascii=False,
                                       separators=(",", ":")) + "\n")
            return assigned
        return await asyncio.to_thread(_append)


async def append_and_bump(
    owner_scope: Dict[str, str], survey_id: str,
    rows: List[Dict[str, Any]], *,
    expected_revision: Optional[int],
    note: str = "",
    allow_missing_state: bool = False,
) -> tuple:
    """单一临界区：CAS 校验 → 版本分配 → 追加 → revision 推进。

    R1 P2-2：此前 CAS 在 append 之后——校验失败会留下"已落库但 revision
    未推进"的孤儿记录。本函数保证要么全部生效、要么全部不发生。
    返回 (assigned_rows, new_revision)。
    """
    async with _survey_lock(survey_id):
        def _atomic() -> tuple:
            path = _survey_dir(owner_scope, survey_id) / "state.json"
            state = _read_json(path) or {}
            current = int(state.get("revision", 0))
            if expected_revision is None:
                if current != 0 and not allow_missing_state:
                    raise SurveySamplingError(
                        "SURVEY_REVISION_CONFLICT",
                        "expected_revision is required once the survey "
                        f"exists (current revision {current})",
                        status_code=409,
                        details={"current_revision": current},
                        correction_hint="GET the survey state first, then "
                                        "retry with the fresh "
                                        "expected_revision")
            elif expected_revision != current:
                raise SurveySamplingError(
                    "SURVEY_REVISION_CONFLICT",
                    f"expected_revision {expected_revision} != current "
                    f"{current} — reload the survey and re-apply",
                    status_code=409,
                    details={"current_revision": current},
                    correction_hint="GET the survey state first, then "
                                    "retry with the fresh expected_revision")
            # CAS 通过 → 版本分配 + 追加
            max_versions: Dict[str, int] = {}
            for rec in load_records(owner_scope, survey_id):
                sid = str(rec.get("sample_id"))
                max_versions[sid] = max(max_versions.get(sid, 0),
                                        int(rec.get("version", 0)))
            assigned: List[Dict[str, Any]] = []
            rec_path = _survey_dir(owner_scope, survey_id) / "records.jsonl"
            rec_path.parent.mkdir(parents=True, exist_ok=True)
            with open(rec_path, "a", encoding="utf-8") as f:
                for row in rows:
                    sid = str(row.get("sample_id"))
                    version = max_versions.get(sid, 0) + 1
                    max_versions[sid] = version
                    rec = dict(row)
                    rec["version"] = version
                    assigned.append(rec)
                    f.write(json.dumps(rec, ensure_ascii=False,
                                       separators=(",", ":")) + "\n")
            new_revision = current + 1
            _atomic_write_json(path, {
                "revision": new_revision,
                "updated_at": time.time(),
                "note": note,
            })
            return assigned, new_revision
        return await asyncio.to_thread(_atomic)


async def bump_revision(
    owner_scope: Dict[str, str], survey_id: str,
    expected_revision: Optional[int], *,
    note: str = "",
    allow_missing_state: bool = False,
) -> int:
    """CAS revision 推进（锁内读-比-写原子临界区）。

    ``expected_revision=None`` **只在 revision==0（尚无任何推进）时合法**
    ——既有调查必须携带真实 expected（R1 P2-2：省略即绕过 CAS 的
    fail-open 缺口）。``allow_missing_state=True`` 供创建路径使用。
    """
    async with _survey_lock(survey_id):
        def _bump() -> int:
            path = _survey_dir(owner_scope, survey_id) / "state.json"
            state = _read_json(path) or {}
            current = int(state.get("revision", 0))
            if expected_revision is None:
                if current != 0 and not allow_missing_state:
                    raise SurveySamplingError(
                        "SURVEY_REVISION_CONFLICT",
                        "expected_revision is required once the survey "
                        f"exists (current revision {current})",
                        status_code=409,
                        details={"current_revision": current},
                        correction_hint="GET the survey state first, then "
                                        "retry with the fresh "
                                        "expected_revision")
            elif expected_revision != current:
                raise SurveySamplingError(
                    "SURVEY_REVISION_CONFLICT",
                    f"expected_revision {expected_revision} != current "
                    f"{current} — reload the survey and re-apply",
                    status_code=409,
                    details={"current_revision": current},
                    correction_hint="GET the survey state first, then "
                                    "retry with the fresh expected_revision")
            new_revision = current + 1
            _atomic_write_json(path, {
                "revision": new_revision,
                "updated_at": time.time(),
                "note": note,
            })
            return new_revision
        return await asyncio.to_thread(_bump)


def get_revision(owner_scope: Dict[str, str], survey_id: str) -> int:
    state = get_survey_document(owner_scope, survey_id, "state.json") or {}
    return int(state.get("revision", 0))


def save_estimate(
    owner_scope: Dict[str, str], survey_id: str,
    estimate_id: str, payload: Dict[str, Any],
) -> None:
    _safe_id(str(estimate_id), "estimate_id")
    path = (_survey_dir(owner_scope, survey_id) / "estimates"
            / f"{estimate_id}.json")
    _atomic_write_json(path, payload)


def get_estimate(
    owner_scope: Dict[str, str], survey_id: str, estimate_id: str,
) -> Optional[Dict[str, Any]]:
    _safe_id(str(estimate_id), "estimate_id")
    return _read_json(_survey_dir(owner_scope, survey_id) / "estimates"
                      / f"{estimate_id}.json")


def list_estimates(owner_scope: Dict[str, str], survey_id: str) -> List[Dict[str, Any]]:
    est = _survey_dir(owner_scope, survey_id) / "estimates"
    if not est.exists():
        return []
    out = []
    for p in sorted(est.glob("*.json")):
        payload = _read_json(p)
        if payload:
            out.append(payload)
    return out


def list_surveys(owner_scope: Dict[str, str]) -> List[Dict[str, Any]]:
    root = owner_dir(owner_scope) / "surveys"
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        design = _read_json(p / "design.json")
        if design:
            out.append(design)
    return out
