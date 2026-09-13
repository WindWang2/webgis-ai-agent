# ads-v1 · DS4 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS4 · 降级链与失败自愈 · ADR-0174 · 里程碑 M3（与 DS5 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 声明式条件降级链 | `source_registry.py`（`FallbackRule` + 归一化：YAML `on:` 布尔键 / 整数触发词）+ `fallback.py::resolve_chain`（多级、环安全） | `tests/unit/test_data_fabric_fallback.py`（15 测） | 真实声明链：planetary→copernicus→nasa（timeout/5xx/429）、local_osm→overpass（empty_result/timeout）；lint 校验引用存在性 |
| D3 决策逐跳落账 | `fallback.py::execute_fallback_chain` | `test_fallback_on_matching_trigger_records_decision` 等 | trigger 分类（typed 优先→内嵌状态码→截断信号）；comparable 判定（粒度/类型/覆盖，未知保守 False，声明可覆盖） |
| 不可比强制标注（硬约束） | 同上（ChainResult.non_comparable + decisions） | `test_non_comparable_marked_when_granularity_differs` / `test_comparable_true_when_facts_agree` | 粒度不一致必标不可比；一致则 True |
| 既有原语整合 | `fallback.py`（retry_call 逐跳重试 + CircuitBreakerRegistry 熔断，均复用） | `test_circuit_open_skips_source`（瞬态跳闸语义保留） | 永久错误不计熔断（既有语义）；OPEN 源跳过带 circuit_open 决策 |
| provider_health 覆盖 fabric | `provider_health.py::FabricHealthBridge` + snapshot 合并 | `test_health_bridge_mirrors_attempts`（经 async snapshot 间接覆盖） | `fabric:<source_id>` 键入监控快照 |
| A9 本地优先注册表化 | `local_first.py::registry_local_chain`（`ADS_LOCAL_FIRST_REGISTRY_DRIVEN` 开关 + 声明 priority 10/20/30） | `test_local_first_registry_chain_default_off` / `test_local_first_registry_chain_equivalence` | 缺省=硬编码链兜底；开启=注册表驱动且源集等价（gd_poi 先于 OSM） |
| 30 组故障注入矩阵 | `tests/data/test_ads4_fault_matrix.py`（5 源型 × 6 故障，离线） | 30 组参数化 + no-chain typed 失败抽查 + 30 组计数闸 | **30/30 全绿**：timeout→timeout、5xx→5xx、429→429、empty_result→empty_result、truncated→truncated（JSON 截断信号）、schema_mismatch→{schema_mismatch（形状校验型）或 empty_result（形状宽容型）}；每组断言降级落源/决策/comparable=False/fact.degraded |
| D4 事实产出 | `fallback.py`（AcquisitionFact 组装） | 各执行测试断言 fact 字段 | DS8 埋点输入就绪 |

## 波次验收对照（§6 DS4 行）

- [x] 30 组故障注入全绿且每组有结论（矩阵参数化 + 台账结论表）
- [x] `comparable=false` 场景产物必标不可比（断言：non_comparable → fact.degraded + 决策记录）
- [x] `provider_health` 覆盖 fabric 源（FabricHealthBridge 合并快照）
- [x] `local_first` 路由注册表驱动（开关化；硬编码链保留为兜底，等价性有测试）

## 矩阵结论表（5 源型 × 6 故障，全部命中预期）

| 故障 | ogc_api | wfs | arcgis | stac | stats_api | 结论 |
|---|---|---|---|---|---|---|
| timeout | timeout | timeout | timeout | timeout | timeout | 逐跳重试后降级 |
| 5xx | 5xx（包装错误内嵌状态码） | 5xx | 5xx | 5xx | 5xx | 同上 |
| 429 | 429 | 429 | 429 | 429 | 429 | 同上 |
| empty_result | empty_result | empty_result | empty_result | empty_result | empty_result | 声明触发即降级 |
| truncated | truncated（JSON 截断信号） | truncated | truncated | truncated | truncated | typed SourceBadResponseError 降级 |
| schema_mismatch | empty_result（形状宽容） | empty_result | empty_result | empty_result | schema_mismatch（声明形状校验） | 宽容型按空结果降级、校验型 typed——两类都进链 |

## 备注

- 矩阵执行要求 fixture 先装补丁再建 adapter（adapter 构造时绑定会话）——测试文件已固化该顺序并注释。
- 无迁移。
