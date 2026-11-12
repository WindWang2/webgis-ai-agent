# REVIEW — G22-PI-STATE-CONTINUITY（独立复核，2026-11-12）

复核人：独立 reviewer（未参与实现）。BASE `b8f2a6ecc`；worktree 分支
`zcode/g22-pi-state-continuity-20261111T151408Z`。全部断言均由本人在
worktree 内静态审读 + 实跑/脚本实证得出。

## 发现（P0 最重）

| # | 级 | 位置 | 发现 | 证据/推演 |
|---|---|---|---|---|
| 1 | **P1** | `app/services/context_assembly/providers.py:672-677` | G16 标注覆写投影读 **dict**（`isinstance(overrides, dict)`），而 G16 唯一权威写路径持久化的是 **list**（`app/services/mapspec/mutation_handlers.py:355` 读 `isinstance(..., list)`；`:414` 写 `{"overrides": next_overrides}`（list）；`app/tools/label_authoring_tools.py:327`、`app/services/mapspec_to_svg.py:2045` 同读 list）→ 真实 G16 数据下 **A14 投影恒为死代码**，恢复目录永不包含标注约束。A14 单测（`test_gis_state_rebuild_provider.py:129-141`）与 j17 e2e 夹具（`frontend/e2e/fixtures/sse.ts:213,244`）都用 dict 形状 —— 测试断言的是自己发明的形状（circular）。`recovery_summary.py:152` 却 dict/list 双收 —— 同 PR 内口径互相矛盾，坐实 providers 侧是 bug 而非契约。 | 脚本实证：list 形状（真实）→ 无标注行；dict 形状（夹具）→ 有。ACCEPTANCE A14 "pass" 在真实数据上 vacuous。 |
| 2 | **P1** | `providers.py:770-786` + `contract.py:358-361` | 「约束永不因截断消失」不成立：内部 1540 预算只约束**通用行**，constraint/recovery 行无条件追加；`bounded_item` 对 GIS_STATE_REBUILD 域 **1600 硬截断**（`content[:1599]+"…"`）切的是块尾。真实长度 id（uuid 级）下约束行自身即超 cap：实测 26 层/22 隐藏/16 锁 = **2030 字符**（>1600），截断后**整行丢失**：多画幅绑定行、非终态任务行、失效引用披露行、连「目录截断」披露行本身也被切掉 —— 模型既丢约束又不知道被截断。A13（隐藏）因排序靠前幸存，A14/A15/A16/A18 在挤压下可被静默丢弃。 | 本人构造 >24 层 + 超界锁/隐藏/标注实测（两种场景 2030/2245 字符，截断后缺 4-5 行）。`test_g22_constraints_survive_budget_pressure` 用短 id（`visible-39`）+1 锁，测不出。 |
| 3 | **P2** | `app/agent_pi_bridge.py:1497,2109,2149`（注释）vs 全仓实现 | 注释三处引用的 `_drain_control_rpcs`（teardown 交接前有界等待在飞控制 RPC）**不存在**——全仓 grep 仅命中注释；`_inflight_control` 只被 add/discard/soak 测尺寸，无任何等待方。PLAN.md P1 条目 3（"新 turn 准入严格晚于旧 turn 取消动作完成…互为闭环"）未实现。已实现的门只保证**字节序**（abort 写出在门内、register 同门后置）；而 vendor rpc 模式 stdin 分发是 fire-and-forget 并发（vendor `chunk-JW5DXUQE.js` runRpcMode：`attachJsonlLineReader(stdin,line=>{handleInputLine(line)})`，prompt 分支不 await、abort 分支 `await session.abort()`），门释放后 T2 prompt 字节可即刻写出、vendor 内新 run 可在旧 abort **效果落地前**启动 —— mid-window（abort 已写出、T1 自然收尾、T2 准入）下隔离不完全。 | grep 全仓无定义；vendor bundle 源码核读。ADR D1 本身只主张字节序（已实现），但代码注释/PLAN/TEST_EVIDENCE（"交接门台账"）描绘了更强的未实现机制。 |
| 4 | **P3** | `app/services/chat/pi_rpc_client.py` `issue()` | 写相位被取消（`run_in_executor` await 处 CancelledError）时 pending 项不回收 —— 旧 `request()` 的 finally 无条件 pop（F19），新 `issue()` 只在 BrokenPipe/OSError 分支 pop。executor 线程写成功时响应方可代收，写失败则泄漏一条 pending。 | 对照 diff：try 块无 finally。 |
| 5 | **P3** | `agent_pi_bridge.py:2126` / `:1193` | abort 取门**无超时**（`async with`），register/unregister 有 5s 降级：一条 wedged stdin 写持门时，后续所有 abort 无限挂起（旧代码只挂自己那条）。且 5s 降级路径披露不准：PR_BODY 称"写前身份校验兜底"，但该校验在写阻塞前已通过，解冻后不再复核 —— ungated register 下 T2 prompt 字节可越过 T1 abort 字节（两个 executor 线程无序）。属已披露残余风险的细化，非新缺陷。 | 推演；PR_BODY 残余风险段。 |
| 6 | **P3** | `app/services/chat/pi_turn_context.py:529-547,558-563` | ① 同宿主多进程（uvicorn --workers / dev+test 并跑）共享 hostname 前缀：worker B 启动清扫会删 worker A 的 live key（多 pod 安全 ≠ 多进程安全；需显式 WEBGIS_INSTANCE_ID）。② `_parse_stored_turn` 用 `split(":",1)`：instance id 含 ":" 时跨 pod is_active 误判。turn id（`turn-<hex>`）无冒号，turn 段本身安全。 | 代码推演。 |
| 7 | **P3** | commit 52c944e62 消息 / `test_pi_rpc_client.py:386` | ① 消息称"13 个 capability id"，种子文件实为 **12** 个（与工具 descriptors 的 12 个 dangling id 吻合，boot 已通，数字系笔误）。② 迁移后删掉了 `assert result == {"ok": True}`（abort 返回值不再断言；其余断言面更严：`request.assert_not_called()`）。 | 数数 + diff。 |

