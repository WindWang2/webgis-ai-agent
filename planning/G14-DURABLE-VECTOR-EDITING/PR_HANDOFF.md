# PR_HANDOFF — G14-DURABLE-VECTOR-EDITING

- Branch: `zcode/goal-g14-durable-vector-editing-20261110T133233Z` (pushed)
- Base/head: `816705218a5aa6202bf15d745cb31cddb3437f44` → 见 PR（master 未移动）
- Worktree: `webgis-worktrees/goal-g14-durable-vector-editing-20261110T133233Z`（保留，勿删）
- Commits: 42d830dbb（结构核心）→ be83b589a（provisioning/拓扑/事务）→ 425161301（旗舰旅程/Pi 工具）→ 1fb16f376（前端）→ a86ecf724（ledger）→ c0dc82393（R3 评审修复）→ 4c1801382（R4 评审修复）

## 主要功能
create/delete/replace_geometry/split/merge 结构性编辑事务（可逆 delta + 原位恢复 + 决定性新身份 + 血缘）、空 typed 图层/草图升格/只读派生副本 provisioning、声明制拓扑闸、Pi 工具（provision/lineage + 结构性 preview/commit）、前端真实图层手势编辑 + 草图保存为数据集 + `_wfid` 身份链对齐。

## 测试与评审结论
- 后端 123 例全绿（feature_edit 全目录）+ interaction_ir 邻域 126 绿；穷举删除-undo 序属性扫描（N=6 × 63 子集 × 正/乱序）0 失败。
- 前端 vitest 5042 通过（2 个既有失败文件在纯净 master 复现隔离）；eslint --max-warnings 0 与 tsc 全绿。
- 独立评审 3 轮：R1（1×P1+8×P2+P3）→ c0dc82393 修复 → R2 复审（发现修复引入的多删除 undo 回归，blocking）→ 4c1801382 修复 + 穷举验证 → R3 终验：**NON-BLOCKING，无未解决阻塞，无新回归**（reviewer 独立复扫 N≤9 全子集×乱序 0 失败，123 例复现全绿，原话 "Ship it."）。

## 既有失败隔离（非本分支）
- `tests/unit/api_contract/test_field_contract.py`：/health +agent_runtime_*、/export/vector-pdf +constraint_layout 漂移（已合并 #2036/#2015 未刷新快照；纯净 origin/master worktree 复现）。
- 前端 `test/pdf-dependency-security.test.ts`（15，jsdom DOMPurify 行为）、`tests/e2e-lanes/changed-lanes.test.ts`（j13 journey 漏登记）—— 同上隔离。

## 操作说明
- 草图升格按钮在草图保存条（保存为数据集）；真实图层编辑入口在工具栏（选择带 ref 的图层 → 开始编辑；分割工具 ✂；合并按钮在底部编辑面板按当前 selection≥2）。
- 手动编辑 = owner 通道（preview→approve 由手势一次完成）；Pi 结构性提案仍走提案卡确认。
- 环境变量/迁移：无新必需配置；envelope 兄弟键（geometry_type/topology/read_only/derived_from）为可选加法。

## 剩余外部阻塞
无。未运行项（如实）：线上 CI（按仓库纪律不等待）、Playwright 新 journey（#2032 合并后补）、MVT 层地图手势拾取（文档化限制）。
