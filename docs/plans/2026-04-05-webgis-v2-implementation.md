# WebGIS AI Agent v2 Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 将 webgis-ai-agent 从分散的原型代码重构为基于 Function Calling 的研究/教学工具

**Architecture:** 用户通过对话面板发送自然语言指令，后端 LLM 通过 FC 调度工具链（OSM查询/空间分析/遥感/报告），结果以 GeoJSON/影像形式推送至前端地图和结果面板

**Tech Stack:** FastAPI + OpenAI SDK(FC) + GeoPandas + overpy + sentinelhub + MapLibre GL JS + Next.js + SQLite

---

## Phase A: 清理 + 基础对话链路

### Task A1: 删除无关模块

**Files:**
- Delete: `app/services/pr_checker/` (entire directory)
- Delete: `app/services/pr_workflow/` (entire directory)
- Delete: `app/services/pr_check_flow.py`
- Delete: `app/services/issue_check_flow.py`
- Delete: `app/services/issue_state_sync.py`
- Delete: `app/services/issue_stats.py`
- Delete: `app/services/issue_tracker_db.py`
- Delete: `app/services/issue_workflow/` (entire directory)
- Delete: `app/services/feishu_notification.py`
- Delete: `app/services/feishu_notifier.py`
- Delete: `app/services/celery_config.py`
- Delete: `app/services/celery_worker.py`
- Delete: `app/services/celery_issue_tasks.py`
- Delete: `app/services/orchestration/` (entire directory)
- Delete: `app/services/task_queue.py`
- Delete: `app/api/routes/webhook.py`
- Delete: `app/api/routes/issue_webhook.py`
- Delete: `app/api/routes/orchestration.py`
- Delete: `app/api/routes/orchestration_v2.py`
- Delete: `app/api/routes/auth.py`
- Delete: `app/core/auth.py`
- Delete: `app/core/app.py`
- Delete: `tests/unit/test_pr_check_flow.py`
- Delete: `tests/unit/test_issue_tracker_db.py`
- Delete: `tests/unit/test_celery_issue_timeout.py`
- Delete: `tests/unit/test_auth.py`
- Delete: `tests/orchestration/` (entire directory)
- Delete: `tests/unit/` (remaining)
- Clean up: `requirements.txt` — remove unused deps (celery, redis, langchain)

**Step 1: Delete files**
```bash
rm -rf app/services/pr_checker app/services/pr_workflow app/services/issue_workflow app/services/orchestration tests/orchestration
rm -f app/services/pr_check_flow.py app/services/issue_check_flow.py app/services/issue_state_sync.py app/services/issue_stats.py app/services/issue_tracker_db.py app/services/feishu_notification.py app/services/feishu_notifier.py app/services/celery_config.py app/services/celery_worker.py app/services/celery_issue_tasks.py app/services/task_queue.py
rm -f app/api/routes/webhook.py app/api/routes/issue_webhook.py app/api/routes/orchestration.py app/api/routes/orchestration_v2.py app/api/routes/auth.py
rm -f app/core/auth.py app/core/app.py
rm -rf tests/unit
```

**Step 2: Clean requirements.txt**
保留: fastapi, uvicorn, sqlalchemy, pydantic, pydantic-settings, geojson, geopandas, shapely, rasterio, openai, overpy, httpx, aiofiles
删除: celery, redis, langchain, sentry-sdk 等无关依赖

**Step 3: Verify app still imports**
```bash
python -c "from app.core.config import settings; print('OK')"
```

**Step 4: Commit**
```bash
git add -A && git commit -m "chore: remove non-core modules (PR/Issue/Celery/Feishu/Auth)"
```

---

### Task A2: 重构配置模块

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_config.py`

**Step 1: Write test**
```python
# tests/test_config.py
import os
import pytest

def test_default_settings():
    from app.core.config import Settings
    s = Settings()
    assert s.PROJECT_NAME == "WebGIS AI Agent"
    assert s.DEBUG is True
    assert s.LLM_MODEL  # has default model
    assert s.DATA_DIR == "./data"

def test_llm_settings():
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "LLM_BASE_URL")
    assert hasattr(s, "LLM_API_KEY")
    assert hasattr(s, "LLM_MODEL")

def test_osm_settings():
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "OVERPASS_API_URL")
    assert hasattr(s, "NOMINATIM_URL")

def test_tiangodi_settings():
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "TIANDITU_TOKEN")

def test_sentinel_settings():
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "SENTINELHUB_CLIENT_ID")
    assert hasattr(s, "SENTINELHUB_CLIENT_SECRET")