**核对为真的关键声明**（不列缺陷）：ProbeRpcClient 在 `issue`（写相位）内经
`get_active_turn_entry` 读**真实**注册表（a ✓，A07 三态 + 门序测试设计正确）；
锁/帧/undo/ACTIVE_STATUSES 均为真实契约函数（b 其余项 ✓）；N4 needle +
`llm_calls_observed_before>=1` 反 vacuous 锁真实有效、J2 计数非 vacuous（d ✓）；
chat 路由默认响应无 recovery 键、flag 才附挂、失败降级不 500，TestClient 走真
路由（e ✓，摘要白名单无载荷/凭据泄漏）；Redis 双格式 owner-checked 注销 +
仅本实例前缀清扫、旧格式不动（f ✓，见 #6 边界）；快照再生仅晨间端点
（briefs/data-import/field-compute/data-refresh），无 G22 面混入（j ✓）；6 文件
mock 迁移保持原断言意图（k ✓，见 #7②）；capability 种子描述忠实于工具
descriptor、platform/planned 与 G15 惯例一致、BASE 破损属实（BASE 有 8+2+2
处 capabilities= 声明而无种子包）（i ✓）；vendor HEAD = v1.5.2（`b4afdddf`）；
`git status` 仅 planning/ + ADR 未跟踪，无其他遗留。

## 复跑结果（本人实跑，worktree）

| # | 命令 | 结果 |
|---|---|---|
| 1 | pytest 6 个 G22 新套件（turn_isolation/reconciliation/dedup/soak/recovery_summary/rebuild_provider） | `43 passed in 8.63s` |
| 2 | pytest cancellation_unified/bridge_leak/bridge_concurrency/pool_v5b/rpc_client | `44 passed, 32 warnings in 57.36s` |
| 3 | `scripts/pi_doctor.py` | `no FAIL`；vendor `PASS pin ok (v1.5.2-0-gb4afdddf4); floor v1.5.1`；build/extension/surface(428 tools)/rpc/env 全 PASS |
| 4 | pytest `tests/unit -k "pi"` | `1 failed, 1883 passed, 78 skipped, 1 xfailed in 333.29s`；唯一失败 `test_response_model_coverage`，缺 response_model 的端点（raster-authoring 等）及其路由文件均在 BASE 存在，G22 仅触 chat.py（保留 response_model）→ **确系 BASE 既有债** |
| 5 | vitest recovery-banner + changed-lanes×2 | `3 files / 25 tests passed (1.11s)` |
| 6 | playwright j17（:3310 --webpack 复用） | `1 passed (14.2s) + 1 skipped`（real 档占位，测试内自披露） |
| 7 | `PI_JOURNEY_E2E=1` stub LLM journeys（flock 持锁） | `1 passed in 26.45s` |

