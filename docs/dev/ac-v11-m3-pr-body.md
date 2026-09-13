## M3 · 标注深化与版面叙事（W4 + W5，ADR-0164/0165）

> 关联任务书:`goals-ac/V11-master-adaptive-cartography-1B.md`(§3 W4–W5/§6/§7)。
> 分支策略(§0.6):同分支叠加(M1 PR #1271 合入后本 PR 即只含 W4+W5)。
> **按 §7 纪律,本 PR 不自行合并。**
> 注:GitHub 一分支一开放 PR —— 本 PR 在 #1271 合入后以本文件内容创建
> (分支已就绪:86d025b5,含 M3 评审修复 commit;tag `ac-v11-w4` / `ac-v11-w5`)。

### 1. 目标

- **W4(ADR-0164)**:C3 LabelPlan 定稿、交互侧网格碰撞(缺口 G5)、专业排版、
  避让优先级、前端字段兜底、性能预算。
- **W5(ADR-0165)**:G1 备选版面生产接线、G2 自愈 planned→executed、
  多图版面、StoryMap 大纲、版面五维评分。

### 2. 波次交付摘要(台账:`ac-v11-w4-ledger.md` / `ac-v11-w5-ledger.md`)

- **W4**:C3 只加不改(collision/typography 可选段,maplibre = V10 回滚开关);
  `label-grid.ts` 交互侧网格碰撞 —— **200 点密集阵实测重叠率较无避让基线
  下降 ≥40%(硬断言) + 放置率 ≥0.5 防抑制刷分**;MapLibre 内置避让保留为
  兜底;多语言断行(CJK 按字/拉丁按词/溢出可见截断/auto);面内标注最大
  内接圆;`labelPriorityScore`(重要性×类别×面积,与 W5 共享);字段兜底
  degraded 诚实标记;10k/50k 性能预算(实测 ~0.5s/~3s)。
- **W5**:G1 —— planner 投影缝(几何派生 artifact 集)→
  `composition_alternatives_payload`(count==3 测试收紧)→ template_selection
  证据(grep 断言生产调用);G2 —— compose.ts 四级链**应用到渲染面**,逐动作
  状态诚实(仅已应用记 executed,shrink 保持 planned),安全网并入注入前
  autofill 流(`__fallback_*` 代号归零);atlas 逐页 C2 IR(≤20 页);
  story 四幕大纲(missing 诚实);五维版面评分。

### 3. 数值对照表(M2 → M3)

| 指标 | M2(W2+W3) | M3(W4+W5) | 备注 |
|---|---|---|---|
| cartography lane 覆盖率 | 55.40%(9047 stmts,891 tests) | **57.43%**(9217 stmts,907 tests) | +16 lane 测试,floor 50 |
| 交互侧标注避让 | MapLibre 贪心 | +确定性网格(重叠率 **-≥40%**) | 放置率 ≥0.5 伴随约束 |
| 版面自愈 | planned 记录 | **executed**(渲染面真实改变) | 逐动作状态 |
| 备选版面 | 纸面算法 | 生产调用(count=3 + 评分) | grep 断言 |
| 专业排版 | 宽度贪心 | CJK 按字/拉丁按词/可见截断 | 中英混排用例 |
| 标注性能 | 无预算 | 10k/50k 显式预算(实测 ~0.5s/~3s) | |
| frontend vitest | 3620 | 3627+(**修复 1 例时序 flake**) | 全绿 |
| 共享几何 | Python 单点 | +TS `label-geometry.ts`(solver/grid 收敛) | parity 223 |

### 4. 门禁原文(§6;SKIP_BROWSER=1 完整形态,M3 终版)

```
[gate] 1/4 cartography lane 独立覆盖率闸（floor=50）
907 passed, 21 skipped, 16693 deselected, 93 warnings in 81.49s
  app/lib/cartography :  9217 stmts,  57.43%   ← 闸 scope
  判定: ✅ 通过
[gate] ✅ 全部步骤通过（quality_gate_local.sh）
```
（评审修复 86d025b5 后的终版重跑;golden 步骤按 SKIP_BROWSER=1 跳过——浏览器取证随 W8 基线批。）

### 5. /code-review findings 处置(§7.2,双轴;修复 commit 86d025b5)

**已修复:**
1. [Standards] compose.ts 头部与 executed 行为矛盾 → 头部对齐;
   安全网曾在注入循环**之后**并入(决策声称 present 而渲染面无物)→ 移到
   循环前;repair 逐动作状态(仅已应用记 executed,shrink 如实 planned)。
2. [Standards·诚实] 断行「绝不丢词」措辞与可见截断不符 → 措辞对齐
   (可见截断,与 CJK 末行同规)。
3. [Standards·诚实] label-grid 头部声称已接线/visibility 字段 → 对齐
   (dx/dy 供 W6 消费方映射;去掉不存在的字段声称)。
4. [Standards] layout_score 密度带 docstring 与代码不符 → 对齐。
5. [Standards] label_plan 默认值字面量双写绕过模型 → 由模型构造单点。
6. [Standards·dedup] label-grid 复制 solver 几何 → 抽 **共享
   `frontend/lib/label-geometry.ts`**,两消费方导入(parity 223 全绿)。
7. [Spec] G1 投影 count==1(artifact 集恒空/`intent.data_kind` 不存在)
   → 几何派生 artifact 集 + profile 字段类型派生 variable_kind;测试收紧
   count==3。
8. [Spec·防刷分] 重叠率指标可被「全抑制」刷 → 增放置率下限断言。
9. [质量] collab.test.ts 固定 20ms 睡眠全量负载下 flake(隔离全过)
   → vi.waitFor 轮询(与同文件既有稳定性修复同款)。

**书面反驳/移交(与波次边界一致):**
- W4.2「生效」:求解器 + 40% 硬断言交付;**runtime 消费接线**归 W6(与
  三渲染器收敛同批,台账登记;头部措辞已诚实化)。
- W4.3 三类排版 golden / W5.3-W5.4 渲染端到端 / W5.5 入 C4+ratchet:
  编排层交付,渲染与事实库消费归 W6/W8(任务书职责划分)。
- W4.6「入 perf 基线」:预算断言在 cartography lane;solver 侧 perf 基线
  文件随 W8 golden 批统一。
- C3 collision/typography 的 maxDisplacement/suppressOverflow 消费:
  契约已冻结,W6 渲染器消费时启用。

### 6. 风险与回滚

- G2 为行为变更(自愈真改渲染面):compose 22 例更新后全绿;回滚 =
  tag `ac-v11-w4`/`ac-v11-w5` 或 W4 的 `collision.strategy="maplibre"`。
- 共享几何抽取:parity corpus(frozen twin)223 全绿 = 行为零变化实证。

### 7. 与 V10 十线的兼容

- C3 只加不改(新段默认 None/语义等价 V10);安全网 id 更名保留老工件
  origin 兼容读;label-solver 公共 API 不变(内部改 import)。目录边界内,
  未触碰 `.github/workflows/**`。
