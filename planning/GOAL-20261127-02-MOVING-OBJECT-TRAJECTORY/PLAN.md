# PLAN — GOAL-20261127-02 移动对象轨迹、时空事件与地理过程分析

## 架构总览（复用优先）

新增独立领域模块 `app/services/moving_object/`（02 线独占移动对象语义），沿现有扩展点接线：

```
app/services/moving_object/
  __init__.py
  contracts.py      # WP1: EntityObservation / TrajectorySpec / TrajectorySegment / 质量合同（pydantic）
  ingest.py         # WP1: 点表→实体观测流（排序/去重/时区/异常诊断，原始点不可变）
  builder.py        # WP2: 轨迹构建（分段、断点阈值、观测间线性假设声明）
  motion.py         # WP2: 运动量（时差/累计距离/速度/方向/驻留；无 inf/假零）
  dwell.py          # WP2: 停留识别（半径+时间阈值+最小点数；证据 refs）
  quality.py        # WP2: 质量诊断与清洗建议（诊断与清洗分离，清洗出 preview）
  events.py         # WP3: 区域 enter/exit/cross/dwell/unknown 事件（边界包含规则、洞、推断区间）
  aggregate.py      # WP3: 实体级事件表→区域级汇总（有效分母/缺测/stale 语义）
  service.py        # 双入口共用编排：inspect/build/analyze/query（幂等/CAS/预算/取消）
  oracles.py        # WP6: 合成轨迹生成器（测试专用，可复现）
app/tools/moving_object_tools.py   # WP5: 工具面（inspect/build/events/summary/visualize）
app/services/moving_object/README 或 docs/adr/0306-*.md  # ADR
```

- **数据面**：轨迹点/派生线/事件表按 fetch-on-demand ref 存放（沿 od_table_ref 的 ref_cursor 模式 + artifact/durable blob store），LLM 只见有界摘要与样本。
- **MapSpec**：新增 trajectory 图层表达（MapSpec additive 合同），速度/时间编码沿 #2266 流向样式模式；事件标记用点样式。
- **前端**（Next.js App Router：`frontend/app|components|lib`）：新增轨迹工作台面板（沿 `frontend/components/flow/flow-product-panel.tsx` 挂载于 `map-studio-tab.tsx` 的模式；网络边界隔离在 `frontend/lib/moving-object/`），地图↔列表↔时间轴联动复用 `lib/selection/selection-store.ts` 发布/订阅与 `lib/temporal-map/linkage.ts` 时窗语义。
- **Pi**：工具注册沿 `register_moving_object_tools(registry)` + `app/tools/__init__.py` + `categories.py`；能力/证据沿现有科学证据挂载。
- **不动**：vendor/pi、temporal engine 既有面（只读复用）、OD/拓扑/发布/工程包。

## 阶段计划

| 阶段 | 内容 | 输入 | 验收/完成证据 |
|---|---|---|---|
| S0 探索 | 后端/前端/Pi 契约盘点 | master 代码 | 契约笔记（本文件附录） |
| S1=WP1 | contracts + ingest + 单测 | temporal models、SessionStore ref 模式 | 单测覆盖乱序/重复/时区/DST/断点/无 ID；`pytest tests/unit/moving_object -q` 绿 |
| S2=WP2 | builder + motion + dwell + quality + 单测 | geo_analysis 测距 | oracle 单测：直线/折线/双对象/跨反经线/零时间/噪声；无 inf |
| S3=WP3 | events + aggregate + 单测 | temporal filter 语义 | enter/exit/cross/dwell/unknown oracle；洞/多面/边界/跳过区域 |
| S4=WP5后 | 工具面注册 + 能力/证据 + 真实数据面 ref | registry/categories | 工具 schema 出现在 registry；工具级单测 |
| S5=WP4 | MapSpec 图层 + 前端面板 + 联动 | flow_product 前端模式、#2285 联动 | vitest 组件测试；typecheck 绿；旅程 1 手动验证记录 |
| S6=WP6 | 规模验证 1k/10k/100k + 负例 + R1 | S1-S5 | perf 数据（p50/p95/内存）；R1 修复清单 |
| S7 收口 | R2、必过旅程 1-4、PR | 全部 | journey 记录、PR_BODY、gh pr create |

## 预算分配（总 3.1 亿 tokens 规划）

WP1 40M / WP2 55M / WP3 65M / WP4 55M / WP5 55M / WP6 40M。

## 风险与对策

- 前端工作台挂载点未知 → S0 探索确认（进行中）
- 时间轴组件是否已有可复用 → temporal_view / temporal_media 前端确认
- Pi RPC 本地验证需要受控 provider → 沿 tests/test_pi_e2e.py 既有模式
- 大 worktree 编译资源 → -j4 上限，pytest 单进程分片