## 结论：**BLOCK**

阻塞项（需修复后重审）：
- **发现 1（P1）**：A14 在真实 G16 数据上 vacuous —— providers.py 读 dict、
  权威写路径是 list；测试夹具（单测 + e2e）需换真实 list 形状重证。
- **发现 2（P1）**：约束行绕过内部预算 + 1600 硬截断可静默丢弃 A14/A15/A16/A18
  披露行（含截断披露本身）—— 与 ACCEPTANCE「约束永不消失/pass」直接矛盾；
  需约束段自身让位策略或提高该域 cap 并以真实长度 id 复测。

发现 3（P2）不单独阻塞（门 + entry 同一性已给出实质保护、ADR 主文未夸大），
但三处失实注释与 PLAN 条目必须改为与实现一致，或补实现 drain；#4-#7 建议随手
清偿。其余方向（P0 pin、P3 对账、P4 去重、P6 UI、N4、种子/快照、mock 迁移、
A22/A24 证据）复核为实，可作为修复后的合并基础。

## 第二轮复审（fix 轮 ad3b5d81c，2026-11-12）

> 复核范围：`git show HEAD`（7 文件）逐项对照第一轮发现；实跑 4 组命令 +
> 两轮对抗性脚本（144 组维度扫描）。结论：**BLOCK（仅剩 1 项，范围已大幅收窄）**。

### 逐项裁决

| # | 第一轮发现 | 裁决 | 证据 |
|---|---|---|---|
| 1 | P1 标注覆写读 dict（真实 list 恒空） | **FIXED** | `providers.py::_label_entries` 以 list 为权威（`[{featureId,…}]`，与 `mutation_handlers.py:355/414` 写路径一致），dict 仅读容错并另有容错测试钉死非权威；A14 单测与 `frontend/e2e/fixtures/sse.ts`（`J17_LABEL_OVERRIDES`）均换真实 list 形状；j17 复跑 1 passed。 |
| 2 | P1 截断吞约束（约束行绕预算 + 1600 硬截断切块尾） | **PARTIAL — 仍阻塞（范围收窄）** | 三级打包器本身有效：144 组维度扫描中**锁/隐藏/标注/画幅/任务五行在全部组合存活**（第一轮任意约束可丢 → 现仅尾部两行）。但修复的保证「≤1600 / 全类别在场 / 披露行收尾」不成立：一旦累计越过 1500 检查点，force-append 兜底逐行续加而不做**总长**兜底 → 重负载下渲染超 1600（实测多组合 1638–1695；修复方自测 1579 仅是其特定维度恰好压线），`bounded_item` 硬截断切尾。**具体复现**（全程真实量纲）：60 层/22 隐藏/8 层锁/8 组件锁/4 标注层/6 画幅 + 8 任务 + 6 失效 ref → 渲染 **1693**，交付文本末行为「失效数…」——A16 失效引用披露（类别名+计数）整体丢失、压缩披露行整体丢失。A16 幽灵图层防护与「压缩必披露」两条验收在负载下失效。修法方向（任一即可）：`_join_len` 预留披露行+末尾类别最坏长度；或组装后总长超限时对已收行整体降档重排；或单行可接受长度上限使检查点+兜底之和 ≤1600。 |
| 3 | P2 `_drain_control_rpcs` 幽灵引用 | **FIXED（代码）/ PARTIAL（文档+测试）** | 方法已实现（`agent_pi_bridge.py:1600-1626`，有界 5s `asyncio.wait`，超时告警放行）并接入全部 4 个 teardown 站点（2741/2782/3438/3542，均在 `_safe_unregister_active_pi_turn` **之前**）；PLAN 条目 3 与 PR_BODY 残余风险段已改写为实现口径。缺口：① `docs/adr/G22-…md` D1 未同步——bridge 注释与 PR_BODY 均称「残余记录于 ADR D1」但 D1 无此句（悬空引用）；② drain 与 #5 门超时 fail-safe 均无任何测试（第一轮的失效模式恰是「有注释无实现无测试」，现实现无测试同样挡不住回归）。不单独阻塞。 |
| 4 | P3 issue() 写相位取消泄漏 pending | **FIXED** | `except BaseException: pop; raise`（pi_rpc_client.py:852-858）；`_handle_response` 对 pop 后迟到响应静默忽略（pop 默认 None + done 检查，:714-715），幂等安全。 |
| 5 | P3 abort 取门无界 + 降级披露不准 | **FIXED** | 取门 5s 有界；超时**整体跳过**（fail-safe：宁可不取消不误伤后继），try/finally 保证释放；代码注释 + PR_BODY 残余风险段与新行为一致。无测试（见 #3②）。 |
| 6 | P3 实例 id 冒号 + 同宿主多进程误扫 | **FIXED** | `_instance_id` 冒号清洗为 `_`；docstring 明示同宿主多进程必须显式互异 `WEBGIS_INSTANCE_ID`（部署披露即我方要求）。 |
| 7 | P3 种子数 13≠12 + result 断言削弱 | **FIXED** | PR_BODY 改「12 个 capability id」（种子 commit 消息为 pre-push 历史已在 PR body 注记）；`rpc.collect=AsyncMock(return_value={"ok":True})` + `assert result == {"ok": True}` 恢复。 |

