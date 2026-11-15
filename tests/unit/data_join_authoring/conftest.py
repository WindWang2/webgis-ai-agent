"""G38 单测夹具 —— DB 表、DATA_DIR 隔离、会话源种子。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.core.database import Base
import app.models  # noqa: F401 — 注册全部模型（含 data_join_authoring）


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每测独立 DATA_DIR（data-join 终态包/会话存储不落开发目录）。"""
    from app.core.config import settings

    data_dir = tmp_path / "g38-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    # 会话存储：内存实现（USE_REDIS=false 已由套件 conftest 钉扎）
    yield data_dir


@pytest.fixture()
def db_tables():
    """本域 + 全模型建表（测试库 = 套件级临时 SQLite）。"""
    from app.core.database import Engine

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    yield


async def seed_session_source(session_id: str, payload: Dict[str, Any],
                              prefix: str = "g38") -> str:
    """payload → session ref（测试种子；会话存储内存实现）。"""
    from app.services.session_data import session_data_manager

    return await session_data_manager.store(session_id, payload, prefix=prefix)


@pytest.fixture()
async def seeded_sources() -> Dict[str, Any]:
    """旗舰 A 双源：行政区边界 FC + 人口表（async 种子 → ref 集合）。"""
    from tests.unit.data_join_authoring.fixtures import (
        admin_boundaries,
        population_table,
    )

    session_id = "sess-g38-unit"
    left_ref = await seed_session_source(
        session_id, admin_boundaries(), prefix="g38fc")
    right_ref = await seed_session_source(
        session_id, population_table(), prefix="g38rows")
    return {
        "session_id": session_id,
        "left_ref": left_ref,
        "right_ref": right_ref,
        "actor": {"trusted_session_id": session_id},
    }
