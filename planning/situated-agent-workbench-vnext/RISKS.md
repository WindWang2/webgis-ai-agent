# RISKS — situated-agent-workbench-vnext

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | 与 open PR #1965 在 `map-panel.tsx`/`use-cartographic-observation.ts` 冲突（其改动 observation 链与 viewCommands） | 高 | 默认零写入这两个文件；新事件走 `frontend/lib/interaction/bus.ts` 与 `frontend/lib/selection/` 既有通道；确需 map-panel 接入时 fetch 复查 → 最小 diff → 独立 commit。 |
| 2 | 与 open PR #1972 在 `gis_harness/tools.py` 冲突 | 高 | 零写入；scoped analysis 若需新工具面，评估放独立文件（先例 `map_operations_tools.py`）+ 单独 commit + 契约测试锁定；优先复用既有工具参数面。 |
| 3 | IR 词表扩张破坏 manifest 双闸（backend pytest ≡ contracts.py ≡ frontend contract-manifest.ts） | 高 | 新 kind 走 additive；同步再生 `contract_manifest.json` + 前端镜像 + 双侧测试；不改既有 kind 语义。 |
| 4 | 造出第二状态真相（与 MapSpec/session/map_state 打架） | 高 | 红线：新投影全部只读派生自权威 store；ContextSnapshot 不落新表，复用 situation 快照机制；测试断言「投影失败不影响权威状态」。 |
| 5 | 词表纪律违背 ADR-0227（无真实前端钩子的 kind 不得收录） | 中 | 每个 IR 新 kind 必须同时落前端真实接线 + 测试证明；无钩子的需求如实记「未接线」不收词。 |
| 6 | 指代解析越权（跨会话 ref / 无权数据被解析进 scope） | 高 | 服务端验证 ref 所有权（session store resolve）+ revision 一致性；无法唯一指代 → 最小澄清（复用 #1874 clarification 候选模式），绝不随机挑选。 |
| 7 | 图表↔地图 linked brushing 回环 | 中 | 复用 chart-map-link 既有防回环（源标记 source=chart/map）+ 新增事件只记 settle 终态；测试含双向刷选断言无回环。 |
| 8 | 恢复/重连把旧 selection/旧 ACK 写进新会话或新 revision | 高 | 复用 client_generation 单调 + situation revision 字典序拒退；session 切换清 outbox/undo 栈既有行为回归测试覆盖。 |
| 9 | e2e journey 在本地环境不可跑（浏览器/后端依赖） | 中 | 分层标记：纯 store/IR 测试（必跑）、mock e2e（@smoke，需 playwright）、real e2e（E2E_MODE=real，环境不支持时如实 NOT_RUN 并注明阻碍）。 |
| 10 | 审计 issue #1856/#1857/#1859（gis_memory/gis_context 既有 Medium）被误伤 | 低 | 只读这些模块；不修复不扩散；风险披露在 PR body。 |
| 11 | 本地并行构建资源争抢（同机多 worktree 任务） | 低 | -j2 纪律；重型测试串行；先 `ps` 检查再跑。 |
| 12 | ADR 编号撞号（并发分支） | 低 | 用 `scripts/allocate_adr.py` 分配 + watermark 推进 + 提交前 fetch 复查。 |

## 开放审计 issue 关联（只读风险输入）

- #1856（gis_memory S4j，5 Medium）：热索引不失效/晋升指纹每轮变——本方向只读消费 memory，不触碰其 store；E1 的选择/事实过期**不得**依赖 gis_memory。
- #1859（gis_context S4m）：AOI 视口漂移伪 stale——本方向 AOI 语义走 IR aoi_* 事件（用户显式绘制），不复制其 viewport-bound AOI 派生。
- S3-001/S4-001/S4-002 等安全类：路由权限模式照抄 `require_owned_session` 既有先例，不发明新鉴权。
