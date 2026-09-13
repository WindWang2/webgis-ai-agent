# ads-v1 · DS1 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS1 · 数据源注册表与能力声明 · ADR-0171 · 里程碑 M1（与 DS0 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 注册表格式 + schema + 加载器 + 热加载 + 显式报错 | `app/services/data_fabric/source_registry.py`（pydantic 模型 + `SourceRegistryService`） | `tests/unit/test_data_fabric_source_registry.py`（15 测） | 重复 ID/未知协议/明文密钥均 `SourceRegistryError`（带文件名）；`reload_if_changed()` 热加载有测试 |
| 能力声明 schema | 同上（`PushdownCapabilities` / `QuotaDecl` / `AuthDecl` / `DatasetDecl`） | 同上 | pushdown/quota/覆盖/许可/新鲜度/auth/verified 全字段 |
| 12 个源条目 | `config/sources/*.yaml`（beijing/shanghai/guangdong_gov + local_osm/poi/yearbook + planetary_computer/copernicus/nasa_cmr + worldbank/gbif/overpass） | `test_repo_sources_load_and_count` | 3 政务 + 3 本地 + 6 公开 = 12；公开源 `verified: false` 已在 PR 披露 |
| A2 gov adapter 迁移 | `app/adapters/gov/gov_data_adapter.py`（`_platforms()` 读注册表；`PLATFORMS` 保留为 deprecated 过渡兜底，DS9 清理） | `test_gov_adapter_reads_registry` + `test_gov_adapter_falls_back_on_registry_failure` | 生产路径以注册表为准（键已从 beijing→beijing_gov 证明） |
| 注册表 → registry 注册 | `source_registry.to_profile()/build_adapter()` 桥 `data_fabric.registry`；新协议在 `registry.py` 注册 | `test_to_profile_and_build_adapter_for_fabric_protocol` + `test_new_protocols_registered_in_fabric_registry` | explorer-only 协议（gov_portal）typed 拒绝 fabric 化 |
| 新 adapter ×4 | `adapters/geopackage_adapter.py`（pyogrio read_info 零行读 schema + bbox 下推）；`adapters/local_file_adapter.py`（GeoJSON + sqlite 只读）；`adapters/cog_adapter.py`（rasterio 头信息，query typed 不支持）；`adapters/stats_api_adapter.py`（声明式响应映射 + make_safe_session SSRF 安全） | `tests/data/test_ads_local_adapters.py`（9 测：真 GPKG/GeoJSON/sqlite/TIFF round-trip + 缺失 typed unavailable） | 缺失文件 → `SourceUnreachableError`（绝不空结果/伪造）；未灌数 env 未解析 → typed unavailable |
| 端到端演示（加源零 Python） | `tests/data/test_ads_source_registry_demo.py` | 1 测 | 落一个 YAML → load → build_adapter → sync 进 spatial_catalog → 目录可见 |
| 注册表 lint | `scripts/check_source_registry.py` | — | 0 错误；6 个公开源 `verified:false` 披露；能力声明与 adapter 实际不符被 lint 拦截（已据此下调 3 个 YAML 的 bbox/cql 声明） |
| CHANGELOG | `CHANGELOG.md`（DS1 合并入 Unreleased 段） | — | — |

## 波次验收对照（§6 DS1 行）

- [x] ≥12 源条目（3 政务 + 3 本地 + ≥6 公开）
- [x] 加新源无需改 Python（端到端演示测试）
- [x] `gov_data_adapter` 硬编码归零（注册表为生产路径；deprecated 常量保留至零引用确认——DS9 清理项）
- [x] lint 脚本 0 告警（`python scripts/check_source_registry.py` → OK）

## 披露

- 6 个公开源（planetary_computer / copernicus_dataspace / nasa_cmr_lpcloud / worldbank_api / gbif_api / overpass_api）离线无法验证 → `verified: false`，检索排序降权（DS2 消费），联网复验后置 true。
- `stats_api` adapter 的 bbox/CQL 下推未实现 → 相应 YAML 能力声明按实下调，DS3 下推最大化波次接入后上调（lint 防止再次声明过实）。