### 第二轮实跑

| 命令 | 结果 |
|---|---|
| 13 套件回归（修复方称 205 passed） | `205 passed, 32 warnings in 100.96s` ✓ 一致 |
| 对抗脚本（新量纲 40 隐藏/20 锁/10 组件锁/8 任务/6 ref + 144 组扫描） | 类别存活面大幅改善，但发现 #2 残余：多数重组合渲染 >1600（最高 1695），`refs`+披露行被硬截断切掉（详见上表复现） |
| playwright j17（:3310 复用） | `1 passed (14.1s) + 1 skipped`（real 档占位自披露） |
| `PI_JOURNEY_E2E=1` 旅程（flock） | `1 passed in 25.12s` |

### 总裁决：**BLOCK**

唯一阻塞项 = **#2 残余**：打包器逐行检查点 + force-append 兜底缺总长约束，
重负载（量纲全真实）下渲染超 1600 域 cap，`bounded_item` 硬截断静默切掉
**A16 失效引用披露行与压缩披露行**（复现与修法方向见上）。第一轮其余六项
全部 FIXED（#3 带 ADR 悬空引用与 drain/门超时零测试两个非阻塞尾巴，建议
随手清偿）。#2 修掉后无需全量重审——重跑 13 套件 + 我方 144 组扫描脚本
（或等效属性测试：任意量纲下 ≤1600 且七类标记全在）即可放行。

## 第三轮（收窄确认，fix 轮 2 f4c7ba136，2026-11-12）

> 范围：仅 HEAD 一个 commit（`fix(g22): 二轮复审残余清偿`）。静态审读
> 打包器 v2 / drain discard / 两条新测试 / ADR；按二轮放行条件复跑
> 13 套件 + 本人**独立新写**的对抗扫描（未复用仓库内 sweep 测试）。

### 逐项裁决（对照二轮唯一阻塞项 #2 残余）

**打包器 v2 — FIXED。** 预算 = 1600 − `reserve`（披露行长 + 1 + 头行数）
− 头部连接长：追加披露行的精确增量恰为 `len(disclosure)+1`，故 slack ≥
头行数 ≥1，总长 ≤1600 是**构造性**不变量（`_fits` 逐条守门 + 降级只缩不
涨），assert 仅兜底。四档 full→compact→count_only→minimal；minimal 不入
时逆优先级降级已收条目（undo 条目四档等长 → 降级无效但无害，走丢弃
兜底）。通用行同预算逐行守门。检查过各档行内 id 截断：层 id[:48]、
featureId[:12]、frame_id[:24]/title[:16]、active[:32/16]、job id[:14]、
ref[:24]——自由字符串均截断；唯一未截断的是 `product_kind`/undo `revision`
（G15/G14 枚举/整型，非自由 id，见下「残余观察」）。

