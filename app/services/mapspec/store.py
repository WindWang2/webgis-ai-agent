"""MapSpec Storage Module (app/services/mapspec/store.py).

负责 MapSpec JSON 文件的持久化、版本 Revision 生成，
以及 Redis map_state 的底层缓存同步。
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASE_STORAGE_DIR = PROJECT_ROOT / ".webgis-agent"

LABEL_LAYER_SUFFIX = "-label"


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


class MapSpecStore:
    """MapSpec JSON 文件持久化与 Revision 管理服务"""

    def get_session_dir(self, session_id: str) -> Path:
        session_dir = BASE_STORAGE_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    async def get_mapspec(self, session_id: str) -> Optional[Dict[str, Any]]:
        map_state = await session_data_manager.get_map_state(session_id)
        if "mapspec" in map_state:
            return map_state["mapspec"]

        mapspec_file = self.get_session_dir(session_id) / "mapspec.json"
        if mapspec_file.exists():
            try:
                with open(mapspec_file, "r", encoding="utf-8") as f:
                    mapspec = json.load(f)
                    await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
                    return mapspec
            except Exception as e:
                logger.error(f"Error reading mapspec file for session {session_id}: {e}")

        return None

    async def save_mapspec(self, session_id: str, mapspec: Dict[str, Any]) -> Dict[str, Any]:
        session_dir = self.get_session_dir(session_id)
        rev_dir = session_dir / "revisions"
        rev_dir.mkdir(parents=True, exist_ok=True)

        mapspec_path = session_dir / "mapspec.json"
        with open(mapspec_path, "w", encoding="utf-8") as f:
            json.dump(mapspec, f, ensure_ascii=False, indent=2)

        rev_filename = f"mapspec_rev_{int(time.time() * 1000)}.json"
        with open(rev_dir / rev_filename, "w", encoding="utf-8") as f:
            json.dump(mapspec, f, ensure_ascii=False, indent=2)

        await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
        return {"mapspec": mapspec}


mapspec_store_instance = MapSpecStore()
