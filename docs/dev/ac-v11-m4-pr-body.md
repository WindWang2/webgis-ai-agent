## M4 · 出版交付与闭环智能（W6 + W7，ADR-0166/0167）

> 关联任务书:`goals-ac/V11-master-adaptive-cartography-1B.md`(§3 W6–W7/§6/§7)。
> 分支策略(§0.6):同分支叠加(M1–M3 合入后本 PR 即只含 W6+W7)。
> **按 §7 纪律,本 PR 不自行合并。** GitHub 一分支一开放 PR —— 本文件在
> 前序 PR 合入后用于创建（分支已就绪;tag `ac-v11-w6` / `ac-v11-w7`）。

### 1. 目标

- **W6(ADR-0166)**:G3 收敛骨架（IR 三渲染器 parity）、G9 收口（PDF 图体
  矢量、高分评估、批量队列、可访问性、格式矩阵）。
- **W7(ADR-0167)**:G6 fallback（本地确定性视觉判据）、自愈策略库与归因、
  动作空间扩展、阻断切换（默认关 + 一键回滚）。

### 2. 交付摘要（台账:`ac-v11-w6-ledger.md`；W7 见 ADR-0167）

- **W6**:`ir-parity.test.ts` 三方锚点对拍 + Z 序语义锁;`lib/map-exporter/`
  残壳清理;`pdf-vector.ts`（svg2pdf.js 路径嵌入,无 Image XObject 实证,
  两处互操作实证入 ADR）;高分评估量化入档（重建实例否决/瓦片 zoom
  折中 opt-in）;`export_batch_queue.py`（串行恒 1/重试/断点续传/fail-soft）;
  `accessibility_manifest.py`（alt/图层标签/色盲实测声明/来源/投影,
  complete-missing 断言）;格式矩阵缺口登记。
- **W7**:`local_visual_criteria.py`（五维可计算视觉事实;无画面仍
  not_evaluated）;`selfheal_policy.py`（落 W1 反馈信号存储;成功率表 +
  归因表 30 样本;先验只重排）;动作 10→14;`selfheal_blocking_enabled`
  默认关（回滚 = 清环境变量,测试锁定）。

### 3. 数值对照（M3 → M4）

| 指标 | M3 | M4(W6+W7) |
|---|---|---|
| cartography 覆盖率 | 57.43% | **57.7% 区间**（floor 50） |
| lane 测试 | 907 | **~929** |
| PDF 图体 | 栅格 addImage | **矢量优先 + 栅格兜底（模式回执）** |
| 批量导出 | 无 | 串行队列 + 断点续传 |
| 可访问性 | 散落可选 | **必产清单 + 完整性断言** |
| 视觉证据 | 仅 VLM（未配→恒空转） | +**本地五维确定性判据** |
| 自愈学习 | record-only | 策略库 + 归因表（30 样本）+ 阻断开关 |
| frontend 依赖 | — | +svg2pdf.js（MIT） |

### 4. 门禁原文（§6;SKIP_BROWSER=1）

（终版原文见终版 PR 或本 PR 评论;门禁独占运行纪律见 W6 台账
「运维教训」——pytest 并发共用 .coverage 数据文件。）

### 5. findings / 缺口（如实）

- 已入档缺口:vectorSvg 生产端接线、GeoTIFF 菜单、打印档核验、批量队列
  路由、可访问性随件携带、渲染器像素级 parity（骨架在）——全部进 W8/W9
  台账与格式矩阵文档,不虚报。
- VLM provider 未接线（W7 交付本地 fallback 与开关;provider 配置与
  ≥5 图型端到端归 W8 矩阵批次,无 key 环境据实登记）。

### 6. 风险与回滚

- 矢量优先为叠加（失败回退栅格 + 回执）;阻断开关默认关;回滚 tag
  `ac-v11-w6` / `ac-v11-w7`。

### 7. 兼容性

- PDF 新增可选参数（vectorSvg/onBodyMode）默认行为不变;SVG/PNG 路径未动;
  自愈默认 record-only（V10 语义）;仅增依赖与只增字段。