```

**Step 2: Run test — confirm fails**
```bash
pytest tests/test_config.py -v
```
Expected: FAIL — missing LLM_MODEL, OVERPASS_API_URL etc.

**Step 3: Rewrite config.py**
```python
"""核心配置模块"""
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    PROJECT_NAME: str = "WebGIS AI Agent"
    DEBUG: bool = True
    API_V1_STR: str = "/api"

    # 数据库 (SQLite)
    DATABASE_URL: str = "sqlite:///./webgis.db"

    # LLM
    LLM_BASE_URL: str = "http://192.168.193.70:8000/v1"
    LLM_API_KEY: str = "not-needed"
    LLM_MODEL: str = "MiniMax-M2.5"

    # OSM
    OVERPASS_API_URL: str = "https://overpass-api.de/api/interpreter"
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"

    # 天地图
    TIANDITU_TOKEN: str = ""

    # Sentinel Hub
    SENTINELHUB_CLIENT_ID: str = ""
    SENTINELHUB_CLIENT_SECRET: str = ""

    # NASA EarthData
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""

    # 数据目录
    DATA_DIR: str = "./data"
    TMP_DIR: str = "./tmp"

    # CORS
    CORS_ORIGINS: List[str] = ["*"]

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

**Step 4: Run test — confirm passes**
**Step 5: Commit**
```bash
git add -A && git commit -m "refactor: rewrite config with OSM/Sentinel/Tianditu settings"
```

---

### Task A3: 重构数据库层 (SQLite)

**Files:**
- Modify: `app/db/session.py` → `app/core/database.py`
- Modify: `app/models/db_models.py` → `app/models/database.py`
- Test: `tests/test_database.py`

**Step 1: Write test**
```python
# tests/test_database.py
import pytest
from sqlalchemy import create_engine
from app.core.database import Base, get_engine

def test_engine_creates_sqlite():
    engine = get_engine()
    assert "sqlite" in str(engine.url)

def test_base_has_tables():
    # 检查 Base.metadata 注册了预期表
    table_names = Base.metadata.tables.keys()
    assert "conversations" in table_names
    assert "layers" in table_names
```

**Step 2: Run test — confirm fails**
**Step 3: Implement database.py**

```python
# app/core/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

class Base(DeclarativeBase):
    pass

def get_engine():
    return create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})

Engine = get_engine()
SessionLocal = sessionmaker(bind=Engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=Engine)
```

数据库模型简化为：`conversations`（对话历史）、`messages`（消息）、`layers`（图层元数据）

**Step 4: Run test — confirm passes**
**Step 5: Commit**

---

### Task A4: 实现 FC 工具注册框架

**Files:**
- Create: `app/tools/__init__.py`
- Create: `app/tools/registry.py`
- Create: `app/tools/geocoding.py`
- Test: `tests/test_tool_registry.py`

**Step 1: Write test**
```python
# tests/test_tool_registry.py
from app.tools.registry import ToolRegistry, tool

def test_register_tool():
    registry = ToolRegistry()

    @tool(registry, name="test_tool", description="A test")
    def my_tool(query: str) -> dict:
        return {"result": query}

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "test_tool"

def test_dispatch_tool():
    registry = ToolRegistry()

    @tool(registry, name="test_tool", description="A test")
    def my_tool(query: str) -> dict:
        return {"result": query}

    result = registry.dispatch("test_tool", {"query": "hello"})
    assert result == {"result": "hello"}

def test_dispatch_unknown_raises():
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.dispatch("nonexistent", {})
```

**Step 2: Run test — confirm fails**
**Step 3: Implement registry.py**

```python
# app/tools/registry.py
import json
from typing import Callable, Any
from pydantic import validate_call

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict] = []

    def register(self, name: str, description: str, func: Callable):
        self._tools[name] = func
        # Auto-generate JSON schema from function signature
        import inspect
        sig = inspect.signature(func)
        params = {}
        required = []
        for p_name, p in sig.parameters.items():
            p_type = "string"
            if p.annotation in (int, float):
                p_type = "number"
            elif p.annotation is bool:
                p_type = "boolean"
            params[p_name] = {"type": p_type, "description": p_name}
            if p.default is inspect.Parameter.empty:
                required.append(p_name)
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required,
                }
            }
        }
        self._schemas.append(schema)

    def get_schemas(self) -> list[dict]:
        return self._schemas

    def dispatch(self, name: str, arguments: dict) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name](**arguments)

def tool(registry: ToolRegistry, name: str, description: str):
    def decorator(func):
        registry.register(name, description, func)
        return func
    return decorator
```

**Step 4: Run test — confirm passes**
**Step 5: Commit**
```bash
git add -A && git commit -m "feat: FC tool registry framework"
```

---

### Task A5: 实现对话引擎

**Files:**
- Create: `app/services/chat_engine.py`
- Test: `tests/test_chat_engine.py`

**Step 1: Write test**
```python
# tests/test_chat_engine.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode a location")
    def geocode(query: str) -> dict:
        return {"lat": 39.9, "lon": 116.4, "name": "Beijing"}

    return r

def test_chat_engine_init(registry):
    engine = ChatEngine(registry)
    assert len(engine.tools) > 0

@pytest.mark.asyncio
async def test_chat_returns_response(registry):
    engine = ChatEngine(registry)
    with patch("app.services.chat_engine.AsyncOpenAI") as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "北京坐标是 39.9, 116.4"
        mock_response.choices[0].message.tool_calls = None
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await engine.chat("北京的坐标", session_id="test")
        assert "39.9" in result["content"] or result["content"]
```

