# GOAL-20261127-03 PLAN — 概率抽样调查、专题图精度核查与面积估计

分支：`zcode/goal-probability-sampling-map-accuracy-20261127`；base `f34cb69dd`。
数学基线：Olofsson et al. 2014（DOI 10.1016/j.rse.2014.02.015），分层 SRS
不放回；**strata == 地图类** ⇒ 行和已知 ⇒ UA/PA 方差特化精确（推导见
`app/lib/survey_sampling/estimation.py` docstring）。

## 架构（三报告汇合后的最终接线）

### 纯函数库（WP1/2/4 数学内核，已完成骨架）
- `app/lib/survey_sampling/design.py` — 设计合同/分配/样本量预览/digest
- `app/lib/survey_sampling/frame.py` — 分类栅格→冻结框（分块读、测地面积核查、NoData、uint32 索引、checkpoint）
- `app/lib/survey_sampling/selection.py` — 可复现选样（层独立 SRSnwr、π/w、uuid5 sample_id、预抽替补）
- `app/lib/survey_sampling/estimation.py` — 加权矩阵/OA/UA/PA/面积 CI/诚实状态机

### 服务编排（WP3 持久化 + 旅程编排）
- `app/services/survey_sampling/contracts.py` — typed SurveySamplingError（照 raster_sampling）
- `app/services/survey_sampling/store.py` — DATA_DIR JSON/JSONL 原子存储 + revision CAS
  （先例：field_compute receipt.py、modelops evaluations 目录；**不加 DB 表**，
  避免 5 线并行迁移冲突；参考记录 append-only 保留历史）
- `app/services/survey_sampling/service.py` — build_frame/preview/create_design/
  draw_samples/record_reference/import_records/adjudicate/progress/estimate/list；
  源漂移→stale 拒绝；替换策略=预抽替补顶替非响应（原因披露）；
  盲标默认（不回显预测类）
- 源解析复用 `app/services/raster_authoring/source.py resolve_source`（ref→path+指纹）

### Pi 工具（WP5 双入口之一）
- `app/tools/survey_sampling_tools.py` — survey_frame_preview / survey_design_preview /
  survey_create_design / survey_draw_samples / survey_record_reference /
  survey_progress / survey_estimate / survey_get_estimate
- descriptor 域包：`app/lib/gis/algorithms/survey_sampling.py`（ALGORITHMS+PARAMETER_CONTRACTS）
- 登记：`app/tools/__init__.py` `_TOOL_MODULES` 追加一行

### REST（WP5 双入口之二，同 service）
- `app/api/routes/survey_sampling.py` + `app/schemas/survey_sampling_schema.py`
- `app/main.py` include；鉴权 require_owned_session + X-Session-Token

### 前端（WP5 工作台，模板=raster-sampling-panel）
- `frontend/components/sidebar/sampling-accuracy/sampling-accuracy-panel.tsx`
- `frontend/lib/api/sampling-accuracy.ts`；tab 注册 4 处（hud-types/nav-rail/MODE_TABS/context-panel）
- i18n `messages/{zh-CN,en-US}/samplingAccuracy.json`
- 误差矩阵 heat_matrix（ChartCore）+ CI 表 + 样点层（状态着色）+ 行点击定位

## 每阶段验收与证据

| 阶段 | 输入 | 验收 | 证据 |
|---|---|---|---|
| WP1+4 内核 | 数学契约 | 手算 oracle/全查归零/独立性交叉核对通过 | tests/unit/survey_sampling/ |
| 覆盖模拟 | 固定 seed 500 次重复 | OA/UA/PA/面积 CI 覆盖≈95%±MC 误差；SE≈经验 SD | test_coverage_simulation.py |
| WP2 框 | 合成分类栅格（EPSG:6933） | 计数/面积/digest/NoData/漂移拒绝；4326 大跨纬 typed 拒绝 | test_frame.py |
| WP3 记录 | journey 服务流 | 状态机/CAS 冲突/争议/历史保留/盲标 | test_service_journeys.py |
| WP5 双入口 | Pi 工具+REST+面板 | 同服务同结果；工具注册对账通过 | test_publish_and_tools/test_api_routes + panel.test |
| 旅程 1-4 | 合成数据 | goal §6 全部跑通 | test_service_journeys.py::test_journey_* |
| 性能 | 1e6/2e7 单元框 | 帧构建/估计计时+峰值 RSS；分块取消无半成品 | test_survey_perf.py（cartography-style 宽松兜底+实测打印） |
| R1/R2 | 独立审查 | P0-P2 清零；数学独立复核（手推公式 vs 实现） | REVIEW_R1/R2.md |

## 风险与对策
- 大框内存：MAX_FRAME_UNITS=2e7 硬闸 + uint32 + 分块读（先计数后索引两遍可选；v1 单遍索引数组）
- 覆盖模拟运行时长：固定小总体（N≈2000），500 重复 <30s
- OpenAPI/execution_catalog 漂移：交付前重新生成（gen_api_docs.py + execution_catalog_docs）
- 与并行线冲突：只加新文件 + `_TOOL_MODULES`/main.py/前端 4 处最小接线；PR 前 rebase 复验
