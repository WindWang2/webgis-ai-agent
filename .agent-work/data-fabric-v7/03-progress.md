# V7 进度记录（真实执行结果）

- 2026-09-09 Phase A：只读审计完成 → 00-baseline.md（23.6k 行 data_fabric 审计、
  P0-P3 分级、V6 已知限制继承清单）
- 2026-09-09 Phase B：01-architecture.md 冻结；Subagent-A 架构挑战 → 3 CRITICAL
  （聚合下推证明不完备 / delivered_crs 校验不可实现 / observe_row_count 契约
  破坏）+ 4 MAJOR → 全部修订（§8 R-C1/C2/C3、R-M1/M2/M3）
- 2026-09-09 W1-2：ConnectionRegistry（scope/content-revision/secret 分离/
  健康状态机/生命周期）+ P1 修复（manager/routes 六处 profile 重建恢复凭证）。
  测试 29 passed（test_fabric_connection_registry + federation 回归）。
- 2026-09-09 W3：CapabilityProbeService + AdapterCapabilitiesV2 additive
  （output_crs_pushdown / filter_encoding）。7 passed。
- 2026-09-09 W4：SourceFactsService + DurableSourceFactsStore + migration 0034
  （单 head 验证）。9 passed + test_deploy_migration_wiring 20 passed。
- 2026-09-09 W5：compile_predicate_cql2_json + OGC cql2-json 协商 + STAC
  filter extension 探测/下推。8 passed + adapter 回归 47 passed。
- 2026-09-09 W6：GeoParquet iter_query_arrow_batches + fabric/arrow_lane +
  physical 委托/诚实回落。5 passed + 22 回归。（修复：@staticmethod 误装饰、
  _needed_columns 空投影语义）
- 2026-09-09 W7-8：costing 不确定性乘子/限流惩罚 + server CRS placement
  （decide_crs_transform server_feasible 通道门控、计划树 output_crs、执行期
  delivered_crs 账本、mismatch 一次性重扫回退、PostGIS/ArcGIS delivered_crs
  metadata）。costing/enumerator 测试更新 + 3 个新测试。
  **发现并修复 V6 潜在缺陷**：PostGIS/ArcGIS 默认交付 4326 与声明原生 SRID
  的本地对齐 → 双重变换。
- 2026-09-09 W9：safe aggregate pushdown（aggregate_pushdown_proof R-C1、
  planner 树改写、executor 组行拉取+精确投影、ChainSource.source_type/
  ChainSourceStats.unique_keys additive）。唯一键 on/off 差分锁定。
- 2026-09-09 W10：bushy 整树重排（_maybe_bushy_replan + federation
  _make_bushy_replan_fn；偏差触发/严格更优/given 禁用/1 次上限）。
- 2026-09-09 W11-13：FabricFeedbackStore（衰减中位数+R-C3 守卫+durable）、
  FederatedResultCache（披露式命中+owner 隔离+负缓存+双界）、FabricCounters
  + execute_chain_v6 fabric 段 + 工具 use_cache/fabric/result_cache 透出。
  10 passed。
- 2026-09-09 W14-16：SSRF/过期/无 secret 面回归 + 4 源差分语料（v5/v6 ×
  cache on/off × replan on/off 精确一致）+ 结构性 perf/chaos（请求数预算、
  行预算 fail-fast、缓存命中 0 远端请求、源骤变语义保持）。12 + 4 passed。
- 2026-09-09 W17：ADR-0119 + CHANGELOG + 本目录文档。

## 联邦全量回归（每 wave 后跑）
- 2026-09-09（W7-10 后）：212 passed
- 2026-09-09（W11-13 后）：60 passed（增量面）
- 最终矩阵见 04-test-matrix.md
