# G44 BASELINE — 业务字段契约与结构化要素录入

## 身份

- TASK_ID: G44-FIELD-CONTRACTS；slug `g44-field-contracts`
- Branch: `zcode/g44-field-contracts-20261115T124850Z`
- Base: `655f769ca17f17c5797da13b4c299b8fa600e370`（origin/master @ 2026-11-15T12:46Z fetch）
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/g44-field-contracts-20261115T124850Z`
- 主工作区（816705218，落后）与用户未提交内容不动。

## 启动复核（Phase B 实测）

- remote = origin https://github.com/WindWang2/webgis-ai-agent.git；defaultBranchRef=master。✓
- Open PR：无（G40 #2124 已于 base 中合并）。
- #2117–#2122、#2124 三类评论通道逐一实读：review submissions 全 0；inline comments 全 0；
  conversation 仅 #2120 有一条独立复审第 2 轮记录（P1 全修复），其余空。与任务快照一致。
- Open issues：仅 #2123（旧 master CI 基线报告）—— 审计质量门，非本任务目标。
- 基线代码漂移：`git diff 34f4e3670..origin/master -- app/services/feature_edit/ app/services/feature_proposals/ app/tools/feature_edit_tools.py frontend/components/map/feature-layer-editor.tsx` 为空 —— 任务书引用的 file:line 在最新 master 仍有效。
- 迁移头：单一 head `g40_cartographic_symbol_assets`（down=`bcf4e5b51f43`）。G44 迁移
  `g44_field_contracts` 挂其下；ID 已在 `git-common-dir/webgis-id-reservation.lock` 登记。
- Pi pin：vendor/pi gitlink v1.5.2 / b4afddf（G22 已对齐，不作为本任务成果）。

## 权威既有事实（file:line 见 DEDUP_MATRIX）

- 唯一写路径：`feature_edit/executor.py::commit_plan`（锁内 CAS→重求值→overwrite→journal）；
  undo/redo 同文件 `apply_undo/apply_redo`；preview 纯函数 `plan.py::build_plan`（`derive_changes`
  为 preview/commit 共用中枢）。全部入口（REST/Pi 工具/G32 bridge/前端 submitEditOps）都汇入
  这两个函数 —— 合同校验挂 `build_plan` + `commit_plan` + undo/redo 即可覆盖全部写入口。
- 观测词表 ≠ 业务合同：`attr_ops.py` 24 值 distinct 枚举 + 20k 扫描上限是观测校验；G44 新增
  owner 声明域（DB 持久、版本化、激活制），声明字段覆盖观测枚举判断，保留标量安全。
- G32 bridge（`feature_proposals/bridge.py::merge_proposal`）→ build_plan+commit_plan；激活后
  合并需复核当前合同（本任务接线）。
- durable job：`app/services/jobs/lifecycle.py::JobKind` 增 `field_contract_assess`；runner 用
  G38/G39 的 `data_operations/runner.py::submit_async_job` 模式。
- 工具注册：`app/tools/__init__.py::_TOOL_MODULES` + `@tool(...)`；session_id 注入即授权。
- 路由 ACL：`require_owned_session`（owner 动作）+ 服务端裁决 actor/origin。
- 前端：侧栏面板 dynamic mount 于 `frontend/app/page.tsx`；i18n zh-CN/en-US 双目录 + key 完整性
  测试；`submitEditOps` 一次手势=一次语义提交。
- 测试：Pi 旅程模板 `tests/integration/test_pi_g41_bivariate_reliability_journey.py`（真实子进程，
  PI_JOURNEY_E2E=1 门）；浏览器旅程 `frontend/e2e/j*.journey.ts`（mock/real 两模式）。
- 环境：per-worktree `.venv`（uv）+ per-worktree `frontend/node_modules`（pnpm）。本 worktree 初始
  均无 —— 首次构建需自建。

## 资源纪律

- 重阶段锁：`$(git-common-dir)/webgis-heavy-stage.lock`（flock）；-j2 默认 / -j4 上限；
  pytest 单进程；vitest 1–2 workers；playwright 1 worker。
- 端口/临时目录/DATA_DIR 独立；结束时只清理自己登记的进程。
