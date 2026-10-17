"""ProjectRuntimeStore —— 项目域 runtime snapshot 存储（ADR-0220 §6/§7）。

- 家：``DATA_DIR/workspaces/<project_id>/runtime-snapshots/``（项目域，
  在 mapspec 会话清扫的 ``workspaces`` 白名单之下 —— 不随会话 TTL 蒸发）；
- 原子写（tmp + replace）：快照要么完整落盘，要么不存在 —— 绝无半成品；
- 有界保留（每项目 ≤25，最旧先删，镜像 workspace snapshot 的 cap 纪律）；
- resume lease：同 snapshot 并发 resume 的第二请求 fail-fast（诚实冲突，
  绝不 last-write-wins）；
- schema fail-closed：读路径遇到更新 schema_version 的 manifest 返回
  ``unsupported_schema``（原样数据带回给 validation 层判定，不投机升级）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_runtime.contract import (
    RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    ProjectRuntimeSnapshotV1,
)

logger = logging.getLogger(__name__)

#: 每项目 runtime snapshot 保留上限（runtime snapshot 语义上重于 workspace
#: snapshot —— 每个都承诺一个可验证恢复面；25 与 workspace 50 相比更保守）。
MAX_RUNTIME_SNAPSHOTS_PER_PROJECT = 25

#: project/snapshot id 白名单（与 workspace snapshot 同款 charset 纪律）。
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SNAPSHOT_ID_RE = re.compile(r"^prs-[A-Za-z0-9_-]{1,64}$")

#: resume lease TTL（同 snapshot 并发 resume 的 fail-fast 窗口）。
RESUME_LEASE_TTL_SECONDS = 300


def validate_project_id(project_id: str) -> bool:
    return bool(_PROJECT_ID_RE.match(project_id or ""))


def validate_snapshot_id(snapshot_id: str) -> bool:
    return bool(_SNAPSHOT_ID_RE.match(snapshot_id or ""))


def runtime_snapshots_dir(project_id: str) -> Optional[Path]:
    if not validate_project_id(project_id):
        return None
    from app.services.workspace.snapshot import workspaces_root

    return workspaces_root() / project_id / "runtime-snapshots"


def _snapshot_path(project_id: str, snapshot_id: str) -> Optional[Path]:
    if not validate_project_id(project_id) or not validate_snapshot_id(snapshot_id):
        return None
    directory = runtime_snapshots_dir(project_id)
    if directory is None:
        return None
    return directory / f"{snapshot_id}.json"


class UnsupportedSnapshotSchema(Exception):
    """manifest 的 schema_version 超出本进程可理解范围（fail-closed）。

    携带原始 dict 供 validation 层产出诚实的 unsupported_schema 报告 ——
    读路径绝不投机升级或丢弃字段。
    """

    def __init__(self, raw: Dict[str, Any], schema_version: Any) -> None:
        super().__init__(
            f"runtime snapshot schema_version={schema_version!r} unsupported "
            f"(supported: <= {RUNTIME_SNAPSHOT_SCHEMA_VERSION})"
        )
        self.raw = raw
        self.schema_version = schema_version


def _atomic_write_json_sync(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json_sync(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class ProjectRuntimeStore:
    """runtime snapshot 的落盘 / 读取 / 清单 / 删除 / lease 原语。"""

    async def save(self, snapshot: ProjectRuntimeSnapshotV1) -> bool:
        """原子写 manifest（要么完整，要么不存在）。"""
        path = _snapshot_path(snapshot.project_id, snapshot.snapshot_id)
        if path is None:
            return False
        try:
            await asyncio.to_thread(
                _atomic_write_json_sync, path, snapshot.model_dump(mode="json")
            )
        except OSError as e:
            logger.warning(
                "[RuntimeSnapshot] disk write failed project=%s: %s",
                snapshot.project_id, e,
            )
            return False
        await self._enforce_cap(snapshot.project_id)
        return True

    async def _enforce_cap(self, project_id: str) -> None:
        directory = runtime_snapshots_dir(project_id)
        if directory is None:
            return

        def _cap() -> None:
            if not directory.is_dir():
                return
            now = time.time()
            for tmp in directory.glob("*.tmp-*"):
                try:
                    if now - tmp.stat().st_mtime > 3600:
                        tmp.unlink()
                except OSError:
                    continue
            files = sorted(
                directory.glob("prs-*.json"), key=lambda p: p.stat().st_mtime
            )
            overflow = len(files) - MAX_RUNTIME_SNAPSHOTS_PER_PROJECT
            for f in files[: max(0, overflow)]:
                try:
                    f.unlink()
                except OSError:
                    continue

        try:
            await asyncio.to_thread(_cap)
        except OSError:
            pass

    def _load_raw(self, project_id: str, snapshot_id: str) -> Optional[dict]:
        path = _snapshot_path(project_id, snapshot_id)
        if path is None or not path.is_file():
            return None
        return _read_json_sync(path)

    def load(
        self, project_id: str, snapshot_id: str
    ) -> Optional[ProjectRuntimeSnapshotV1]:
        """读取 + schema 校验（fail-closed：更新版本抛 UnsupportedSnapshotSchema）。"""
        raw = self._load_raw(project_id, snapshot_id)
        if raw is None:
            return None
        version = raw.get("schema_version")
        try:
            version_int = int(version)
        except (TypeError, ValueError):
            version_int = -1
        if version_int > RUNTIME_SNAPSHOT_SCHEMA_VERSION:
            raise UnsupportedSnapshotSchema(raw, version)
        try:
            return ProjectRuntimeSnapshotV1.model_validate(raw)
        except Exception:  # noqa: BLE001 — 损坏/字段漂移按不存在处理（诚实）
            logger.warning(
                "[RuntimeSnapshot] manifest invalid project=%s snapshot=%s",
                project_id, snapshot_id,
            )
            return None

    async def list_snapshots(
        self, project_id: str
    ) -> List[Dict[str, Any]]:
        """有界清单（最旧 → 最新；≤25；摘要字段，不解析重载入模型）。"""

        def _list() -> List[Dict[str, Any]]:
            directory = runtime_snapshots_dir(project_id)
            if directory is None or not directory.is_dir():
                return []
            out: List[Dict[str, Any]] = []
            for f in sorted(
                directory.glob("prs-*.json"), key=lambda p: p.stat().st_mtime
            ):
                data = _read_json_sync(f)
                if not isinstance(data, dict):
                    continue
                out.append({
                    "snapshot_id": str(data.get("snapshot_id") or f.stem),
                    "label": str(data.get("label") or ""),
                    "created_at": data.get("created_at"),
                    "freeze_reason": str(data.get("freeze_reason") or ""),
                    "session_id": str(data.get("session_id") or ""),
                    "sources": len(data.get("source_bindings") or []),
                    "datasets": len(data.get("datasets") or []),
                    "schema_version": int(data.get("schema_version") or 1),
                    "workspace_snapshot_id": str(
                        data.get("workspace_snapshot_id") or ""),
                })
            return out[:MAX_RUNTIME_SNAPSHOTS_PER_PROJECT]

        try:
            return await asyncio.to_thread(_list)
        except OSError:
            return []

    async def delete(
        self, project_id: str, snapshot_id: str, *, owner_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """定点删除（仅 unlink manifest；blob 归 artifact lifecycle GC 引用计数）。"""
        path = _snapshot_path(project_id, snapshot_id)
        if path is None or not path.is_file():
            return None
        raw = _read_json_sync(path) or {}
        created_by = str(raw.get("created_by") or "")
        # 非本人创建：拒绝（不删别人的项目资产；路由侧已做项目鉴权，
        # 此处是服务侧第二道守卫，与 workspace snapshot delete 同款）。
        if created_by and owner_id and created_by != str(owner_id):
            return None
        try:
            await asyncio.to_thread(path.unlink)
        except OSError:
            return None
        lease = path.with_name(path.name + ".resume-lease")
        try:
            await asyncio.to_thread(lease.unlink, True)
        except OSError:
            pass
        return {
            "snapshot_id": snapshot_id,
            "deleted": True,
            "durable_pointers": sum(
                1 for b in (raw.get("source_bindings") or [])
                if isinstance(b, dict) and b.get("durable_pointer")
            ),
        }

    # ── resume lease（并发 resume fail-fast）────────────────────────────

    def _lease_path(self, project_id: str, snapshot_id: str) -> Optional[Path]:
        path = _snapshot_path(project_id, snapshot_id)
        return path.with_name(path.name + ".resume-lease") if path else None

    def acquire_resume_lease(
        self, project_id: str, snapshot_id: str
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """O_EXCL 创建 lease；新鲜 lease 在场 → (False, holder) 诚实冲突。"""
        lease = self._lease_path(project_id, snapshot_id)
        if lease is None:
            return False, {"reason": "invalid ids"}
        holder: Optional[Dict[str, Any]] = None
        try:
            lease.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lease), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "acquired_at": time.time()}, f)
            return True, None
        except FileExistsError:
            raw = _read_json_sync(lease)
            if isinstance(raw, dict):
                age = time.time() - float(raw.get("acquired_at") or 0.0)
                if age > RESUME_LEASE_TTL_SECONDS:
                    # 过期 lease：抢占（stale 持有者已死或挂起超时）。
                    try:
                        lease.unlink()
                        fd = os.open(
                            str(lease), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                        )
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            json.dump(
                                {"pid": os.getpid(), "acquired_at": time.time()}, f
                            )
                        return True, None
                    except OSError:
                        return False, {"reason": "lease contention"}
                holder = {
                    "pid": raw.get("pid"),
                    "age_seconds": round(age, 1),
                }
            return False, holder or {"reason": "lease exists"}
        except OSError as e:
            logger.warning(
                "[RuntimeSnapshot] lease acquire failed project=%s: %s",
                project_id, e,
            )
            return False, {"reason": f"lease error: {e}"}

    def release_resume_lease(self, project_id: str, snapshot_id: str) -> None:
        lease = self._lease_path(project_id, snapshot_id)
        if lease is None:
            return
        try:
            lease.unlink()
        except OSError:
            pass


_store: Optional[ProjectRuntimeStore] = None


def get_project_runtime_store() -> ProjectRuntimeStore:
    global _store
    if _store is None:
        _store = ProjectRuntimeStore()
    return _store


def reset_project_runtime_store() -> None:
    global _store
    _store = None
