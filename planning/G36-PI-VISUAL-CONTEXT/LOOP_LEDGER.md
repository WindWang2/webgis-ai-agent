# G36-PI-VISUAL-CONTEXT LOOP_LEDGER

## 执行环境
- worktree：`/home/kevin/projects/webgis/webgis-worktrees/g36-pi-visual-context-20261114T151654Z`
- branch：`zcode/g36-pi-visual-context-20261114T151654Z`；base=1276692737（=快照，无漂移）
- venv：`.venv-g36`（uv，python3.13，16G uv 缓存命中）；frontend node_modules
  自主工作区硬链接拷贝（npm 慢速网络不可用；5 个 minor 版本差异由 CI 干净安装覆盖）
- vendor/pi @ b4afdddf 已构建（bundle+dist）；pi_doctor 6 域 PASS
- 资源纪律：重阶段均经 `.bare/webgis-heavy-stage.lock` flock；pytest 单进程；
  vitest 默认并发（单文件批）；与兄弟任务（G38 等）共享锁排队正常

## 轮次记录

### R1（研究，完成）
- base/PR/review 复核：open PR=0；#2094–#2116 抽查 3 PR reviews/comments=0（与快照一致）
- 上游契约直读 pin SHA（rpc-types/message-types/models 文档）
- Explore agent 报告六区接缝（chat UI/store/快照/路由/G29/store+持久化）
- 产出 BASELINE/DEDUP_MATRIX/PLAN

### R2（实现 WP-A..F，完成）
- 新模块 7 文件 + 路由 + 工具 + 迁移 + ADR-0291
- 接线：chat_schema/bridge(双路径)/rpc_client(投影+锚点)/pi_result_abi/
  dispatch/result_images/chat 路由(持久化+恢复)/pi_interactions(V31)
- 前端：API client/store/采集 registry/composer UI/chips/框选模态/恢复/i18n×2
- 关键修复：decoder 头部尺寸先行；crop 越界改拒绝；VisionGate 参数名；
  pi_interactions 缩进事故（diff 复核后修复）；store 元组解包；thumb unwrap

### R3（验证，完成）
- L1 28✓；L2 9✓；L3 真实 Pi 旅程 1✓（20 断言）；回归 91+77✓
- 既有失败隔离：test_turn_settlement_seam 5 例 —— 干净 base 复现（非本任务）
- 前端：tsc main clean；vitest 30+40+8✓；i18n 1 既有失败（未触碰文件裸 CJK）
- L4 浏览器未运行（无 Playwright 栈）——如实登记

### R4（review → fix → 复审 → PR，进行中）
- 独立 reviewer subagent（未参与实现）审全量 diff
- 待：修复 P0/P1 → 复审 → commit/push/PR

## 决策追加
- D5: 旅程 driver 用 USE_REDIS=0（进程内锁/内存会话态）——单进程语义等价，
  旅程自包含（不依赖本机 Redis 缺席）；生产 Redis 路径未变。
- D6: 前端 node_modules 硬链接共享（npm registry 网络受限）；类型/单测结论
  对 G36 代码有效，CI 干净安装为准。
- D7: settlement_seam 5 例为既有失败（base 复现证据）——不在本任务范围，
  PR 正文披露。

## 未完成/风险
- L4 浏览器旅程未跑（阻塞：本机无 Playwright/chromium 栈；不伪造覆盖声明）
- 生产 Docker 隔离包检查未执行（V32 部分）
- 真实视觉服务实测未做（无凭据；stub 证明协议链）
