# G30 LOOP_LEDGER

## 执行轮记录

### R1（2026-11-12/13）— 主实现轮
- base `ca7b3ec273f2b43336852951e2292a416b2238e6` → branch `zcode/g30-executable-gis-skills-20261112T144732Z`
- 研究：subagent #1（Explore）PR/规划/源码 digest → DEDUP_MATRIX（20 项判定，E1-E9 全复核）
- 实现：WP01-WP12 全部（contract v2 + 3 YAML v1.1.0 + skill_jobs 域模块 + 3 Pi 工具 + 13 API 路由 + 迁移 + 前端工作台 + chat.skill_name 链 + plan seam 显式选择）
- 冒烟纵链：三旅程（report/point map/choropleth）真实跑通（发现并修复：双步同参 dedup 冲突→单步映射；layer source 规范化→显式 source id；point 契约缺质检能力→补声明）
- 既有缺陷（master）：scale_portrayal_tools memory_class 非法值致 G27 两工具注册死亡（`git show HEAD` 可证）→ 修复并按 G22 先例补种 2 capability（否则暴露 manifest fatal）
- 测试：新增 36 后端 + 4 前端；Pi 真子进程旅程 J-G30（发现并修复：匿名会话 owner-token 回传、per-turn 面两跳激活）
- 回归：gis_harness 全目录 3547 绿/8 失败（6 base 既有 + 2 我引入→修复转绿；纯净 base worktree 复现隔离）
- checkpoint commit `60dbd94d3`

### R2 — 独立评审轮
- subagent #2（general-purpose，未参与实现）：APPROVE-WITH-FIXES（P1×5 + P2×7，含两个安全项：跨 org 预设更新 IDOR、运行单读 IDOR）
- 修复 commit `fecd2b7a9`：P1 全清 + P2 全清（死码词表披露保留）
- 修复后复验：39 后端 + 31 前端 + tsc 0 + 旅程复跑通过 + ruff 清洁 + alembic 单头
- R2 复审：已提交，待回执（REVIEW.md 回填）

## 资源记录
- subagents：#1 研究（Explore）、#2 独立评审（复用复审）—— 配额 3 未超
- 重阶段（vendor build / Pi 旅程）均持 webgis-heavy-stage.lock
- pytest 单进程 / vitest 单文件运行 / tsc 单次

## 剩余未做（披露）
- Playwright 真浏览器 E2E：未运行（组件测试+API 链验证替代；TEST_MATRIX 披露）
- base 既有 8 失败（6+2 类）：未纳入本任务范围（干净 base 证据在案）
- Pi 旅程为 opt-in（PI_JOURNEY_E2E=1）——与 G26 旅程同门控约定
