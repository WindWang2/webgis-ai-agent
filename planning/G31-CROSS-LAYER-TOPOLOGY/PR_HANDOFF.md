# G31-CROSS-LAYER-TOPOLOGY — PR_HANDOFF

## 交付物

- **PR**：#2099（`gh pr create` 于 2026-11-13；见下方 URL）
- **branch**：`zcode/g31-cross-layer-topology-20261112T144915Z`
- **worktree**：`/home/kevin/projects/webgis/webgis-worktrees/g31-cross-layer-topology-20261112T144915Z`
- **base**：`ca7b3ec27`（origin/master @ 创建时；push 前 re-fetch 确认无漂移）
- **head**：`d412e84b9`（+docs 收尾提交）
- **提交序列**：
  1. `fe2291941` feat(g31) 全量实现（43 文件）
  2. `8e01518e4` docs(g31) PR 正文 + 旅程账本
  3. `77485cb77` fix(g31) 评审一轮清偿（P1×2 + P2×6 + P3）
  4. `d412e84b9` fix(g31) 复审二轮清偿（NEW-1 + P2-4 真接线 + digest 口径）
  5. docs 收尾（REVIEW.md / TEST_EVIDENCE 终稿 / LOOP_LEDGER 出口 / 注释修正）

## 可操作功能（用户视角）

1. 项目侧栏 →「拓扑」页签：创建规则集（5 类规则、参数语义提示）→ 绑定源
   （coverage/aoi/exclusions…角色；会话 ref 或 bbox AOI）→ 启动检查 →
   运行列表（维度分离结论：适用/输入完整/计算完整/结论 —— all-blocked 显式
   警告不显绿）→ 问题队列（筛选/分页/全量计数披露/相机定位/详情 witness
   GeoJSON + locator + stale 披露）。
2. 例外：理由必填（用户决定面）→ accepted_exception 计数独立展示；源/规则
   变化后旧例外 stale，需重新验证；可撤销回 open。
3. 修复：问题详情给出候选（归属选择类明确要求用户选保留方，无一键修复）；
   snap/trim 类生成 G14 预览 plan（在地图编辑提案卡确认，CAS/undo 继承）。
4. 复验：运行行「复验」→ 同规则+最新源重跑 → resolved/persisted/new 计数
   与 receipt 证据（绑定输入/输出 revision + 规则 digest）。
5. Agent：`topology_ruleset_configure` / `topology_check_run` /
   `topology_check_status` / `topology_findings_list` /
   `topology_finding_detail` / `topology_repair_preview` 六工具（经
   tool_search 发现后裸名调用）；例外接受仅 REST/UI（模型不得代用户决定）。

## 验证证据索引

- `planning/G31-CROSS-LAYER-TOPOLOGY/TEST_EVIDENCE.md` —— Pi 旅程 verdict、
  oracle 数值、10k 资源曲线、兼容回归、pre-existing 归因
- `planning/G31-CROSS-LAYER-TOPOLOGY/REVIEW.md` —— 三轮独立评审全记录（终审
  APPROVE，含对抗探针与负例对照）
- `planning/G31-CROSS-LAYER-TOPOLOGY/TEST_MATRIX.md` / `INTERFACES.md` /
  `BASELINE.md` / `DEDUP_MATRIX.md` / `PLAN.md` / `LOOP_LEDGER.md`

## 复验命令（worktree 内）

```bash
# 后端域套件（49）
/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest \
  tests/topology_workbench/ -q --no-cov --deselect tests/topology_workbench/test_perf_curve_g31.py
# 资源曲线（3）
/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest \
  tests/topology_workbench/test_perf_curve_g31.py -q --no-cov
# Pi 旅程（真实子进程，opt-in）
PI_JOURNEY_E2E=1 /home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest \
  tests/integration/test_pi_g31_topology_journey.py -q --no-cov
# 前端（worktree/frontend）
./node_modules/.bin/vitest run test/project/topology-workbench-panel.test.tsx
```

## 已知边界 / 未测项（如实）

- 真实浏览器 E2E 未启动（jsdom 覆盖请求链与交互）；多副本 Redis/celery 未验证
  （eager 同形）；GEOS 不可中断单步的取消延迟以预算上限约束（120s/规则）。
- pre-existing 失败 2 例（干净 base 同败，非本任务引入，已在 TEST_EVIDENCE 归因）：
  `test_api_auth_required`（data_import 六路由，G17 遗留）、
  `test_field_compute_review_v1` openapi 短名（快照缺 PreviewResponse）。
- 并行 PR（G29/G30/G32/G33/G34，open 状态）与本分支无文件冲突面；共享文件
  （main.py / tools/__init__.py / project-assets-section.tsx / project.json）
  均为纯增量行，合并期由 owner 处理。
