"""MapSpec Storage Module (app/services/mapspec/store.py).

负责 MapSpec JSON 文件的持久化、版本 Revision 生成，
以及 Redis map_state 的底层缓存同步。

可靠性契约（REL-03 / REL-04）：
- 磁盘写入原子（temp + os.replace），崩溃不会留下半截 mapspec.json。
- 文件 IO 经 asyncio.to_thread 卸载，不阻塞 event loop（大 inline GeoJSON
  不再冻结所有 session 的 I/O）。
- 磁盘与 Redis 双写的顺序：先落盘（durability），再写 Redis（cache）。落盘
  失败绝不写 Redis，避免 cache 持有磁盘没有的 state。
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASE_STORAGE_DIR = PROJECT_ROOT / ".webgis-agent"

LABEL_LAYER_SUFFIX = "-label"

# Revision 保留上限：每次 save 都会生成一份完整快照 mapspec_rev_<ms>.json。
# 无上限时磁盘随会话生命周期无界增长（审计 Phase 8 发现）。裁剪到最近 N 份。
MAPSPEC_REV_RETENTION = 20


def _should_remove_layer(layer: Dict[str, Any], target_layer_id: str) -> bool:
    """判断图层是否为目标图层或其伴随标签图层"""
    lid = layer.get("id")
    if not lid:
        return False
    return lid == target_layer_id or lid == f"{target_layer_id}{LABEL_LAYER_SUFFIX}"


def view_has_center(mapspec: Dict[str, Any]) -> bool:
    """Predicate: MapSpec 是否显式设置了视点 Center 坐标"""
    view = mapspec.get("view") or {}
    center = view.get("center", None)
    return "center" in view and center is not None


def _atomic_write_json_sync(path: Path, payload: Any) -> None:
    """同步原子写 JSON：写到同目录临时文件再 os.replace。

    POSIX 下 os.replace 是原子的；崩溃要么看到旧文件、要么看到新文件，
    不会出现半截写入。临时文件与目标同目录保证 replace 不跨文件系统。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, str(path))
    except BaseException:
        # 清理未替换的临时文件，避免遗留垃圾
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json_sync(path: Path) -> Optional[Any]:
    """同步读 JSON；文件不存在或损坏返回 None（不抛）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"[mapspec] read failed for {path}: {e}")
        return None


class MapSpecStore:
    """MapSpec JSON 文件持久化与 Revision 管理服务"""

    def get_session_dir(self, session_id: str) -> Path:
        base = BASE_STORAGE_DIR.resolve()
        session_dir = (base / session_id).resolve()
        if session_dir.parent != base:
            raise ValueError("invalid session id for MapSpec storage")
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    async def clear_session_files(self, session_id: str) -> None:
        """Purge durable MapSpec/checkpoint/revision state for one session."""
        session_dir = self.get_session_dir(session_id)
        await asyncio.to_thread(shutil.rmtree, session_dir)

    async def discard_mapspec(self, session_id: str) -> None:
        """Remove a first-mutation MapSpec that must not survive rollback.

        Used when the session had no prior spec: saving then failing would
        otherwise leave the half-committed candidate as last-known-good.
        This is not a session-delete tombstone — later mutations may create a
        fresh spec.
        """
        mapspec_path = self.get_session_dir(session_id) / "mapspec.json"

        def _unlink() -> None:
            mapspec_path.unlink(missing_ok=True)

        await asyncio.to_thread(_unlink)
        await session_data_manager.set_map_state(session_id, "mapspec", None)
        await session_data_manager.set_map_state(
            session_id, "_cartographic_mutation_revision", 0
        )

    async def get_mapspec(self, session_id: str) -> Optional[Dict[str, Any]]:
        map_state = await session_data_manager.get_map_state(session_id)
        if map_state.get("_cartographic_deleted") is True:
            return None
        if "mapspec" in map_state:
            return map_state["mapspec"]

        mapspec_file = self.get_session_dir(session_id) / "mapspec.json"
        # 文件读卸载到线程，避免大文件阻塞 event loop。
        mapspec = await asyncio.to_thread(_read_json_sync, mapspec_file)
        if mapspec is not None:
            await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
            return mapspec

        return None

    async def save_mapspec(self, session_id: str, mapspec: Dict[str, Any]) -> Dict[str, Any]:
        session_dir = self.get_session_dir(session_id)
        rev_dir = session_dir / "revisions"
        mapspec_path = session_dir / "mapspec.json"

        # No-op 保护（Phase 8）：若磁盘与 Redis 均已持有相同 spec，跳过全部
        # 三重写入。重复/幂等保存（如快速连续相同意图）因此不产生 IO。
        # 磁盘读卸载到线程。
        disk_current = await asyncio.to_thread(_read_json_sync, mapspec_path)
        if disk_current == mapspec:
            state = await session_data_manager.get_map_state(session_id)
            if state.get("mapspec") == mapspec:
                return {"mapspec": mapspec}

        # 原子落盘（mapspec.json + revision 快照 + 裁剪）整体卸载到线程，
        # 避免大 GeoJSON 的 json.dump 阻塞 event loop。
        await asyncio.to_thread(
            self._persist_disk_sync, mapspec_path, rev_dir, mapspec
        )

        # 落盘成功后再写 Redis cache（顺序契约：cache 不持有磁盘没有的 state）。
        persisted = await session_data_manager.set_map_state(
            session_id, "mapspec", mapspec
        )
        if persisted is False:
            raise RuntimeError("authoritative MapSpec cache write rejected")
        return {"mapspec": mapspec}

    @staticmethod
    def _persist_disk_sync(
        mapspec_path: Path, rev_dir: Path, mapspec: Dict[str, Any]
    ) -> None:
        """同步：原子写 mapspec.json + 写 revision + 裁剪旧 revision。"""
        _atomic_write_json_sync(mapspec_path, mapspec)

        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_filename = f"mapspec_rev_{int(time.time() * 1000)}.json"
        _atomic_write_json_sync(rev_dir / rev_filename, mapspec)

        # Revision 保留上限：裁剪到最近 MAPSPEC_REV_RETENTION 份
        # （按文件名时间戳排序，删除最旧）。
        try:
            rev_files = sorted(rev_dir.glob("mapspec_rev_*.json"))
            if len(rev_files) > MAPSPEC_REV_RETENTION:
                for stale in rev_files[:-MAPSPEC_REV_RETENTION]:
                    stale.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"[mapspec] revision pruning failed: {e}")


mapspec_store_instance = MapSpecStore()