**Step 2: Run test — confirm fails**
**Step 3: Implement chat_engine.py** — OpenAI FC 循环：发消息 → 检查 tool_calls → dispatch → 回传结果 → 最终回复
**Step 4: Run test — confirm passes**
**Step 5: Commit**

---

### Task A6: 重写 Chat API (SSE)

**Files:**
- Modify: `app/api/routes/chat.py`
- Test: `tests/test_chat_api.py`

**Step 1: Write test** — 测试 POST /api/chat 和 SSE 流
**Step 2: Run test — confirm fails**
**Step 3: Implement** — FastAPI StreamingResponse + SSE
**Step 4: Run test — confirm passes**
**Step 5: Commit**

---

### Task A7: 前端对话对接真实 API

**Files:**
- Modify: `frontend/lib/api/chat.ts`
- Modify: `frontend/components/chat/chat-panel.tsx`

**Step 1:** 移除 mock 数据，对接 SSE 端点
**Step 2:** 测试对话面板流式显示
**Step 3: Commit**

---

### Task A8: 重写 FastAPI 入口

**Files:**
- Modify: `app/main.py` (was `app/core/app.py create_app`)
- Modify: `main.py`

清理路由注册，只保留 chat / map / health。去掉所有旧中间件。

**Commit**

---

## Phase B: OSM + 地图

### Task B1: OSM Geocoding 工具

**Files:**
- Create: `app/tools/geocoding.py`
- Test: `tests/test_geocoding.py`

实现 `geocode(query)` 和 `reverse_geocode(lat, lon)`，调用 Nominatim。
注册到 ToolRegistry。

---

### Task B2: OSM Overpass 工具

**Files:**
- Create: `app/tools/osm.py`
- Test: `tests/test_osm.py`

实现:
- `query_osm_poi(area, category)` — 查询 POI
- `query_osm_roads(bbox)` — 查询路网
- `query_osm_buildings(bbox)` — 查询建筑
- `query_osm_boundary(name)` — 查询行政区边界

结果转 GeoJSON，支持缓存。

---

### Task B3: 地图底图服务

**Files:**
- Modify: `frontend/components/map/map-panel.tsx`
- Create: `frontend/lib/map-styles.ts`

实现 OSM + 天地图双底图切换。
天地图需要 WMTS token（从 config 获取）。

---

### Task B4: GeoJSON 图层渲染

**Files:**
- Modify: `frontend/components/map/map-panel.tsx`
- Modify: `frontend/components/chat/chat-panel.tsx`

对话中工具返回 GeoJSON → 自动添加为地图图层。
前端通过 SSE 事件接收 `tool_result` 类型消息。

---

## Phase C: 空间分析

### Task C1: 空间分析工具

**Files:**
- Create: `app/tools/spatial.py`
- Modify: `app/services/spatial_service.py` (保留现有，封装为工具)

注册 FC 工具：buffer_analysis, overlay_analysis, spatial_stats, heatmap

---

### Task C2: 分析结果可视化

**Files:**
- Modify: `frontend/components/panel/results-panel.tsx`

分析结果渲染：表格 + 图表 + GeoJSON 叠加到地图

---

### Task C3: 报告生成对接

**Files:**
- Modify: `app/tools/report.py`
- Modify: `app/services/report_service.py`
- Modify: `frontend/components/report/`

报告工具注册为 FC 工具，对话中可触发报告生成。

---

## Phase D: 遥感数据

### Task D1: Sentinel Hub 工具

**Files:**
- Create: `app/tools/remote_sensing.py`
- Create: `app/services/rs_service.py`
- Test: `tests/test_rs_service.py`

实现 `fetch_sentinel(bbox, date_range, bands)` 和 `compute_ndvi(bbox, date_range)`

---

### Task D2: NASA EarthData / DEM

**Files:**
- Modify: `app/tools/remote_sensing.py`
- Modify: `app/services/rs_service.py`

实现 `fetch_dem(bbox)` — SRTM DEM 数据获取

---

### Task D3: 影像地图叠加

**Files:**
- Modify: `frontend/components/map/map-panel.tsx`

GeoTIFF / COG 影像加载为地图图层（MapLibre 支持栅格源）

---

## 总结

| Phase | 任务数 | 预计耗时 | 交付物 |
|-------|--------|----------|--------|
| A | 8 | 主要工作量 | 可对话的 FC Agent |
| B | 4 | 中等 | OSM 数据查询 + 地图渲染 |
| C | 3 | 中等 | 完整分析链路 |
| D | 3 | 较大（API申请） | 遥感数据支持 |

**建议执行顺序: A → B → C → D，每个 Phase 完成后验证再进入下一个。**
