# PLAN — 里程碑与交付物

## 里程碑（对齐任务书 M1–M9；每里程碑原子 commit）

| M | 交付物 | 验证 | commit 粒度 |
|---|---|---|---|
| M1 | 5×场景 trace 与断链定位（本文档 §2 + TEST_MATRIX） | 文档评审（勘察证据 file:line 交叉） | 随 M0 一次提交 |
| M2 | `analysis_workflows/contracts.py` + `families.py`（5 族模板）+ typed 码 | TDD：contracts/families 单测（悬空工具引用 fatal、schema 版本指纹、单位合同校验） | 1 commit |
| M3 | `orchestrator.py`（角色绑定+科学前置裁决）+ `state.py` | 单测：数据缺失 typed blocked、无路网 typed blocked、分母缺失只给计数、双期未对齐 typed 拒绝 | 1 commit |
| M4 | `executor.py` + `evidence.py` + 4 Pi 工具 + 行政区设施分布族 E2E | 单测（真实 registry.dispatch）+ 验收：总数=分区合计、边界点策略显式、M:N 收口 | 1-2 commit |
| M5 | 可达性族 + 暴露族步骤模板打通（含 unit_contract 核验、阈值警示） | 单测 + 负例（无路网不得数值结论；buffer 替代须注明科学含义差异） | 1 commit |
| M6 | 时态族 + 统计分区族（对齐校验/不确定度披露/无虚假归属） | 单测 + 科学负例（年份未对齐拒绝、小样本警示、多重比较披露） | 1 commit |
| M7 | `recompute.py`（指纹/失效传播/resume 幂等）+ 取消 | 单测：输入 B 变更只失效依赖步骤；style-only 零重算；恢复不重复写产物 | 1 commit |
| M8 | receipt 映射进 Pi 上下文（有界工具契约 + evidence 摘要 + G02 终局对账） | 单测：未执行步骤不在 receipt；G02 final gate 输入一致 | 1 commit |
| M9 | G05 场景族扩充（F28 新族 + 重 pin）+ 性能/容量测试（10 万 features 限额/流式/阻断）+ 两轮独立 review + PR | G05 lab 全绿两遍；性能锁；review 清零 P0/P1/P2 | 2-3 commit |

## 实施顺序与依赖

M2→M3→M4 串行（契约→裁决→执行）；M5/M6 可在 M4 后并行开发串行提交；M7 依赖 M4 的 state；M8 依赖 M4-M6 全族；M9 收口。每个 M 完成即本地验证（见 TEST_MATRIX 命令），失败不进入下一 M。

## 场景 → 族的映射（用户自然语言入口）

1. 「分析成都市小学的服务覆盖：按区统计、可达性、低覆盖区域、交给制图」→ family=admin_facility_distribution + service_accessibility（可双族组合，composite 引用既有族）
2. 「各区小学数量与每万人密度」→ admin_facility_distribution（分母缺失→只给计数）
3. 「小学 15 分钟步行可达覆盖」→ service_accessibility（无路网→typed blocked+buffer 替代披露）
4. 「化工厂 1km 内人口/资产暴露」→ hazard_exposure
5. 「2018 vs 2023 建成区变化面积/百分比」→ temporal_change；「按网格统计案件率热点」→ statistical_zones

## 去重与缩窄记录

- 2D「多步分析编排」无在途 PR（g3d 系列全部 3D 且已合并）；本方向=2D 等价物 + 选择性重算用户可达化 + receipt 证据链。
- 不重写任何 §BASELINE 表列 production 的模块；只在断链处 additive。
- 网络恢复后 PR 创建前执行第二次去重（PR_OVERLAP_MATRIX §2 复核流程）。
