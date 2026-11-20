# LOOP_LEDGER — G16-AUTHORABLE-LABELS

| # | 增量 | commit | 验证 | 状态 |
|---|---|---|---|---|
| 1 | WP02 schema v2.1 + label_authoring.py 纯模型 + 61 例 | 349ec621d | model/schema suites 全绿（版本钉随 v2.1 前移） | ✅ |
| 2 | WP05/07 求解器融合（双孪生 + 4 golden fixtures + 行为回归） | d4d82fd59 | py 30 + ts 20；legacy 逐字节 | ✅ |
| 3 | WP06/08 带身份障碍抽取 + 导出接线 + 诊断词表 + 20 例 | 4742c0d63 | cartography 全绿（catalog_docs 基线外） | ✅ |
| 4 | WP03 PatchLayerLabelAuthoringIntent（原子/CAS/撤销/溯源盖章） | ce00c97e8 | lifecycle 10 例 + registry 一致性 | ✅ |
| 5 | WP10 webgis_label_inspect/author（有来源 why-not-shown） | eb798c66f | 工具旅程 6 例（真实 ToolRegistry） | ✅ |
| 6 | WP09a TS 障碍孪生 + mapspec-to-svg.ts 接线 + 9 例 | 8d57c17a8 | tsc 0 error；compiler 59 全绿 | ✅ |
| 7 | WP09b/WP08 live runtime 投影（auto/pinned/callout）+ 双端 callout | cca0ba33d | runtime 57 全绿（新增 15） | ✅ |
| 8 | WP04 标注编辑 UI（toolSlice/toolbar/label-editor/i18n/undo） | 4be190ac0 | 组件 6 例 + tsc/eslint 全绿 | ✅ |
| 9 | WP11/12 orphan 披露键修复 + parity corpus + ADR-0289 + 性能 | 4beefbb7d | parity golden 再生成；dense 88ms | ✅ |

## 关键决定记录

- canonical offset 单位 = em（live 原生；导出 ×font_px 同比 DPI；否决绝对像素/裸 pt——live px 与导出 pt 存在既有 96/72 字号口径差，em 绕开且两端一致）。
- 覆写住 layer.label.authoring（层嵌套=身份构造唯一）；否决 spec 级总表（跨层混写/双读点）。
- 原子拒绝（任一 op 失败整笔 400）而非部分应用 —— CAS 语义简单、修正提示可执行。
- zone 网格分解放大保铺满（否决抽稀——留洞）；组件 placement 页面 px ×dpi_scale。
- 2.0 号位 G15 占用（恒等上船，双向收敛）；ADR-0289（G15 分支占 0288）。

## 基线既有失败（证据）

- catalog_docs theme-palette 漂移：干净 BASE_SHA stash 复现（主仓亦然）。
- changed-lanes j13：git stash 后干净树复现（G14 #2075 PR 同报）。

## 下一步

独立 review → 修复 → 复审 → push + PR。
