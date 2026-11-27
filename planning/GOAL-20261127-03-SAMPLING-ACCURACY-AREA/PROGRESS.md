# GOAL-20261127-03 PROGRESS — 执行台账

## 提交序列（分支 zcode/goal-probability-sampling-map-accuracy-20261127）

| commit | 内容 |
|---|---|
| b4fed9108 | 启动审计台账（baseline+dedup） |
| 615cebc8f | 数学内核（design/frame/selection/estimation + 39 单测） |
| d2c790524 | WP5 后端（8 Pi 工具 + 12 REST 端点 + 能力/契约/域关键词接线） |
| e224babf3 | 必过旅程 1-4 + 三个真缺陷修复（指纹字段名/取消吞包/行列同序） |
| aa8a3d564 | 工具 dispatch 真跑 + REST 合同测试 |
| 081ffa1fb | 门禁收口（mypy/ruff/docs 重生成 + perf 基线） |

## 事故记录（诚实披露）

- 2026-11-27：主 Agent 误触发 `git stash pop`，把外部旧 stash
  （codex/all-issues-deep-review，另一任务遗留）应用进本 worktree，
  产生 ~380 文件冲突。恢复：受影响 tracked 文件经 diff 验证仅含本任务
  改动后备份；`git reset --hard HEAD` + 精确清除 58 个 stash 遗留
  untracked（.claude/skills 符号链接）；备份校验（无冲突标记）后还原；
  全量复验（39 内核测试 + 工具注册 + 路由导入）。stash 对象本体未删除
  （仍为 stash@{0}，归属其他任务工作树）。

## 修复的真缺陷（审查/旅程驱动）

1. PA 方差漏乘 W_s²（oracle 手算抓出）
2. UA SE 误传方差（未开方）
3. 帧指纹字段名错误（content_fingerprint→fingerprint）导致漂移检查从未生效
4. 取消异常被包装成 SURVEY_FAILED（破坏取消终态语义）
5. 估计矩阵列序=字典插入序 vs 行序=排序层序（消费者陷阱）→ 统一为层序
6. dispatch kwargs 注入语义（工具签名逐字段化）
7. list_surveys 缺 survey_id 字段
8. numpy 布尔 `or`（design prior 校验）
9. 稀有类 CI 测试输入全零列方差为 0（测试构造修正）
10. 类值超 dtype 范围静默回绕防护；空 CRS typed 拒绝

## 门禁状态（vs master 基线 f34cb69dd）

| 门禁 | master | 本分支 |
|---|---|---|
| ruff（本任务文件） | n/a | 0 错（全仓 26 处为基线既有） |
| mypy（本任务模块） | n/a | 0 错 |
| 测试（本任务） | n/a | 49/49 绿 |
| tests/test_tool_meta_contract | 2 failed | 同 2 failed（基线既有） |
| execution-catalog docs drift | **红** | **绿**（已重生成） |
| manifest shape fatal==0 | 绿（旧文件掩盖） | 红（诚实暴露基线 28 dangling fatal） |
| debt_ratchet god_modules | 4 超 | 同 4 超（基线既有） |
| import boundaries | OK | OK |
| 工具对账 --fail-on warning | exit=1 | 同基线（survey 工具自身全 pass） |
| descriptor coverage | 80% FAIL | 同 80%（capabilities 补齐后缺口与 master 持平） |

## 剩余工作

- [ ] 前端面板（子代理进行中）
- [ ] R1 报告 + 修复（子代理进行中）
- [ ] R2 复审 + 关键 journey 复跑
- [ ] 交付（rebase 检查、PR）

## R1 修复（commit ddbbb7f26，2026-11-28）

- 数学结论（R1 独立 4000 次 MC）：OA/UA/PA/面积公式全部正确
  （SE/SD 0.98–1.01、覆盖 0.94–0.95）——零数学缺陷，修复集中在工程面。
- P0-1 前端组件入库；P1-1 路径注入阻断；P2-1~P2-6 全修；
  P3-1/2/4/6/9/10 修，P3-3/5/7/8/11/12 记录在案（披露口径/性质说明类）。
- 修复回归：tests/survey_sampling/test_r1_regressions.py 9 项；
  调查域测试 61/61；前端 typecheck/eslint/vitest 亲验通过（5/5、228/228）。
- P2-2 深化：append_and_bump 单一临界区（CAS→版本→追加→revision），
  消除 CAS 失败孤儿记录（R2 重点复核项）。

## R2

- 复审代理进行中（同一独立审查员，有 R1 上下文）。
