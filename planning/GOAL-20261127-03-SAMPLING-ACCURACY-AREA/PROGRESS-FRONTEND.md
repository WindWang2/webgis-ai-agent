# PROGRESS-FRONTEND — WP5 前端面板（概率抽样调查与精度核查工作台）

## Touch 文件
- 新增 `frontend/components/sidebar/sampling-accuracy/sampling-accuracy-panel.tsx`（五步向导：建框→设计预览→创建→核查标注→估计；样点上图走 addLayer + legend_spec categorical 按 check_status 着色；行点击 setViewport；records 带 expected_revision CAS；估计 422/409 typed 信封呈现 code+correction_hint）+ `sampling-accuracy-panel.test.tsx`（5 测试：建框层表/预览分配表/记录→progress 更新/估计卡与徽章/typed 拒绝信封）。
- 本会话前已入库（HEAD 已含，等价内容）：hud-types/nav-rail/workbenchSlice/context-panel 四处 'survey' 注册、messages ×2 locale samplingAccuracy.json、messages.ts 登记、layout.json tab/title/desc/boundary 键、lib/api/sampling-accuracy.ts —— 复核一致。
- 分支既有门禁破损修复（非本面板引入）：`lib/mapspec/live-spec.test.ts`（source.tiles 越出 Layer['source'] 联合，加 `as unknown as Layer['source']` 断言）；`messages/en-US/chat.json` figureTopValues 补 `{n}` 占位符。

## 门禁结果（全部实跑通过）
- `pnpm run typecheck`（tsc --noEmit + tsconfig.test.json）→ exit 0。
- `pnpm exec eslint components/sidebar/sampling-accuracy lib/api/sampling-accuracy.ts --max-warnings 0` → 0 findings。
- `pnpm exec vitest run components/sidebar/sampling-accuracy` → 5/5 绿；`vitest run test/i18n` → 41/41 绿（含键完整性+代码引用键⊆catalog）；`vitest run components/layout lib/store` → 187/187 绿（nav-rail/context-panel 注册回归）。

## 遗留风险
- 样点级核查状态为会话内本地追踪（后端无逐点 records 读回端点），刷新后回退"未访问"；权威进度以 /progress 聚合表为准（面板已就地披露 localTrackingNote）。
- 面板组件目录仍为 untracked（REVIEW_R1 P0-1）：主 Agent 需 `git add frontend/components/sidebar/sampling-accuracy/` 后统一提交，否则 fresh checkout 构建失败。
- 盲标可被 sample_id 枚举逆向（REVIEW_R1 P2-5）为后端议题；前端仅按协议隐藏 stratum_id，参考类下拉取自 design.allocation keys。
