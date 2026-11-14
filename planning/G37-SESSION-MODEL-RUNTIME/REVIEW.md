# G37 REVIEW（独立评审 → 修复 → 复审）

## R1：独立评审（reviewer：未参与实现的独立子代理；对象 = b0e2c6d3f vs base 1276692737 全量 diff）

**结论：APPROVE-WITH-NOTES**（1×P1 / 2×P2 / 10×P3）。评审复核确认的正确面（摘）：
- turn 边界应用位置正确（isolate 之后、prompt 之前；全仓仅两个 prompt 发送点且都被门控；预算拒绝先于 prompt 字节有单测钉死）；
- epoch/跟踪健全（respawn 复用同一 client → spawn_epoch 单调；new_session 唯一调用点必清跟踪；跨会话必重应用）；
- reader 世代钉扎不破坏合法死亡路径（is_alive/stop 各先行路径自行清算）；
- models.json 仅 spawn 原子写、共享内容为管理员目录、绝不按会话重写；
- 三个端点 owner 守卫齐全；工具只读 + session_id 注入（客户端传值被 registry 剥离）；
- usage 语义（reasoning⊂output、零价 unknown、responseModel 只从 assistant 回填）、CAS、SSE 首事件契约、迁移 head 单调性均确认。

### R1 发现与修复对照

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | **P1** | `_readline_bounded` 改 staticmethod 必选参破坏 2 个未修改既有测试（tests/unit/test_pi_rpc_client.py:163、tests/unit/test_review_core_low_boundaries.py:1240） | ✅ 已修：恢复实例方法 + `proc=None` 缺省回落 `self._process`（保留世代钉扎语义）；两文件 118 用例复跑通过 |
| 2 | P2 | 预算投影漏会话历史上下文（长会话超支随转录增长） | ✅ 已修：投影计入 `history_context` = 上一 turn 真实 input（账本实数，含当时全部历史的代理）；新单测钉死 |
| 3 | P2 | `_apply_session_model_for_turn` 的 `except Exception` 过宽 —— 非 DB bug 也会降级成 degraded_inherit（explicit 选择静默回退 + admission 被跳过） | ✅ 已修：收窄为 `SQLAlchemyError`（纯 DB 基础设施）；其余异常上抛走既有诚实失败路径 |
| 4 | P3 | PUT 响应死三元（两分支同值） | ✅ 已修：`active_turn_end` / `next_turn` |
| 5 | P3 | 首写 INSERT 的 `except Exception` 把连接故障伪装成 CAS 冲突 | ✅ 已修：收窄 `IntegrityError` |
| 6 | P3 | 同 turn 内重复投递同一 message_end 不被 (turn_id, call_seq) 唯一约束覆盖（call_seq 按 enumerate 分配） | ✅ 已修：采集器按 `(kind, message.timestamp)` 去重（vendor 每消息唯一 ts；无 ts 时仍由 DB 约束兜底）；新单测钉死 |
| 7 | P3 | 价格表版本声明了但未随行落盘（estimated 不可审计到价版本）；cacheWrite 未计价 | ✅ 已修：estimated 行 usage_json 盖 `cost_source` + `price_table`（pi-model-catalog / llm_price.v1）戳；cacheWrite 不计价为已知低估（estimated 语义披露） |
| 8 | P3 | 汇总 cost_source 用最近 N 行采样，与全量总额混算 | ✅ 已修：SQL 分组对全部 turn 聚合 |
| 9 | P3 | 前端失败读回（ok=false）仍显示 effective 徽标（vendor 报告的错误模型）；status 显示原始 token | ✅ 已修：徽标 gate 在 `liveStatus.ok !== false`；status 走 `chat.model.status.*` i18n |
| 10 | P3 | thinking_level 接受存储但永不生效（端点契约不支持） | ✅ 已修：非空 thinking_level → 409 明确拒绝（待端点确证后放开）；新单测 |
| 11 | P3 | stop() 与 reader EOF 竞态下残留 pending 等待窗 | 📝 接受为 note（stop/recycle 仅 turn 间执行，pendings 非预期；PI_STARTUP_READY_TIMEOUT 有界） |
| 12 | P3 | 工作树散置未跟踪文件（j18/browser test） | ✅ 有意新增文件，随修复 commit 一并入库 |
| 13 | P3 | ORM index=True 与迁移命名索引漂移 | ✅ 已修：ORM 改为 `__table_args__` 内与迁移同名索引（idx_ctr_session_created / idx_cur_session / idx_cur_turn） |

## R2：复审（reviewer：同 R1 独立子代理；对象 = 7096c0e54（3 commits）vs base）

**结论：APPROVE-WITH-NOTES** —— 13 项 R1 发现全部确认解决或按 note 接受，无新 P1/P2；浏览器车道（gating/teardown/断言）复核为正确。5 个残留 P3：

| # | 残留 | 处置 |
|---|---|---|
| R2-1 | history_context 取最近行无过滤 —— budget_rejected/failed 回执 tokens_input=0，会把历史误判为 0（单 turn 超支窗口） | ✅ 已修：查询加 `tokens_input > 0`；新单测（零输入行跳跃）钉死 |
| R2-2 | timestamp 去重依赖 vendor ts 在场/唯一（同毫秒同 kind 理论误去重） | 📝 note（模型调用间隔 ≫1ms；无 ts 时 DB 约束兜底） |
| R2-3 | usage_json 形状条件化（cost_source/price_table 仅在已知时在场）—— 未来读者不得假设键在场 | 📝 note（当前无消费者；写入侧文档化） |
| R2-4 | j18 预算腿用 inherit 重写 preference（未验证显式选择存活）+ 文案耦合（响亮失败，非静默绿） | ✅ 已修：预算 PUT 改为保留 explicit g37-b 并断言响应偏好仍为 g37-b；文案耦合保留（失败即红，是正确失败模式） |
| R2-5 | _wait_http 接受 <500（4xx 误判就绪） | ✅ 已修：<400（HTTPError 分支同步收紧） |

修复后复验：42 单测通过（含 3 个新钉死测试）；前端 tsc 通过。

## 评审过程注记

- 评审者为只读（曾误建空 `.venv` 后自清）。
- R1 报告完整原文由 reviewer 子代理产出；上表为逐项处置对照。
