# G27 LOOP LEDGER

> 每里程碑更新：下一步可执行动作、最新 SHA、有效契约、必须保留的用户约束、未解阻断。

## L0 — 2026-11-11 14:40Z · 侦察与独立工作区

- BASE `8a9a79f76875eb95d31bd448baad971edb2e61d3`；分支 `zcode/g27-scale-aware-portrayal-20261111T144000Z`；worktree `webgis-wt-g27-scale-aware-20261111T144000Z`（干净）。
- 完成：安全 fetch；远端全景；只读架构调查员真实调用图；RECON/DUPLICATION_MATRIX/PLAN/INTERFACES 落地；隔离 venv 与宿主重任务锁确认。
- 必须保留约束：起始工作区 `M vendor/pi` + `.claude/skills/*` + `_tmp_47_*` 只读；不改 master / 不合并 / 不删他人分支 / 不等线上 CI 但不降门禁；不升级 Pi vendor（G22）；避开 #2084–2086；最多 3 subagent、不派生下级。

## L1 — 2026-11-11 · 实现与验证

- 4 个阶段 commit：`4ddf1e4f5` P0/P1 → `33c91255c` P2/P5 → `c6b04ecea` P3/P4 → `46b93d7d2` 文档。
- 实证：后端 `68 passed`、前端等价 `23 checks passed`、tsc `G27_RELATED 0`、P6 探针、py_compile；三层验证中第 2/3 层因 4 GiB VA 硬限 blocked。
- 发现并修复真实漂移：`build_label_zoom_bands` [3,19) 投影死带（钳到 [0,24)）。
- 状态：P0–P7 `implemented/verified`（A 项按 ACCEPTANCE 标 verified/partial/blocked）。

## L2 — 独立 review → 修复 → 主线整合

- **R1**（未参与实现的 `g27-reviewer`）：自行复跑 `68 passed` / `23 checks` / py_compile / diff，**阻断未归零**，1 BLOCKER + 4 P1 + 4 P2 + 2 P3；实跑坐实 converter 与 service 对同一 `priority_field` policy 判定相反。详见 `REVIEW.md`。
- **主线整合**：`origin/master` 前进到 `b8f2a6ecc`（#2084/#2085/#2086 已合并，开放 PR 清空）；`git merge --no-edit origin/master` → `5cbefc82b`，ort 无冲突；`toolSlice.ts` TS1109 基线错误消失。
- **R1 修复**：F1 REVIEW.md + fix commit + ledger；F2 A19/A20 降级；F3 converter/service 字段证据统一；F4 A15 可证伪 + `visibility_semantics` 真实裁决；F5 移除 thresholds 假接线 + 组件接 service 回执 + 表述降级；F6 真实规模重测；F7/F8/F10/F12 权威统一与判据收紧；F9 user CAS 基线；F11 常量与异常零部分写入。
- **R2 复验**：后端 `71 passed`、前端等价 `23 checks passed`、tsc `TOTAL 1 / G27_RELATED 0`（`render-debouncer` 本地基线）、真实规模探针 `feature_count_unchanged=true` / `mutation_module_loaded=false`。
- **最新 SHA**：见 `git log -1`；下一步 = 独立 reviewer 复审 →（若通过）push 分支 + 创建 **Draft PR**。
- **未解阻断（外部）**：真实 Pi 子进程 RPC 全链、本地真实浏览器 E2E、真实 `apply_gis_mutation` CAS/undo 引擎链（`pydantic_settings` 等 Web 依赖缺失）、真实浏览器帧时间 —— 全部由 `ulimit -Hv=4 GiB` / 最小 venv 造成，已在 ACCEPTANCE/PR_BODY 标 blocked/partial，**未伪造通过**。
- **用户约束仍全部保留**；未合并、未改 master、未删他人 worktree、未降门禁。
