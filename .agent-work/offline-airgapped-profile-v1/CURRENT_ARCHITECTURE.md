# CURRENT ARCHITECTURE — 网络依赖面（Phase 0 勘察结论）

## 出网客户端与检查点（choke points）

| 客户端家族 | 中心接缝 | 说明 |
|---|---|---|
| aiohttp | `app/core/network.py::get_shared_client()/create_client_session()` | geocoding/OSM/CN 地图/web_crawler/gov adapter 共用池 |
| requests | `app/services/data_fabric/security.py::make_safe_session()/SSRFSafeHTTPAdapter.send()` | 全部 data_fabric 远程 adapter（每跳 redirect 重验） |
| httpx（LLM 池） | `app/services/chat/llm_client.py::LLMHttpClientRegistry.acquire()` | chat LLM 唯一池 |
| httpx（ad-hoc） | vlm_provider.py:225,268；visual_evaluator.py:340；health.py:48；config.py llm-test；extensions broker.py:255；modelops remote_client.py:212；local_admin.py:185,280；local_stats.py:201,207 | 8 处散点 |
| pystac-client | `app/services/rs/stac_client.py` | STAC 检索 |
| rasterio /vsicurl | `app/lib/geo_raster/remote.py` | 远程 COG |
| sentence-transformers | `app/services/rag/faiss_store.py` | HF 下载面（已有 RAG_EMBEDDING_OFFLINE） |
| 子进程 | vendor/pi（stdio JSON-RPC） | LLM 调用在子进程内，Python 层不可拦截 |

## 已有离线/出网管控（复用，不重造）

1. `EXTENSION_NETWORK_ALLOW`（broker 默认 deny，per-extension host allowlist）
2. `MODELOPS_REMOTE_ALLOWLIST` + `RemoteEndpointPolicy`（default-deny + typed `RemoteEndpointPolicyError`）
3. `RAG_EMBEDDING_OFFLINE`（HF local_files_only）
4. `LOCAL_QUERY_FIRST` + `LOCAL_GEODATA_DIR`（本地优先，远程兜底）
5. `NEXT_PUBLIC_MAP_GLYPHS_URL`（离线 glyph 托管）
6. `tests/data/offline_guard.py`（测试专用 socket 守卫，运行时不加载）
7. data_fabric typed errors：`SourceUnreachableError/SourceTimeoutError/SecurityBlockedError/...`

## 设置体系硬约束

- 单一 `Settings`（pydantic-settings，`app/core/config.py`），prod fail-fast 走 `@model_validator(mode="after")`
- **任何新 env 键必须三同步**：`config.py` 字段 + `.env.example` 条目 + `tests/conftest.py::_ENV_BASELINE`（`tests/unit/test_env_hygiene.py` 强制 parity；代理键除外）
- `app/services/modelops/config.py` 的 `MODELOPS_*` 是第二 env 面（同规则）

## 健康面

- `/api/v1/health`、`/health/live`、`/ready`（db+llm+redis+celery，503 语义）、`/status/detailed`（JWT 鉴权，`_SRE_COMPONENTS = ("db","redis","llm","worker","object_store")`，词表封闭 ok|degraded|down|not_configured）
- CLI：`manage.py`（argparse，`check` 子命令已覆盖 DB/Redis/LLM/Celery）；extensions CLI 另有 `doctor`

## 前端底图

- `frontend/lib/providers.ts::TILE_PROVIDERS`（12 个远程 provider，URL 硬编码）；`app/core/base_layers.py::BASE_LAYER_CATALOG` 后端镜像
- PMTiles 前端已可作 data source 添加；glyphs 已可 env 指向本地

## 惯例

- typed error + reason code；"honest failure / never fake data"；ADR 引用注释；中文注释说明动机；argparse 而非 click；测试禁真网（conftest `_offline_embedding_model`、`LOCAL_QUERY_FIRST=False` 钉扎）
