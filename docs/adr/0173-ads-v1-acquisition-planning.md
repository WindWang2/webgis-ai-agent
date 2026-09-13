# ADR-0173: ads-v1 取数计划编译器与代价估算（预算选优 + 下推最大化 + 重放）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS3（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0170（D2 AcquisitionPlan 契约）、ADR-0171（pushdown 能力声明）、ADR-0053（adapter 能力旗标）、federated costing（selectivity/rate-limit 组件复用）

## 1. 背景（缺口 A7 的下游）

没有取数计划与代价估算：请求直接打进 adapter，500 万行也会拉进内存；预算
（行数/字节/时延/配额）无处声明；超预算只会失败不会降级。缺口 A7 的阈值单点
（acquisition_limits）提供了**策略上限**，本波在其上补**计划与代价**。

## 2. 决策一：计划编译器（`planning/compiler.py`）

- `PlanRequest`（dataset_key/bbox/时间/列/聚合/抽样/limit/版本/预算）→
  D2 `AcquisitionPlan`，步骤按执行序：source_select → version_pin →
  bbox_clip → time_filter → field_projection → aggregate_pushdown →
  pagination → sampling。
- **下推诚实性**：步骤是否下推由**声明的**能力决定（注册表 pushdown +
  AdapterSpec 旗标）；不可下推的过滤器保留在计划里并标
  `pushed_down=false`——本地处理成本可见，禁止静默"优化掉"。
- **plan_id 确定性**：结构化请求 + 源的 sha256（时间戳/explain 不参与），
  同请求必得同计划（重放契约的一半）。
- 代价估算消费 A7 单点：`effective_feature_limit(50000, viewport=limit)`
  作为内联载体上限的策略来源（不重复定义字面量）。

## 3. 决策二：代价模型（`planning/cost_model.py`，复用不重写）

- rows ≈ feature_count × selectivity(bbox 面积比)；有空间直方图时走
  federated `estimate_spatial_selectivity`（复用），无直方图时面积比兜底
  （均匀假设，provisional）；
- bytes ≈ rows × bytes_per_row（信封 40B + 字段 28B + 几何 point 32B 启发式，
  provisional）；
- latency ≈ 协议基线（ogc 250ms / postgis 60ms / 本地 2ms…）+ 传输
  (bytes/200B·ms⁻¹) + 配额稀缺惩罚（复用 federated `rate_limit_request_penalty`
  语义）；
- **全部常数单点集中在 cost_model 顶部**，DS8 用实测分布替换并转定稿。
- 实测校准（fixture 网格源、服务端 bbox 过滤）：行数偏差 0%，字节偏差
  ≈18.5%（157.6B/行 实测 vs 128B/行 启发式）——P50 偏差 ≤ 30% 达标（provisional）。

## 4. 决策三：预算选优与降级建议（永不硬失败）

`choose_plan`：预算内取最小代价候选；超预算时自动生成**降级变体**
（改聚合粒度 / 25% 抽样 / bbox 收缩至 1/4 面积）并在全部超限时返回
最小代价计划 + 具体建议清单——**给建议而非报错**（任务书 DS3.3），
是否按降级继续由调用方决定，计划器绝不谎报"符合预算"。

## 5. 决策四：explain 与重放

- `explain_plan`：确定性逐行说明（每步标注"下推/本地"+代价+预算），纯函数，
  快照测试锁定；不可下推的步骤在 explain 中显式出现（"取回后本地处理"）。
- `replay`：按计划步骤执行（bbox/投影/分页/抽样），结果哈希 =
  canonical-JSON sha256(dataset_key, version, features)。**同 plan + 同版本
  pin → 同哈希**（闸测试）；版本参与哈希——`latest` 的重放等价只在源修订
  未变时成立，DS5 版本锁定波次把 pin 语义补全。

## 6. 后果

- DS4 降级链以计划为执行单位（fallback 在计划层换源重编译）；
- DS8 成本治理直接消费 cost_estimate 四元组 + 阈值校准落 cost_model 常量；
- ≥5 类源（ogc_api/postgis/arcgis/stac/geopackage/stats_api/local_file 共 7 类）
  计划生成测试通过。
