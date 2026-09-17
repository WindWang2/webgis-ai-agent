# ADR-0202: 离线/内网/信创部署 profile（offline / air-gapped sovereign deployment v1）

- 状态：Proposed（随 `platform/offline-airgapped-profile-v1` 分支评审）
- 日期：2026-09-17
- 基线：origin/master `faa453a8935101378c23eb6694a42c3616d9c670`
- 关联：ADR-0094（Data Fabric 安全面 / SSRFSafeHTTPAdapter）、#925（LLM 内网
  endpoint 豁免）、#662（RAG_EMBEDDING_OFFLINE）、ADR-0104/ADR-0119
  （扩展平台 network allowlist / 隔离）、ADR-0138（typed 错误信封）

## 背景

政府/自然资源等场景要求系统部署在**无互联网出口**的内网/信创环境。master
已有零散的离线能力（`EXTENSION_NETWORK_ALLOW`、`MODELOPS_REMOTE_ALLOWLIST`、
`RAG_EMBEDDING_OFFLINE`、`LOCAL_QUERY_FIRST`、`NEXT_PUBLIC_MAP_GLYPHS_URL`），
但没有系统级的离线部署语义：无法声明"本部署不出网"、无法在运行期对出网
做统一策略拒绝、无法机器可读地回答"哪些能力离线可用"。外部 provider、
tiles、Data Fabric 远程源、模型接口持续增多，缺口随并行方向扩大。

## 决策

### D1 — 单一部署开关：`DEPLOYMENT_PROFILE = cloud | air_gapped`

`app/core/config.py`。默认 `cloud` = 行为与合入前逐字节一致（零变化
Oracle）。`air_gapped` 在 Settings validator 强制 `NETWORK_EGRESS_MODE=
allowlist`（声称离线却不拦截出网 = 矛盾配置，启动即拒绝），并要求
`LLM_BASE_URL` 能过 egress 决策（chat 是启动即必需的能力，矛盾提前到
启动期；可选远程能力不启动拦截，运行期 typed 降级）。

### D2 — 出网守卫：deny-by-default host allowlist

`app/core/egress.py`：纯函数决策面（`EgressPolicy.decide`）+ typed
`AirGappedEgressError`（host/reason/dependency_id 进 evidence）。allowlist
模式放行三类目标：显式 `NETWORK_EGRESS_ALLOW`（精确或 `*.suffix` 通配）、
私网/回环/链路本地（`NETWORK_EGRESS_ALLOW_PRIVATE`，可关）、其余全拒；
云元数据端点（169.254.169.254 等）即使私网豁免也永远拒绝。拒绝词表封闭：
`not_allowlisted | private_blocked | metadata_blocked | invalid_url`。

### D3 — 三大传输接缝 + ad-hoc 收编

- aiohttp：`app/core/network.py` 共享池/工厂在 allowlist 模式挂
  TraceConfig（连接前拒绝）；unrestricted 不装任何东西。
- httpx：LLM 池（`LLMHttpClientRegistry`）client 挂 event hook；
  `guarded_client()/guarded_async_client()` 是 ad-hoc 客户端统一入口
  （vlm_provider、visual_evaluator、extensions broker、modelops remote、
  gov adapter 已迁移；health 探针与 local_admin/local_stats 前置断言）。
- requests：`SSRFSafeHTTPAdapter.send()` 守卫先于 SSRF 门（每跳 redirect
  都过策略）；`DataFabricSecurity.validate_url` 在 probe 面把策略拒绝
  映射为数据面原生 `SecurityBlockedError`（permanent，不重试）。

分层关系：egress 守卫与 `EXTENSION_NETWORK_ALLOW`、
`MODELOPS_REMOTE_ALLOWLIST` 是 **AND 叠加**，互不替代。

### D4 — NetworkDependencyCatalog：清单是登记的事实，不是扫描的推测

`app/core/network_dependency.py`：17 类出网依赖（LLM/VLM/geocoder/底图/
OSM/web 搜索/STAC/远程栅格/政府门户/统计 API/对象存储/ModelOps/HF 下载/
Data Fabric 远程源/前端瓦片），每条含端点（Settings/env 动态解析）、调用
面、离线替代、**是否受运行时守卫覆盖**。pystac-client、rasterio /vsicurl、
DDGS、HF hub、浏览器侧瓦片在第三方库/浏览器内部建连，显式标记
`enforced_by_egress_guard=false`——不谎称全防；最终防线是部署网络层
（防火墙/netns）。工具面 `network=True` 声明经 `tool_network_matrix()`
交叉引用，附带静态离线替代映射。

### D5 — 观测与体检

- `/api/v1/status/detailed` 增 `network_policy` 组件（纯配置投影，无 IO；
  `sre_metrics` 词表同步）。
- `manage.py preflight`：11 项组件化检查，required-down → exit 1（可作
  compose/systemd 启动前门禁）；空本地数据目录 = degraded 而非阻断。
- `manage.py network-catalog`（JSON/table）、`manage.py sbom`（Python
  发行 + 前端依赖声明，仅元数据）、`manage.py asset-manifest`（运营侧
  预置资产登记，status ∈ present/absent/operator_supplied）。

### D6 — 前端

`frontend/lib/providers.ts` 增 `local-xyz` provider（
`NEXT_PUBLIC_LOCAL_BASEMAP_URL` 注入；未配置诚实缺席）与
`getAvailableTileProviders()`（air_gapped 只暴露本地底图）。底图切换器
UI 过滤为后续项（`MAP_STYLES` 同序号耦合，属 #1353 前端热区，本分支不碰）。

### D7 — 不声明信创实测

本分支只交付：可移植性 contract 测试（路径/编码/换行/文件锁）、依赖与
资产清单、部署运行手册。**未在任何国产 OS 上做实测**，文档不声称支持
特定 OS 发行版；合规认证属运营/采购流程，不是代码仓库可断言的事实。

## 兼容性 / 回滚

- 全部新配置键默认值 = 旧行为；cloud profile 下守卫零安装、零拦截。
- kill-switch：`DEPLOYMENT_PROFILE=cloud` 一键回滚（air_gapped 下不允许
  `NETWORK_EGRESS_MODE=unrestricted`，回滚即切回 cloud）。
- 无 DB migration；`/status/detailed` 只增字段；无 API 破坏性变更。

## 已知边界

1. vendor/pi 子进程的 LLM 调用（stdio JSON-RPC）不可从 Python 层拦截；
2. pystac-client / /vsicurl / DDGS / HF hub / 浏览器瓦片不经守卫（清单
   登记 + preflight 报告 + 网络层兜底）；
3. 底图切换器 UI 未按 profile 过滤（registry/helper 已就绪）；
4. `network=True` 工具的离线替代映射是静态保守表，随工具演进维护。
