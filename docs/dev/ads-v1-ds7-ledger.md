# ads-v1 · DS7 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS7 · 本地数据资产索引 · ADR-0177 · 里程碑 M4（单波车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 统一清单 | `app/services/data_fabric/local_index.py::scan_local_assets`（三库扫描，root 注入缝） | `tests/unit/test_data_fabric_local_index.py`（10 测） | available=LocalAsset（pyogrio/sqlite 真实层清单 + 零行读 meta）；扫描产物进 DS2 卡片 |
| meta.json 规范化 | `META_SCHEMA`（8 键）+ `normalize_meta`（sidecar 别名兼容：srs/total_rows/generated_at） | `test_meta_schema_keys_documented` / `test_normalize_meta_maps_sidecar_aliases` | 未知值诚实缺位进 `meta_missing`；三库补齐来自 owning 模块事实（poi CRS/字段、yearbook 时间范围） |
| 自动扫描注册 | `manage.py sources-scan` 子命令 + `assets_to_cards` → DS2 检索索引 | `test_sources_scan_command_runs`（函数级冒烟） + `test_assets_to_cards_are_local_and_verified` | 本机实测：root 未配置 → 三库显式 unavailable + 灌数指引 |
| 缺失态处理（硬约束） | `UnavailableLibrary{reason, ingest_hint}` | `test_scan_empty_dir_is_explicit_unavailable` / `test_scan_unconfigured_root_reports_all_unavailable` | 指引必须含 manage.py 命令（闸测试）；绝不伪造空结果 |
| 本地优先策略 | DS3 cost=1.0 + DS4 priority 链 + 本波卡片合并 | 见 DS3/DS4 台账 | cost_hint≈0 语义在代价模型与排序中生效 |
| 在线/本地统一检索（10 组用例） | DS2 ranker（rel 主导，cost 平权） | `test_mixed_local_online_ten_cases` + 正反两例（不强行置顶/相关性一致时胜出） | 10 组混合用例断言：显式本地查询→本地第一；纯在线查询→在线优先 |

## 波次验收对照（§6 DS7 行）

- [x] 三类本地库各有 descriptor 且可检索（LocalAsset → DatasetCard → 检索索引）
- [x] meta.json 统一 schema 且三项补齐（8 键 schema + owning 模块事实填充）
- [x] `sources-scan` 可用（命令冒烟 + 真实输出）
- [x] 空目录显式 `unavailable` 有测试（含灌数指引可执行断言）
- [x] 本地/在线混合检索 10 组用例（含不强行置顶的反例断言）

## 备注

- 本波无迁移（清单是扫描产物；meta 持久化需求随 DS8 facts 库评估）。
- manage.py 改动 = 任务书 DS7.3 明确要求的子命令注册（最小 diff：subparser + dispatch + 命令函数）。
