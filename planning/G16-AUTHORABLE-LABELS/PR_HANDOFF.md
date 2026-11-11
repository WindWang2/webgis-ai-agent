# PR_HANDOFF — G16-AUTHORABLE-LABELS

- **PR**: https://github.com/WindWang2/webgis-ai-agent/pull/2077
- **Branch**: `zcode/goal-g16-authorable-labels-20261110T163415Z`（已推送）
- **Worktree**: `/home/kevin/projects/webgis/webgis-worktrees/goal-g16-authorable-labels-20261110T163415Z`（保留，未合并前不删）
- **Base**: `master` @ `816705218a5aa6202bf15d745cb31cddb3437f44`（PR 前复核未移动）
- **Head**: `6ccd75a3e`（11 commits：WP02→WP05/07→WP06/08→WP03→WP10→WP09a→WP09b→WP04→WP11/12→R1 fixes→R2 follow-up）

## 交付面

- 要素级标注覆写（hide/pin/offset/text/callout/priority）持久化于 `layer.label.authoring`
  （schema v2.1 additive；≤256/层；em canonical 偏移单位）
- 线/面墨迹避障 `layout.labels.obstaclePolicy: geometry_v1`（实际 stroke 宽、
  逐段 AABB、自身墨迹豁免、公平预算交错 + 截断披露）+ `forbiddenZones` 显式禁入面
- 统一写路径 `PatchLayerLabelAuthoringIntent`（原子/CAS/撤销栈/user-wins；UI 与 Pi 同源）
- live MapLibre 投影（auto/pinned/callout 子层；to-string 数值身份匹配）+ 双端确定性求解孪生
- Pi 工具 `webgis_label_inspect/author`（有来源 why-not-shown；placement 族 not_evaluated）
- 标注编辑 UI（edit_labels 工具：拖动单笔提交/键盘微调/Esc 零写入/reset 四作用域）
- ADR-0289、TEST_MATRIX、DEDUP_MATRIX、LOOP_LEDGER、REVIEW 全套

## 验证结论

- 后端 3467 passed（唯一失败 `test_catalog_docs_fresh` = master 干净基线复现，未触碰）
- 前端 5099 passed（唯一失败 changed-lanes j13 = 干净树复现，G14 #2075 同报）
- tsc/eslint 全绿；legacy 导出逐字节不变（实测对拍）；parity corpus golden 含新意图
- 性能：dense 1800 点 + 40 长线 + 21 覆写：legacy 96ms / geometry_v1 88ms

## 评审链

R1（独立 reviewer）：2×P1 + 10×P2 + 9×P3 → 全部 P1/P2 + 7×P3 修复（bf47d432c）
→ R2（同一 reviewer）：**Ready for PR、0 blocking** → R2 遗留 1×P2 + 4×P3 全部
follow-up 修复（6ccd75a3e）。详见 REVIEW.md。

## 与在途 PR 的冲突面（合并时注意）

- **#2076 (G15)**：schema 版本行/carto_ir 语料钉文本冲突（语义可并：2.0=G15、
  2.1=本线，双方恒等 upgrader；`MapSpecFrame` vs `MapSpecLayerLabel/MapLabelConfig`
  各改各的模型）。任一先合并后另一 rebase 一次即可。
- **#2032 (UI)**：`map-toolbar-hud.tsx`（按钮组）、`map-panel.tsx`（挂载 1 行 +
  门控分支）、`agent-run-panel.tsx`（+1 键）。
- 前端新增 devDependency `@maplibre/maplibre-gl-style-spec@26.4.4`（仅测试）。

## 未测限制 / 后续建议

- PDF 未做像素级打开验证（svg2pdf 消费同一 SVG labels 组；SVG 内容断言 + 既有
  PDF 回归覆盖）；浏览器端编辑器旅程为 jsdom 组件级（未跑 Playwright）
- live callout 引线仅 Point 锚；拖拽 px→em 用 spec label.size（em 语义成比例）；
  覆写 origin 保留首作者 —— 均已在 PR 披露
- 建议后续：TS 障碍孪生 golden 差分语料（当前与 Python 共用 solver fixtures，
  障碍抽取侧为同构实现非共享语料）
