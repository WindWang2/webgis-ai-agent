# WP4 实现简报（前端实现 agent）— 制图规范与系列作者工作台

仓库：`/home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125`（worktree，
分支 `zcode/goal-carto-design-series-20261125`）。WP1/WP2 已提交（后端稳定）：
REST `/api/v1/design-specs`（CRUD/versions/publish/retire/preview）。
WP3 后端（另一 agent 正在做）将提供 `/api/v1/map-series`（契约见 §3 —— 按契约写
client + 面板，端到端联调在其合并后）。

## 你独占的文件

- `frontend/lib/api/design-specs.ts`（新）
- `frontend/lib/api/map-series.ts`（新）
- `frontend/lib/design-series/contracts.ts`（新；TS 类型面）
- `frontend/components/workbench/design-series-panel.tsx`（新）
- `frontend/components/workbench/design-series-panel.test.tsx`（新，vitest）
- `frontend/messages/zh-CN/designSeries.json` + `frontend/messages/en-US/designSeries.json`（新）
- `frontend/lib/i18n/messages.ts`（+1 行登记）
- `frontend/lib/api/transport.ts` **不动**（复用既有 apiFetch/fastGet）
- `frontend/components/sidebar/map-studio-tab.tsx`（⚠️ 开放 PR #2275 也在改它 ——
  只允许**最小 additive**：1 个 import + 1 处挂载，放在 print-layout-panel 挂载点之后，
  便于 rebase 时人工核对；不重排、不格式化其它行）
- `frontend/e2e/journeys/j25-design-series.journey.ts`（新 journey；照既有 journey 形态）
- `frontend/e2e/fixtures/api-stubs.ts`（+本域 stub，additive）

**禁触**：`frontend/components/hud/*`（#2274 领域）、`frontend/messages/{zh-CN,en-US}/map.json`、
`app/`（后端）、`tests/`（后端测试）。

## 后端契约（已定，直接照写 TS 类型）

### /api/v1/design-specs（已实现，tests/unit/design_specs/test_design_spec_api.py 是行为真相）

- `GET /design-specs` → `{success, items: [{id,name,description,is_builtin,status,
  current_version,structural_digest,retired,retirement_note,creator_id,updated_at}]}`
- `POST /design-specs` `{payload}` → `{success, spec}`；409=`data.code DESIGN_SPEC_NAME_CONFLICT`；
  400=`data.code DESIGN_SPEC_INVALID` + `data.violations[]`
- `GET /design-specs/{id}` → `{success, spec, payload, reference_validation:{ok,violations,warnings}}`
- `POST /design-specs/{id}/versions` `{payload, parent_version_id?}` → `{success, version}`
- `GET /design-specs/{id}/versions/{version|latest}` → `{success, version, effective_payload,
  inheritance_chain, reference_validation}`
- `POST /design-specs/{id}/publish` / `POST /design-specs/{id}/retire` `{note}`
- `POST /design-specs/{id}/versions/{v}/deprecate` `{note}`
- `GET /design-specs/{id}/preview?version=` → `{success, spec, version, effective_payload,
  inheritance_chain, reference_validation, render_targets:{supported,unsupported,font_findings},
  conflicts, resolved_digest}`
- 错误信封：HTTPException detail 被全局处理器放进 `data`（`{success:false,code,message,violations?}`）。

### /api/v1/map-series（WP3 契约 —— 另一 agent 按 wp3_contract_brief.md 实现）

- `GET /map-series` 列表（形状照 design-specs 列表 brief）
- `POST /map-series` `{payload}`（map_series.v1：name/design_spec_id/members[
  {member_id,title,session_id,role,dimension_tags,locked_fields,enabled}]/comparability/
  dimension/output）
- `GET /map-series/{id}`；`POST /map-series/{id}/versions`
- `POST /map-series/{id}/members`（增/改/删单成员）
- `GET /map-series/{id}/consistency` → `{findings:[{code,severity,member_id,component,message,fix_hint?}], ok}`
- `POST /map-series/{id}/propagation/preview` → 受影响成员/锁定字段/不可解析成员
- `POST /map-series/{id}/propagation/apply` → `receipts:[{member_id,status,revision,error?}]`
- `POST /map-series/{id}/freeze-plan` → 冻结计划 JSON

## 面板设计（design-series-panel.tsx）

单面板多 tab（专业简洁，避免每图重复大面板；窄屏单列折叠、宽屏双列）：
1. **规范 tab**：规范列表（名称/状态/版本/digest 短码）→ 选中查看 payload 分节
   （色彩/分类/组件/角色/覆盖策略）+ reference_validation 违规列表 + preview
   （supported/unsupported 渲染目标、conflicts）；新建（表单生成 payload 骨架）/版本
   /发布/退役操作。
2. **系列 tab**：系列列表 → 成员图卡网格（title/role/dimension 标签/enabled；
   点击选中高亮 + 「在地图打开」仅展示 session_id 引用信息，不做跨会话加载）；
   成员增删改表单；一致性检查按钮 → findings 列表（severity 徽标，定位 member）。
3. **传播 tab**：spec 版本选择 → preview（受影响成员/锁定/不可解析三列）→
   apply（二次确认 + 逐成员 receipt 列表 committed/superseded/failed 徽标 + 错误信息）。
   传播前强制先跑 consistency（error 级 findings 存在时 apply 按钮禁用 + 提示）。

状态管理：zustand 已有全局 store——**本面板用本地 useState/useReducer 即可**（面板
内部状态不进全局 store，避免 cross-tab stale 扩散）；API 读走 fastGet + 操作后显式
invalidateCache。双 tab stale：每次 tab 激活重新拉列表；mutation 后 invalidate。
键盘：tab 可焦点、Enter 选中、Esc 关闭抽屉；空系列/失效版本显示空态（不假数据）。

## i18n

- namespace `designSeries`，zh-CN 与 en-US 全 key 对齐（禁硬编码文案）；
  hook 用 `frontend/lib/i18n/useT.ts`；`frontend/lib/i18n/messages.ts` 登记一次。

## 测试（全绿才算完）

1. `design-series-panel.test.tsx`（vitest）：列表渲染/空态/违规披露/锁定徽标/
   传播 receipt 渲染/apply 禁用逻辑（mock apiFetch —— 看既有 panel 测试怎么 mock，
   照抄其模式）；窄屏 class 断言。
2. `pnpm --dir frontend test` 全绿（至少不破坏既有用例）；`pnpm --dir frontend
   typecheck` 零错误；`pnpm --dir frontend lint`（--max-warnings 0）通过。
3. journey：mock 模式 stub 本域 API；断言面板打开→规范列表→选规范→系列 tab→
   一致性检查→传播 preview 出现（≥3 个断言）。journey 命名 j25 避让既有编号
   （先 ls frontend/e2e/journeys/ 确认）。

## 命令

`cd /home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125/frontend &&
pnpm test`（vitest 已 symlink node_modules，勿 install）；
`pnpm typecheck`；`pnpm lint`。跑不了再报告，不要改 lockfile/依赖。

## 完成自检

1. 三个命令全绿（test/typecheck/lint）。
2. `git status` 改动面 = 你独占清单 + map-studio-tab 2 行 + messages.ts 1 行。
3. 原子 commit：`feat(cartography): design spec & series authoring workbench UI (WP4)`。
4. 更新 planning/goal-carto-design-series-20261125/progress.md（追加一行）。
5. 报告 ≤30 行：文件清单、测试数字、map-studio-tab 挂载点行号、偏离决策。
