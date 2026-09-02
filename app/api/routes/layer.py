"""
图层数据 API

- GET /layers/data/{ref_id}: Fetch-on-Demand 端点，Agent 执行链路的一部分。
- GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt: 大 POI 矢量瓦片端点
  （Data Plane：替代超大 GeoJSON 全量下发的显示路径）。
- GET /layer-types: 图层类型枚举。
- 图层 CRUD 已移除 — Agent 通过工具链自动创建和管理图层（"Agent is Everything"）。
- 空间分析任务端点已移除 — Agent 通过 tool calling 驱动分析，不再走 REST CRUD。
"""

import asyncio
import gzip
import hashlib
from collections import OrderedDict
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.core.auth import require_owned_session, verify_session_owner
from app.lib.geojson_serializer import serialize_geojson
from app.models.db_model import Conversation
from app.services.mvt import (
    RefDataUnavailableError,
    build_spatial_index_entry,
    encode_tile_from_index,
    single_flight,
    spatial_index_cache,
    tile_lru_cache,
)
from app.services.session_data import session_data_manager
from app.services.task_tracker import TaskTracker  # noqa: F401  (typing aid)
from app.tools._utils import async_db_session

logger = logging.getLogger(__name__)

router = APIRouter()

# NOTE: full file restored in follow-up if truncated — see workspace layer.py
