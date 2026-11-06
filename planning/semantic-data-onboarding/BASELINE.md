# BASELINE — 多源空间数据自动准备与语义绑定工作台

- 基线 SHA：`4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（origin/master，2026-10-29 fetch，PR #1867 合并后；任务拟定锚点同 SHA）
- worktree：`../webgis-worktrees/gis-data-onboarding`，分支 `zcode/gis-data-onboarding-semantic-vnext`
- 网络：2026-10-29 开工时本机对 github.com/api.github.com TLS 全断（git fetch/gh/curl 均失败，pypi 亦不通，属本机网络故障）。最后一次成功 fetch 即锚点。PR/issue 二次去重挂起，恢复后必须补做（见 LOOP_STATE.md）。

## 能力现状归档（三路 subagent 勘察，全部 file:line 实测）

| 能力 | 状态 | 证据 |
|---|---|---|
| 上传→解析（CSV/GeoJSON/SHP/KML/GPKG/GeoTIFF，50MB/200MB 上限，CRS 诚实披露，gb18030 回退） | production | `app/api/routes/upload.py:106`、`app/services/data_parser.py:18-58,346-372` |
| V3 摄入管线（sha256 去重/画像/质量/descriptor 铸造/登记/补偿回滚） | production | `app/services/data_ingest/pipeline.py:113-317` |
| 有界画像（50k 行确定性采样 + 深化） | production | `app/services/data_profile/profiler.py:202`、`app/lib/data/profile.py:36-42` |
| 规则化质量 + 修复提案/事务（proposed→dry_run→applied→verified→rolled_back） | production | `app/services/data_quality/repair_plan.py:144`、`repair_transaction.py:37-42`；公共核 `app/lib/data/quality.py:347`（30+ issue 码含 IMPOSSIBLE_COORDINATES/CRS_MISSING/FIELD_ROLE_AMBIGUITY） |
| dataset_semantics：descriptor（dsd-v1 指纹）/store(8 版本)/reuse/preflight(typed gaps)/provenance(TransformationRecord)/projections | production | `app/services/dataset_semantics/*`、`app/lib/gis/dataset_descriptor.py:285` |
| multi-role 数据集级绑定 + 状态词表（bound/ambiguous/stale/not_authorized…） | production | `app/services/dataset_semantics/role_binding.py:347,38-50` |
| 资格三对应物：DataQualification / PreflightReport / TerrainSourceReceipt | production（三形并存，无统一协议） | `app/services/gis_harness/data_qualification.py:177`、`preflight.py:219`、`terrain_ingest.py` |
| 工具链：ToolRegistry + ToolDispatchService + Pi bridge（`POST /pi-tools/execute`→共享 dispatch） | production | `app/tools/registry.py:475`、`app/tools/__init__.py:11-90`、`app/agent_pi_bridge.py:468`、`app/services/tool_dispatch_service.py:308` |
| 数据类工具（ingest/profile/describe/materialize/semantic compile 等 20+） | production | `app/tools/ingest_tools.py:53`、`data_discovery.py`、`semantic_tools.py:354` |
| 前端：上传入口（dataset-manager file input→upload API→attach） | production | `frontend/components/sidebar/project/dataset-manager.tsx:89-238`、`lib/api/upload.ts:60` |
| 前端：data-sources 面板（4 子页签/目录/检视/QuerySpec 构建器/物化入图） | production | `frontend/components/sidebar/data-sources-tab.tsx`、`dataset-inspector.tsx:47-501`、`:301-369` 物化→addLayer→fetchRefGeoJSON |
| 前端：descriptor modal（仅 id/title/geometry_type/srs/bbox/fields name:type 只读） | partial | `frontend/components/sidebar/data-sources/dataset-descriptor-modal.tsx:37-67` |
| explorer 字段自动映射（仅 name/address/lat/lon 4 角色；confidence 只算 name+address） | partial | `app/services/explorer/parse_stage.py:64-97` |
| explorer validate 阶段（零校验逻辑，仅汇总+桥接） | scaffold-only | `app/services/explorer/validate_stage.py:48` |
| data_fabric 获取语义 field_roles（time/geo/measure/id，声明级） | production（但与另两套不互通） | `app/services/data_fabric/semantic/field_roles.py:25` |
| 字段角色提案（confidence+reasons+rejected 候选） | **absent** | 全仓无 |
| 准备计划/收据（PreparationPlan/Receipt，变换前后指纹、质量 delta、幂等） | **absent** | materialize 仅元数据声明（pipeline.py:285-303）；repair 面只覆盖质量修复不含字段映射/CRS 准备 |
| 工具准备状态机（prepared/requires_input/degraded/blocked） | **absent** | 全 tools 目录无命中（最近骨架：RoleBinding 状态 + needs_clarification） |
| 字段级角色用户确认回路（澄清写回 descriptor） | **absent** | 仅 FIELD_ROLE_AMBIGUITY issue，无确认 API |
| 前端语义绑定 UI（roles/qualification/preflight 展示） | **absent** | modal 5 字段；TS 类型 `DatasetDescriptor`（lib/api/data-fabric.ts:70-81）无 roles |
| 预览足迹（footprint）/错误专用视图 | **absent** | preview-modal 仅表格+JSON |
| GeoParquet 上传 / 本地目录源 | absent（查询面有 GeoParquet） | data_parser.py:18-19 无 .parquet |

## 三套字段角色推断并存（整合点，不重写既有消费者）

1. `app/services/explorer/parse_stage.py:64` auto_field_mapping — name/address/lat/lon，中文 pattern，用于 gov 数据探索
2. `app/services/data_fabric/semantic/field_roles.py:25` infer_field_roles — time/geo/measure/id，fabric 声明级
3. `app/lib/gis/dataset_descriptor.py:103` FieldEntry.roles — descriptor 证据字段

本任务新增统一提案引擎为超集 + 双语语料，向 1/2/3 提供 adapter（渐进采用，不在本 PR 强改既有消费者）。

## 20 个现实失败场景矩阵（M1 交付，测试锚点）

| # | 场景 | 既有覆盖 | 本任务补 |
|---|---|---|---|
| 1 | CSV 经纬度列名中文（经度/纬度）映射 | data_parser 候选含中文 | 提案引擎含同义词 + 置信度 |
| 2 | x/y 对调（值域检测） | 无 | spatial receipt 检测 + 阻断 |
| 3 | 坐标超界（lat>90） | quality IMPOSSIBLE_COORDINATES | receipt 汇聚 + 修复建议（过滤步） |
| 4 | 未知 CRS（无 .prj 无声明） | data_parser crs_source=assumed | receipt typed blocked + requires_input |
| 5 | 伪 CRS（Gauss-Kruger 投影坐标当经纬度） | 部分（quality 码） | receipt 值域启发检测 |
| 6 | 时区 naive 时间字段 | semantic_checks timezone | receipt + 准备步（标注非改写） |
| 7 | 字段角色歧义（两个候选“名称”） | FIELD_ROLE_AMBIGUITY issue | 候选 + rejected + requires_input |
| 8 | 主键重复 | 无专门码 | receipt uniqueness 检查 |
| 9 | 多边形自交（bowtie） | invalid_geometry_fc 工厂 | 透传 quality + repair 提案 |
| 10 | 行政区缺（角色绑定 not_found） | role_binding 词表 | 工作台展示缺失原因 + 可执行算法面 |
| 11 | 人口分母缺失（denominator 角色） | 无 | 提案引擎角色 + preflight 披露降级 |
| 12 | 编码 gb18030 CSV | data_parser 回退 | receipt 记录 detected encoding |
| 13 | zip 炸弹/路径穿越 | shapefile 测试已有 | 不重复（负例只回归引用） |
| 14 | 超大表（10 万行）整包发给 LLM | ingest 有界纪律 | prepare 工具有界断言 |
| 15 | 重复导入同数据 | sha256 dedup | 计划幂等键复用 |
| 16 | 跨会话/跨租户越权 ref | ref store + auth 测试 | prepare API 负例 |
| 17 | 取消执行中任务 | CancellationToken 模式 | 计划执行检查点 |
| 18 | 数据替换后旧指纹误复用 | reuse.py 对账 | receipt 记录 before/after 指纹 |
| 19 | nodata/空值列 | profile 统计 | receipt nullability |
| 20 | 未知单位数值字段 | semantic_checks unit | receipt + 非改写披露 |

## 与相邻已合并 PR 的边界

- #1759（G01 数据资格/多角色绑定）：**权威，不重建**。本任务在其上补字段级确认回路与准备面，消费同一 descriptor/store/provenance。
- #1835（DEM 供给）、#1836/#1867（3D）：不触碰其管线。
- #1760-#1763（G02-G05 科学契约/工具一致性/制图资产/任务验收）：prepare 工具遵循其工具契约纪律（tier/domains/typed error/有界输出）。
