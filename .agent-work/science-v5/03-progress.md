# Science V5 — Progress（真实执行记录）

## 已完成（每 wave 一个 commit）

| Wave | Commit 内容 | 测试 | 结果 |
|---|---|---|---|
| W0 | 56fa435a docs: 审计基线 + 架构冻结 + Subagent-A 挑战修订（14 项发现） | — | — |
| W1 | CV 框架 cv.py（spatial_block 上收别名/temporal_forward/编排+泄漏守卫）；kriging 别名 re-import | 33 | 绿（kriging 回归 99 + oracle 1091） |
| W2 | multi-start polish（仅失败路径）+ select_variogram_model diagnostics 旗标 | 10 | 绿（oracle 1091 主路径逐位不变） |
| W3 | LMC 堆叠批量 solve（显式 (c,m,1) 右端——numpy 2.5 向量广播语义变化）+ LinAlgError∨非有限隔离 | 4 differential | 绿（逐位一致 + V4 回归 7） |
| W4 | ST 定长填充批量系统（哨兵行列清零含约束行列——挑战 C2）+ st_cross_validate | 10 | 绿（4 differential 逐位 + 邻接回归 101） |
| W5 | SGS batched（P 组共享路径 + chunk 堆叠 solve，权重组内复用）+ **两处 P1 修复**（序贯区制 sim-sim 互协方差 + 条件树位置索引经 path 映射）+ 动态上限 | 10 | 绿（两 backend 各自逐位确定；统计 differential corr 0.993/std 比 0.999；V4 SGS 回归全绿） |
| W6 | UncertaintyArtifact（estimator 封闭词表/模型vs数据质量分离/R<2 诚实缺省） | 13 | 绿 |
| W7 | plan_execution（native/vectorized/chunked 封闭词表）+ numpy_batched 变体（raster_cells 窗口）+ 三驱动接线（uncertainty + execution_plan） | 15 | 绿（registry/parity/drift 门全绿） |
| W8 | TemporalCube + phenology（缺口填充/SG/双谐波/SOS-EOS-LOS）+ anomaly + 3 工具 + 契约 | 28 | 绿（时序邻接回归 95） |
| W9 | 多级 Pfafstetter（共享走法 helper，L1 逐位一致；拼接层支流≤3 位码约束）+ flow topology 校验 + 工具分支 + 契约 v2 | 13 | 绿（hydrology V4 回归 11 + drift 门） |
| W10 | oracle science_v5 域 35 case + work-count benchmark（solve 调用随组数不随实现数） | 35 + 4 | 绿（oracle 总 1126） |
| W11 | 生成物再生成（catalog 1544 行/manifest/quality/staleness ledger）+ CHANGELOG | gates | 见下方门禁清单 |

## P0/P1 修复记录（本 Epic 域内，规则 17）
1. **SGS 序贯区制 sim-sim 对角近似**（reference+batched）：非一致协方差模型
   → 非正定系统 → var 爆至 ~40×sill（>1 chunk 才触发，≤1 chunk 区制行为
   不变、oracle 全绿）。修复 = 真实互协方差 + 对角 ridge。
2. **SGS 条件值索引错位**（reference）：sim_k_i 是条件树位置索引，需经
   path 映射回目标序号再取模拟值；V4 直接索引在多 chunk 时坐标/值错配。

## 已知偏离计划（如实记录）
- 架构原计划新增 compare_variogram_models —— 发现 V3 已有
  select_variogram_model（AICc 排名），改为扩展诊断旗标（避免第二入口）。
- temporal_forward 块 0 设计为纯训练库（fold_id=-1）——比计划文档更严格
  的前向链语义（首块无过去可训）。
- L1 Pfafstetter 未按初稿委托多级函数——V4 测试锚定单级 meta 契约
  （mainstem_cells 等），改为共享走法 helper + 独立 meta。
- numpy 2.5.3 堆叠 solve 不再自动按向量情形广播 → 显式 (c,m,1) 右端项。
