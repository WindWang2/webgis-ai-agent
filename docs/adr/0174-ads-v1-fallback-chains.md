# ADR-0174: ads-v1 声明式降级链与失败自愈（条件触发 + D3 决策 + 30 组故障矩阵）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS4（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0171（注册表 fallbacks 声明）、ADR-0151（制图面降级链，同构独立命名空间）、reliability/circuit_breaker（原语复用）、provider_health（监控面扩展）

## 1. 背景（缺口 A4）

取数侧没有备用源切换：`gis_harness` 的 fallback_chain 只服务制图面（ADR-0151），
fabric 侧只有 `reliability.retry_call` 单点重试。源挂了就失败，没有自愈。

## 2. 决策一：声明式条件降级链（注册表）

- 源声明 `fallbacks:`：裸 id（任意故障触发）或条件规则
  `{source_id, on: [timeout|5xx|429|quota|empty_result|truncated|schema_mismatch|circuit_open], comparable, note}`；
- **YAML 坑修**：裸 `on:` 键被 YAML 1.1 解析为布尔 True、裸 `429` 解析为整数——
  注册表加载层统一归一化（bool 键回 "on"，触发词转 str），保持自然语法可用；
- `resolve_chain`：多级链解析，深度限 5、环安全（已见源跳过）；
- 真实声明：planetary_computer → copernicus → nasa_cmr（timeout/5xx/429）、
  local_osm → overpass_api（**empty_result/timeout** ——「本地未命中出网」的
  声明化，A9 本地优先语义进入注册表）。

## 3. 决策二：链执行器（`data_fabric/fallback.py`）——原语复用不重写

- **逐跳重试**：`reliability.retry_call`（is_transient 分类、保守 POST 规则、
  零退避缺省有界）；
- **熔断**：fabric 自己的线程安全 `CircuitBreakerRegistry`（按 source_id）为
  同步权威——OPEN 源以 `circuit_open` 决策记录跳过；**只统计瞬态故障**
  （永久错误不是"源宕机"的证据，既有语义保留）；
- **健康镜像**：每次尝试镜像进 `provider_health` 新增的 `FabricHealthBridge`
  （线程安全同步记账、`fabric:<source_id>` 键、snapshot 合并输出）——
  **provider_health 监控面自此覆盖 fabric 源**（原仅 5 个在线地图 provider）；
- **D3 决策逐跳落账**：trigger 分类（typed 错误优先，消息内嵌状态码次之，
  JSON 截断信号 "unterminated/expecting" → truncated）+ comparable 判定
  （粒度/数据类型/覆盖一致才可比；事实未知 → 保守 False；声明
  `comparable` 可覆盖）；
- **D4 事实汇总**：一次链执行产出一个 `AcquisitionFact`
  （rows/bytes/latency/retries/degraded/outcome/fallback/wave），DS8 落库。

## 4. 决策三：不可比强制标注（任务书硬约束）

备用源与原源的 granularity/data_type/coverage 不一致（或事实未知、或声明
不可比）→ 决策 `comparable=False`，结果负载标记 degraded——**静默换源被
结构性禁止**：下游拿到的 ChainResult 同时携带决策记录与不可比标记。

## 5. 决策四：本地优先注册表化（A9/DS4.4）

`local_first.registry_local_chain()`：`ADS_LOCAL_FIRST_REGISTRY_DRIVEN` 开启时，
本地链参与源由注册表声明（`local_*` 源按声明 `priority` 排序——
local_poi(10) → local_osm(20) → local_yearbook(30)；未声明的本地源自动出局）；
缺省关闭 = 硬编码链 gd_poi → OSM 兜底保留。等价性测试锁：注册表驱动的
源集合 ≥ 硬编码链且 gd_poi 先于 OSM（DS9 确认等价后删硬编码）。

## 6. 决策五：30 组故障注入矩阵（验收工件）

`tests/data/test_ads4_fault_matrix.py`：5 类源（ogc_api/wfs/arcgis/stac/stats_api）
× 6 故障（timeout/5xx/429/empty_result/truncated/schema_mismatch），全部
`ADS_FORCE_OFFLINE=1` 离线执行，每组断言：
- 正确的 trigger 分类（形状宽容型 adapter 对未知 JSON 载荷按 empty_result
  降级——矩阵触发集按故障记录全部可接受分类，实测结论落在台账）；
- 降级到 fallback 源、D3 决策 comparable=False（事实未知保守缺省）、
  D4 fact.degraded=True；
- 无链时全部故障 typed 失败（5 协议抽查），绝不伪造成功。

## 7. 后果

- DS5 版本 pin 在链执行之上（pin 先于 hop 解析）；
- DS8 埋点消费 ChainResult.fact；熔断状态（CircuitState）随 D4 入库可观测；
- 迁移未用（本波无新表）。
