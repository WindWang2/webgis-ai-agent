# G29 LOOP_LEDGER — 执行账本

## 检查点

- base: ca7b3ec273f2b43336852951e2292a416b2238e6（= origin/master 起点；open PR=0）
- 分支：zcode/g29-pi-native-interaction-20261112T144716Z
- subagents：#1 Explore（研究，已完成）；#2 独立 reviewer（评审，未参与实现）；
  预算声明 ≥320M tokens（宿主实际额度由运行环境决定，未伪造）。
- 重阶段锁：.bare/webgis-heavy-stage.lock（vendor 构建/旅程/回归均持锁）。

## 轮次记录

### R0 研究（完成）
PR 全量分页（open 0；closed 近 50 + #2084-2093 评论全空，评审在 planning/G2x）；
G22/G28/G23/G24 台账 + ADR 0285/0286/0288/G22/0282 读取；grep 证实 extension_ui_request/
ctx.ui/steer/follow_up 宿主零实现（DEDUP_MATRIX §1-14 missing 判定成立）。

### R1 WP02+WP03+WP07（完成）
pi_ui_protocol + reader 分支 + send_ui_response + stdin 写锁；交互服务状态机；
bridge 等待记账（stream + 非流式两处）+ deferred 冲刷 + teardown 失效。
单元 28 绿（含双标签页幂等、白名单、超时、cap）。

### R2 WP05+WP06（完成）
封套 HMAC（canonical JSON 递归键序）+ 扩展 ctx.ui 闭环 + resolve 端点 +
webgis_user_decision 工具 + layer_upsert 裸名歧义分支。L1 旅程脚本骨架。

### R3 WP08+WP09+WP10（完成）
卡片/适配器/reducer/SSE 四分支/提交 handler/刷新水合/steer 入口/i18n；
tsc 0 错误；vitest 56 绿（含 use-sse-stream 既有 46 零漂移）。

### R4 WP12 旅程迭代（完成，关键修复清单）
1. fakeredis TcpFakeServer 默认 Resp3Writer ↔ redis-py RESP2 `Protocol Error: b'_'`
   → driver 内 Resp2Writer 补丁。
2. scale_portrayal_tools 既有注册失败（master 同样，未修——非本任务）。
3. 动态面：盲 layer_upsert 不在 default_active（7 工具）→ 旅程走
   map_intent 两段式（真实激活链）+ tool_search；webgis_user_decision 入前门。
4. **扩展签名缺陷（本任务 bug，已修）**：digest 少内层 sha256 + canonical 只排顶层键
   （Python sort_keys 递归）→ `canonicalJson` 递归 + 双层 hash；单测补跨语言一致性。
5. **封套一次性消费缺陷（本任务 bug，已修）**：native 配对即 pop awaiting，resolve
   再 pop 落空 → 封套全文挂记录（record.envelope），consume 从记录读。
6. 驱动细节：播种持久 loop（锁绑定 loop）；verdict 文件优先 + os._exit 前手动冲刷；
   as_of tag 入显示名；turn 完成后 resolved 复询。
7. layer 无 `source` 字段被 MapSpec 拒绝 → master 既有行为（stash 对照复现），旅程
   采用带 source 的合法形状（真实模型亦须如此——工具描述含 id/source/type）。

### R5 终验（完成）
- L4 旅程 pytest：1 passed（J29A-D + 越权负例全绿）。
- L1 72 passed（g29 单元 + cartography + sse 契约 + clarification 回归）。
- L3 56 passed + tsc 0。
- 既有失败披露：810-SIGKILL / streaming-disconnect（环境，base 复现）。

## 决定记录

- 等待预算：豁免 stall + total 按记账展宽（帽 PI_INTERACTION_MAX_WAIT_S），不暂停
  heartbeat —— 与任务 §WP07 一致；原生 timeout 以 vendor 为准（宽限后标 timed_out，
  不再写 stdin）。
- 回答不阻塞等 ACK：stdin 写出成功 = answer_written；resolved 由扩展 resolve 回调或
  真实后续事件观察 —— UI 如实显示「已送达等待继续」。
- steer 在 control gate 内做 entry 同一性复核 + issue（与 abort 同款原子段）；写出后
  收响应返回真实 disposition；用户文字 best-effort 入转录。
- 每会话 pending 上限 3 / per-turn 封套 2：超限立即合法取消（不悬挂 vendor promise）。
- 不做 SessionStore 快照持久化：进程内服务 + ring 重放 + GET 水合已满足「活代际可
  恢复作答 / 跨代际只读终止」；boot_id 使旧视图不可作答。诚实披露（见 PR 未测项）。

### R6 独立评审 + 修复（完成）
reviewer（未参与实现）：APPROVED-WITH-NITS（0×P0/P1；2×P2 修：awaiting 表
实例化+修剪、候选不截断；P2-3 文档过度声明 → 补 committed 跨语言签名单测）。
P3×9 全修（expire_due 锁跳过 / cap 判定先于配对 / steer i18n / 台账 LRU /
跨会话守卫补齐 / 杂散文件 / 精确断言 / confirm 去重 / SessionStore 声明删除）。
复验：单元 29、旅程 1 passed（复跑）、前端 56+tsc+eslint、ruff 全过。
详见 REVIEW.md。

## 下一步

复审 → commit/push/PR。
