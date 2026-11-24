# ADR-0300：GIS 科学方法实验引擎（method-experiment）

日期：2026-11-25 · 状态：已实现 · 关联：02 任务（GIS 算法库与科学方法实验引擎）、ADR-0284（typed 分析产物封套与混合方法链）、G02（AnalysisWorkflowContract）

## 背景与问题

同一空间问题往往存在多个合理的科学方法/参数组合（例：插值用 IDW 还是
克里金？KDE 带宽取 500m 还是 2000m？聚类 k 取 3 还是 8？）。此前的仓库
能力面有两个缺口：

1. **单点选择，无公平比较**：`interpolation_model_compare` 只覆盖插值族
   且方法序/预算硬编码；方法学知识层（`app/lib/gis/methodology/`）回答
   「哪个方法**在资格上成立**」，不回答「成立的候选在**本数据上谁表现
   更好、好多少、换个参数会怎样**」。
2. **比较结果无法沉淀**：没有可版本化的实验计划、没有跨试验的复用与
   成本记账、没有「用户采纳」的证据链——选型知识只能靠口头惯例传递。

## 决策

新建独立包 `app/services/gis_harness/method_experiment/`（与
`analysis_workflows` 平行的薄编排层），六个模块：

| 模块 | 职责 | 不做的事 |
|---|---|---|
| `cards.py` | 五族科学方法卡（候选空间/参数角色/协议建议/局限），全部引用既有 registry id | 不复制算法语义；LLM 生成的文字不进卡（与 methodology/provenance 同红线） |
| `metrics.py` | 指标契约（方向/单位类/可比组） | 不实现任何指标计算 |
| `protocol.py` | 评价协议（折分配引用 cv.py 词表；kind=cv/stability/conservation/scenario） | 不新造 splitter；没有真值的族不产生 RMSE |
| `plan.py` | 确定性计划编译 + 执行前 typed 拒绝 + 预算硬门（≤24 试验） | 不执行计算 |
| `runner.py` | 试验循环（真实 ToolDispatchService 分发、内容 hash 复用、取消/预算/部分结果） | 不旁路 dispatch；不虚构 ETA |
| `evaluate.py` | 可比组裁决、Pareto、证据推荐或弃权、采纳回执 | 不做跨指标归一化、不加权拍脑袋 |

执行真相：试验 = 候选声明的**真实注册工具**调用（经与 Pi 直调同缝的
ToolDispatchService）。为此补了三个 master 缺失的评价原语工具
（`app/tools/science_eval_primitives.py`）：

- `interpolation_block_cv` —— cv.py 编排 + 既有 trend/rbf/kriging 驱动
  复用（全部候选共享同一确定性折分配 = 公平性来源）；
- `density_edge_diagnostic` —— KDE 质量守恒/边界外溢诊断（显式逐点带宽
  评估路径；见下方「发现面」）；
- `cluster_stability` —— kmeans 跨 seed / dbscan bootstrap 子采样 ARI +
  轮廓 + 噪声占比。

UI 与 Pi **同链**：`service.py` 是唯一服务实现，六工具
（`webgis_method_experiment_*`）与六 REST 端点
（`/api/v1/method-experiment/sessions/{sid}/...`）共用——参数契约、授权、
审计零分叉（G41/G46/G47 同模式）。

## 为什么不另建系统（红线对账）

- **不是第二 registry**：卡/指标全部引用既有 id，`validate_cards` 悬空
  fatal；聚类算法描述符（`spatial.cluster.kmeans/.dbscan`）是给**既有**
  `cluster_narrated` 实现补注册，不是新算法。
- **不是第二 evaluator**：评价消费工具产出的真实指标；验收语料走既有
  pytest 门（见测试），不建平台级评分器。
- **不是第二工作流引擎**：单试验即单工具调用（无步骤 DAG）；需要多步
  链时用户应使用 `webgis_analysis_workflow_*`（G02）。本引擎只在
  「多候选 × 同协议」这个维度上扩展。
- **不采纳即改图**：`adopt` 只记 decision receipt；后续产品化走既有
  分析/制图工具链。

## 诚实性语义（不可协商）

- 试验结局词表 `completed / reused / not_evaluated / skipped / failed /
  cancelled`——未评估不是零分、不是失败、绝不冒充完成；
- 推荐必须 ≥2 个可比值，否则**弃权**（`insufficient_completed_trials` /
  `metrics_not_evaluated`）；
- 跨数据集/量纲不同的指标并列披露不可比原因，绝不归一化排名；
- 方向由 `metrics` 注册表唯一裁决；tie-break = candidate_id 字典序
  （全确定性、可重放）。

## 发现面（本 ADR 附带的既有缺陷披露）

锁定的 scipy==1.18 下，`gaussian_kde.evaluate` 会在评估期调用
`_compute_covariance()` 重算 `cho_cov`，使 density.py `_fit_kde`（#384
契约）的带宽覆盖**不再生效于评估**（隔离脚本验证）。本任务不修生产
KDE 工具（不属本任务范围、且行为改动影响面大）；`density_edge_diagnostic`
改用 `_evaluate_adaptive_kde` 显式逐点带宽路径（恒向量=固定核），带宽
真实生效。生产 KDE 工具的修复建议单独 issue。

## 测试与验收

`tests/unit/gis_harness/method_experiment/`（47 项）：计划确定性/typed
拒绝/预算钳制；runner 诚实语义（失败隔离/未评估/取消保留前缀/窗口预算/
内容寻址复用）；评价（方向/tie-break/Pareto/弃权/采纳拒绝路径）；原语
**独立 oracle**（测试内独立实现折分配+IDW 内核，对照 <1%）；真实
`init_tools` + 真实 ToolDispatchService（含 Pydantic/未知参数闸与 ref
解引用）的插值/密度/网络/**自相关**实验全链（幂等重跑零新 dispatch、
跨候选同折分配公平性、网络情景覆盖单调性、scenario 族推荐弃权）。
独立审查（round 1）发现的 args/schema 失配与臆造 stats 键已修复并以
真实 dispatch 回归锁钉住（review-fix commit c91ce30ac）。

## 兼容性

全部 additive：新包、新工具模块、新 REST 前缀、`algorithms/__init__`
与 `capabilities/statistics.py` 各一处 additive 登记、`app/main.py`
一处 router 注册、`app/tools/__init__.py` 两处注册行。零既有行为变更
（259→261 算法、544→550 工具均为净新增）。