**drain discard — 安全。** 滞留 future 移出 `_inflight_control` 只影响后续
drain 的等待集；`collect()` 直接 await `issued.future`（自带
`PI_RPC_TIMEOUT`），响应经 `_pending_requests` 独立解 future——无任何
await 方被孤立。双层 finally 各 5s 不再叠加，与补测的 4s≤t≤10s 断言一致。

**二轮两个非阻塞尾巴 — 均顺手清偿**：① ADR 文件落地，D1 含残余段
（vendor 并发分发/响应等待窗/wedged 门 fail-safe/WEBGIS_INSTANCE_ID/
冒号清洗）——悬空引用消除；② drain 真实等待+有界、abort 门 wedged
fail-safe（零写出、不挂起）各有测试。

### 本人对抗扫描（独立脚本，非仓库 sweep 测试）

`/tmp/rev3_adversarial.py`，种子 20261112：600 组随机量纲（层 0–80、
隐藏 0–60、层锁 0–24、组件锁 0–10、画幅 0–16、任务 0–10、失效 ref 0–10、
标注层 0–8、undo 点 {0,1,3,5}；uuid4 全长 id；隐藏/锁约束 ≤ 层数）+
12 组病态/角落（400 字符层 id × 满量纲、60 画幅、400 字符 id + 60 画幅、
全满、全零、80 层无隐藏、60/60 全隐藏、0 层 + 24 孤儿锁、纯 jobs+refs、
0 层 16 画幅、80 层全标注、400 字符 product_kind）。断言：P1 len≤1600
（且 =1600 时不得以 "…" 结尾 = 硬截断未触发）；P2 在场类别标记必在
（锁定/隐藏/标注/多画幅/非终态任务/失效/撤销栈，substring）；P3 缺席类别
不得虚构（按行前缀判，排除 >24 层自披露行的 substring 假阳性）；P4 出现
「因预算省略」⇒ 披露行必在且为末行；P5 披露行恒为末行；P6 内部 assert
兜底不触发。

| 指标 | 结果 |
|---|---|
| 用例数 | 612（600 随机 + 12 病态/角落） |
| 最大渲染长度 | **1535**（≤1600，余量 65） |
| 含披露行用例 | 358（重载组合均如实披露） |
| P1 超长 / P2 类别缺失 / P3 虚构 / P4-P5 披露非末行 / P6 assert | **0 / 0 / 0 / 0 / 0** |

残余观察（不阻塞）：`product_kind`/undo `revision` 未逐字截断——构造
1500 字符 product_kind（远超 G15 枚举域）可触发「丢弃 + 披露」兜底路径
（实测 len=226、披露行在场且末行、有界不溢出，与代码注释自述一致）。
真实自由字符串（层/锁/ref/frame id）均截断且 400 字符病态 id 全部通过。

### 第三轮实跑

| 命令 | 结果 |
|---|---|
| 13 套件回归 | `208 passed, 32 warnings in 125.55s` ✓ 与修复方一致 |
| 本人对抗扫描（612 组，独立脚本） | 全属性成立（上表），max 1535 |
| playwright j17（:3310 复用） | `1 passed (22.8s) + 1 skipped`（real 档占位自披露） |
| `PI_JOURNEY_E2E=1` 旅程（flock 持锁） | `1 passed in 41.42s` |

### 总裁决：**PROCEED**

二轮唯一阻塞项（#2 残余）确认清偿：≤1600 成为构造性不变量（本人 612
组独立扫描 max 1535，零违例），七类标记任意量纲在场、无虚构、降级必
披露且披露行收尾；二轮两条非阻塞尾巴（ADR 悬空引用、drain/门零测试）
亦已清偿。可合并。
