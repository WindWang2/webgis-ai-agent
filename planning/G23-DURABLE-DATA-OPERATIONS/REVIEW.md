# G23 独立评审（REVIEW）

- 评审人：独立评审（未参与实现）｜日期：2026-11-12
- 范围：`git diff b8f2a6ecc..HEAD`（评审期间分支新增了第 6 个 commit `d72225bce`「提交窗口崩溃的恢复路径补全」，本评审按含该 commit 的 HEAD=d72225bce 出具；任务书原文为 5 个 commit）
- 材料：全量 diff 逐文件阅读（产品代码/测试/集成脚本/planning 文档）、关键用例自行复跑、可疑点用 /tmp 一次性脚本实测

## Verdict：request-changes

1 个 BLOCKER（P0）：`_load_owned` 归一化路径的 check-then-act 竞态 —— 我用真实代码路径确定性地复现了「job=completed 而 batch.json 被 stale 快照覆写回 configured、收据丢失」的终态（见 B1）。窗口为毫秒级、仅跨进程（同进程事件循环内该路径无 await，天然原子），且严格优于 BASE（BASE 任意跨进程读都会整体复位），但它是本方向核心承诺（A03/A11 恢复判据、A12 effectively-once）的直接残留漏洞，修复代价小（写前 rev 复核/重读合并），应在合入前修掉。其余发现均非阻断。

### 复跑结果（全部自行执行，不采信自述）

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests/data_operations -q --no-cov -p no:cacheprovider` | **25 passed** in 19.85s（HEAD；d72225bce 之前为 24 passed） |
| `.venv/bin/python -m pytest tests/data_import tests/jobs tests/delivery -q --no-cov -p no:cacheprovider` | **345 passed**（data_import 41 + jobs 247 + delivery 57） |
| `.venv/bin/python -m pytest tests/data/test_field_compute_{api_v1,review_v1,v1}.py tests/unit/test_field_compute_tools_v1.py -q` | 82 passed, **1 failed**（`TestOpenApiCompatibility::test_existing_short_schema_names_preserved` —— 我在 b8f2a6ecc 干净 worktree 上独立复跑同样失败，确认预存，与 G23 无关） |
| `bash planning/G23-DURABLE-DATA-OPERATIONS/bin/run_repro.sh .venv/bin/python /tmp/g23-rev-repro` | **false_reset_reproduced=false**，A 侧 completed、收据 completed（双真实进程 + 共享 SQLite/DATA_DIR） |
| 同脚本打在 **BASE**（临时 worktree b8f2a6ecc + 共享脚本，跑完已清理） | **false_reset_reproduced=true**（B 读把 COMMITTING 复位 configured）—— 误复位 bug 与修复均独立证实 |
| 双 uvicorn 集成 `.venv/bin/python bin/integration_two_process.py /tmp/g23-rev-integration`（独立复跑，2×真实 uvicorn + 共享 SQLite(WAL)/DATA_DIR + 真实 HTTP） | **S1-S5 全部 pass:true（5/5）**：S1 B 读不重置+跨副本见 running/kind=data_import；S2 B 取消→双面 cancelled；S3 B 重提→completed+收据可读；S4 kill -9 A→B 诚实 committing→注入 stale→归一化 configured→A 重启视图一致；S5 compute B 执行→A 查 running→A 取消→cancelled。RSS 峰 A≈742MB/B≈821MB，DATA_DIR 峰≈40MB |
| TOCTOU 复现脚本（/tmp/g23_review_toctou.py，真实代码路径 + 冻结读） | **CLOBBER_REPRODUCED=true**（B1 的证据） |

---

## Findings

### BLOCKER (P0)

#### B1. `_load_owned` 归一化写入是「读快照 → 探 DB → 无条件覆写 batch.json」——终态迁移窗口内用 stale 快照覆写 runner 的最终落盘，收据丢失
- **文件:行**：`app/services/data_import/service.py:177-221`（判据查询 178、归一化写 221 `staging.save_state(state)` 无 rev/CAS）；同款问题在活跃分支的补记写 `service.py:182-186`（该分支含 `await run_in_executor`，窗口更大，但仅在 active_job_id 失配时触发）
- **问题**：读路径把「归一化决策」建立在 T0 时刻的 batch.json 快照上，DB 探测（`_active_commit_job`）却在 T2 才返回。runner 的收尾顺序是：最终状态写盘（COMPLETED/收据）→ `mark_succeeded` 落 DB 终态。若读者 [T0 读文件, T2 探测] 跨越 runner [最终写盘, DB 终态]，读者会拿 T0 的 stale 快照（缺最后落账的收据）走归一化并**无条件覆写**：最终 batch.json=CONFIGURED/PARTIAL、收据丢失，而 job 行=completed —— 两个事实源永久不一致；重提交时 `pending_members` 看不到收据 → 重复导入并发布新 ref，违反 A12 effectively-once。同进程因事件循环内无 await 而免疫；跨进程（生产 replicas≥2 + UI 轮询批次端点，轮询恰在完成点附近）为毫秒级窗口的真实命中面。runner 最终块（service.py:582-599）虽然锁内重读 `fresh`，但若覆写落在重读之前，最终状态会基于被覆写的文件铸成 PARTIAL——两个时序都丢收据。
- **证据**：/tmp 一次性脚本（真实 `start_commit`/runner/DB 路径，仅把读者的一次文件读「冻结」在提交中时刻以替代慢读）实测：runner 完成后 job=completed、batch.json=completed+收据；经 `svc_b.get_batch` 归一化后终态 batch.json=**configured、receipts=[]、rev=5**。脚本未入库，可按需提供。
- **建议修复**（小改）：归一化写盘前重读文件比对 `rev`——`fresh = staging.load_state(batch_id); if fresh.rev != state.rev: return fresh`（丢弃本次归一化，交下一次读收敛）；或给 `staging.save_state` 加期望 rev 的 CAS。补记写（182-186）同法。配套加一个跨 DataImportService 实例的回归测试（模拟慢读：load 冻结 + 等 job 终态再放行）。
- **阻断**：是（request-changes 的唯一阻断项）。

### MAJOR (P1)

#### M1. A22 验收标记 ✅ 与证据不符：两个新状态工具与「超时→job 句柄」分支零测试覆盖
- **文件:行**：`planning/G23-DURABLE-DATA-OPERATIONS/ACCEPTANCE.md` A22 行；`app/tools/data_import_tools.py:296-360`（import_commit_status）、`app/tools/field_compute_tools.py:433-500`（field_compute_status）、两提交工具的 wait 循环（data_import_tools.py:311-318 / field_compute_tools.py:391-401）
- **问题**：`import_commit_status`、`field_compute_status` 在 tests/ 下没有任何按名调用；`wait_max_s` 超时返回 job 句柄的分支（G23 对 Pi 契约的核心改变）也无测试。A22 引用的 "test_field_compute_tools 6/6" 是 BASE 的 6 个既有工具测试，不覆盖新工具；`tests/data_import/test_tools_dispatch.py` 4/4 只经由真实 dispatcher 走了 `import_batch_commit` 小 fixture 即完成的路径（我已复跑确认）。A22 应降为 🟡 或补测。
- **建议**：补 4 个测试：两个 status 工具（活跃 job / 终态+可读 ref / 不存在批次 404 语义）各至少 1 个；一个 wait_max_s=0 强制走 job 句柄分支的提交测试（断言返回 job_id+guidance 而非伪造完成）。
- **阻断**：否（但 A22 的 ✅ 需先修正为如实状态）。

### MINOR (P2)

#### m1. 测试中的死断言/死代码虚增覆盖
- `tests/data_operations/test_runner.py:176-177`：`ok_forged = ... if False else True` —— 伪造身份进度写从未执行（A06 的「他人写入被拒」实际未测，只测了终态后拒写）；
- `tests/data_operations/test_compute_jobs.py:97-98`：`assert await rc.find_receipt(...) is None or True` —— 恒真，A10 的「无收据」断言无效；
- `tests/data_operations/test_compute_jobs.py:153`：`... if False else None` 死行。
- 建议：改为真实断言（如直接查收据文件/DB）。阻断：否。

#### m2. `wait_max_s` 运行时无上界
- **文件:行**：`app/tools/data_import_tools.py:287,316`（无 le 上界）；`app/tools/field_compute_tools.py:53`（Args 模型 le=600，但工具函数运行时参数未再校验）
- 问题：调用方可传任意大值长时间占用工具调用，与「Pi 工具不长时间占用调用」的最坏情形相悖。建议运行时 clamp（如 min(wait_max_s, 60)）。阻断：否。

#### m3. 归一化路径在事件循环上同步写盘 + 终态集合硬编码重复
- **文件:行**：`app/services/data_import/service.py:221`（`staging.save_state(state)` 同步调用；同文件其他写都走 `to_thread`）；`service.py:80`（`("completed","failed","cancelled","stale")` 硬编码，重复 lifecycle.is_terminal，新增终态时会漂移）
- 建议：`await asyncio.to_thread(...)`；改用 `is_terminal(coerce_status(...))`。阻断：否。

#### m4. `async_durable_job` 在 `_claim` 失败时泄漏 cancellation registry 条目
- **文件:行**：`app/services/data_operations/runner.py:264-266`（register 在 claim 之前，claim 抛 AlreadyFinished 不经过 try/finally 的 discard）
- 影响：被 LRU(2048) 兜底、且有 DB 探针兜底语义，实际风险小；但并发补启动（d72225bce 之后是常态路径）每次认领失败都留一条。建议 claim 抛错时显式 discard。阻断：否。

#### m5. compute 内联路径把「仍在 running 的 job」报成 JOB_INCOMPLETE（措辞自相矛盾）
- **文件:行**：`app/services/data_preparation/field_compute/service.py:505-510`；`app/services/data_operations/compute_jobs.py:151-156`（join 超时→重提→reused→返回 status=running 的 dict）；`compute_jobs.py:168-170`（background 返回 "status": "running"，实际此刻 job 还是 pending）
- 问题：`执行未成功（job X 终态 running）` —— running 不是终态；语义应为「仍在执行/排队，请轮询」。重提安全（同键收敛），不造成错误状态，但误导调用方。建议区分 in-progress 与 failed 的报文。阻断：否。

### NOTE

1. **幂等键 owner scope 的入口分歧**：HTTP 路由传 `JobActor(user_id/owner_token)`（routes/data_import.py:250-253），Pi 工具路径不传 actor → scope="anon"（data_import_tools.py:305）。同一批次经 UI 与经 Pi 提交会产生不同幂等键；正常流由「COMMITTING 门禁 + 收据短路」兜住（不双跑），但 A01 的「同一逻辑提交收敛」只在同类入口内严格成立。staging 归属本就按 session 判定，建议在 INTERFACES.md 记录该边界。
2. **delete_batch 保留同类 check-then-act 窗口**（service.py:262-282：查活跃 job → rmtree，跨进程可能在另一副本「建 job 未落 COMMITTING」间隙删掉 staging）→ 执行侧 typed 失败可恢复；与 B1 同类但窗口更窄，B1 的 rev/CAS 手法可顺带覆盖。
3. **data_operations 全线在事件循环上直接跑同步 SessionLocal 短事务**（submit/probe/progress/heartbeat）：与 G18 data_delivery 既有范式一致（非本方向回归），但生产 Postgres 时每次心跳/探测都会短暂阻塞整个循环，值得后续统一 to_thread 化。
4. **死代码**：`runner.py` `manual_finish` 参数、`find_active_job_by_key` 导出无调用方。
5. **`field_compute_status` 可读性探针整载 payload**（field_compute_tools.py:489-492 `session_data_manager.get`）——大产物时为判可读而全量加载，建议轻量存在性检查。
6. **watchdog 轮询抖动 vs 测试时长贴边**：`test_durable_cancel_stops_body_at_checkpoint` body ~2s，轮询 2.5s×(0.8-1.2) 抖动下界 2.0s —— 有低概率 flake（本机两轮均过）。
7. **TEST_EVIDENCE.md 数字标注歧义**：「tests/jobs tests/delivery # 288 + 57」实际是 jobs=247（288=41+247 为 data_import+jobs 合计口径），不影响结论但建议更正。
8. **`FieldComputeReceiptResponse = Dict[str, Any]`** 使 background 句柄形状不进 OpenAPI 契约（沿用既有宽松定义，加性无害，记录在案）。

---

## 语义诚实性核查（任务书 P1 项）

- **at-least-once / effectively-once**：措辞与实现基本相符（认领互斥 + 收据幂等 + fenced 终态；ACCEPTANCE「不宣称分布式 ACID」的边界声明如实）。但 B1 窗口内收据可丢 → 重复发布，A12 的 ✅ 在该窗口不成立——修复 B1 后该声明才完全站得住。
- **取消矩阵**（边界前/提交中/完成后）：实现与 ACCEPTANCE 一致——claim 前取消直落 cancelled（runner.py:232-237）；成员发布前 `ensure_not_cancelled`（service.py:523-526）+ 行/批间既有检查点；完成后 completed 不可迁 + 批次面 NOT_COMMITTING 409（test_a09 实测）；迟到完成收敛 cancelled（store §14 + test_a07_a08 实测 200k 行取消）。
- **retained 披露**：`batch_outcome_summary` retained_refs/retained_note 真实（test_a13 断言 retained_refs 含已发布产物）。
- **进度真值**：import 按成员计数、compute 按已计算行/已知总量，未知总量不伪造百分比（JobProgress clamped；无 100% 假写）。
- **同步伪装/静默截断**：未发现。ENOSPC→STAGING_WRITE_FAILED(507)+job failed（test_disk_exhaustion 实测）；ROW_BUDGET_EXCEEDED typed 拒绝；300k 行 input=output=300k（计数诚实）。
- **「Pi 不长时间占用」**：默认 20s 有界等待成立，但 m2 的无上界参数是残留缺口。

## 测试有效性核查（mock 边界）

- 故障注入（合理）：`staging.save_state` ENOSPC 注入（test_scale）、`evaluate_rows` 慢化（取消桥测试）、状态文件人为回退 COMMITTING（A11）、kill -9 + raw SQL stale 注入（集成 S4）——注入点都在边界、不绕过被测路径。
- 绕过/虚（问题）：m1 的三处死断言；M1 的新工具零覆盖。`test_cancel_via_durable_bridge_from_other_context` 名为"other context"实为同进程另一入口（durable 桥为真实 DB 轮询路径，语义成立，但跨进程面由集成 S5 补——集成脚本我已逐行审阅为两个真实 uvicorn + 真实 HTTP）。
- conftest：真实 SQLite（sync+async 双引擎）+ temp DATA_DIR，未 mock 被测语义；两个 `DataImportService()` 实例模拟副本的共享面（DB+文件）与双进程拓扑一致，且关键结论另有真实双进程复现背书。

## 安全/权限核查（P2 项）

- job 归属：`/tasks/jobs/*` 走既有 owner 谓词（get_for_owner → JobNotFound 404）；import 面经 `staging.assert_owner`（不匹配统一 NOT_FOUND，不泄露存在性）；`import_commit_status` 经批次归属门禁后读 job；`field_compute_status` 按 session_id 过滤。未发现越权面。
- 脱敏：result_summary 经 `redaction.safe_result`（scrub+限长）；JobView error 单行 500 字符兜底；member/page 投影有界（≤50/≤21、单行 error、无载荷/坐标/拒绝行原文）——新增 `member_status` 投影遵守同一约定。Pi 工具错误详情 `str(exc)[:200]` 既有风格。

## 共享文件克制性

- `jobs/lifecycle.py` +3（JobKind 两值，DB 无 CHECK 纯加性）、`jobs/views.py` +38（仿 page_status 的投影）——最小加性，合格。
- 未触碰：workflow_runtime、data_delivery 本体、task_queue/Celery include、vendor/、governor、Lakehouse/GC（`git diff --name-only` 核对）。INTERFACES.md 的「不改清单」与实际一致。
- 前端：类型 + 一个渲染块 + 两条 i18n 键，缺省不渲染，克制。

## 已验证 / 未验证

**已验证（自行执行或逐行审计）**
- 全量 diff（6 commits，含评审中途新增的 d72225bce；该 commit 的 reused-active 补启动语义已并入审查——认领互斥下不双跑，逻辑成立）。
- 上表全部命令的复跑；BASE 侧误复位独立复现；TOCTOU 覆写复现；OpenAPI 失败的 BASE 归因。
- store/lifecycle 迁移表逐条核对：pending|queued→running 认领原子性、running→cancelled 需两段（两段迁移代码正确）、mark_succeeded 的 late-success→cancelled 收敛、fencing（worker_id+attempt）在 mark_*/confirm/progress/heartbeat 全带。
- runner 生命周期：watchdog 启停、取消后维持心跳、每 tick 独立短会话；registry LRU 有界。
- 幂等键构成与 create_sync 复用/释放语义（含收据态进键的误复用/漏复用分析：计划变更/收据态变化均换键，failed/cancelled 释放旧键建新行——无死胡同复用）。
- `_join_active_job` 收敛分支（completed 取收据 / cancelled 继承 / failed-stale 重提一次 / 超时如实进行中）。
- 消费端不越权、投影脱敏（见上）。

**未验证（如实声明）**
- 真实 Postgres 方言与生产 Redis 拓扑（本机无；与 TEST_EVIDENCE 声明一致，SQL 均为方言无关核心表达式，属可接受残留）。
- Pi 真实子进程端到端（vendor 未构建，master 既有状态；工具层经真实 dispatcher 已覆盖提交面）。
- 浏览器/前端渲染与 Playwright（前端改动为类型+渲染块；tsc 未由我复跑）。
- compute 双副本同 fingerprint 不同 plan_id 并发执行的产物 ref 去重细节（`_exec_lock` 进程内串行保持，跨副本由认领互斥覆盖同键；异 plan_id 同 fingerprint 跨副本并发属 BASE 已有行为放大，未深挖 store.publish 的 ref 决定性）。
- k8s/HPA 真实多副本部署下的长程行为（以双 uvicorn 集成为代理证据）。

---

# 修复记录（实现方按 REVIEW.md 逐项处理，2026-11-12）

| 项 | 修复 | 验证 |
|---|---|---|
| B1 (P0) | `_load_owned` 归一化/补记写盘前**重读+rev 复核**：期间有人写盘（runner 终态落账）→ 放弃覆写返回最新事实（service.py `_load_owned`）；归一化写改 to_thread（顺带 m3） | 新增确定性回归 test_b1_slow_reader_never_clobbers_terminal_state（冻结读→完成→不覆写→重提 duplicate） |
| M1 | 新增 tests/data_operations/test_tools.py（6 用例）：import_commit_status（终态+produced_refs/404 语义）、field_compute_status（completed+可读 ref/无记录）、两提交工具 wait=0 句柄分支（断言 job_id+guidance+不伪造完成）；A22 保持 ✅ | 6/6 通过（经真实 ToolRegistry dispatch） |
| m1 | 三处死断言改为真实断言：A06 伪造身份进度写 rowcount=0 实测；A10 按新内容 hash 查收据为 None；删除死行 | test_runner/test_compute_jobs 通过 |
| m2 | 两工具 wait_max_s 运行时 clamp min(max(x,0),60) | 代码 |
| m3 | 归一化写 to_thread；`_job_active_by_id` 终态集改 lifecycle.is_terminal | 代码+回归 |
| m4 | `async_durable_job` 认领失败显式 discard registry | 代码 |
| m5 | compute 内联 running/pending/queued → 「仍在执行（另一提交/副本认领）请稍后重提或查询任务中心」措辞 | 代码 |
| NOTE1/2/7 | INTERFACES.md 补边界登记（owner scope 入口分歧/delete 残窗）；TEST_EVIDENCE 数字口径更正（jobs=247） | 文档 |
| NOTE4 | 删除死代码 manual_finish 参数、find_active_job_by_key | grep 零残留 |

修复后复跑：tests/data_operations **32/32**（含 B1/M1 新增 7 用例）+ data_import/jobs/delivery/tools/api-compat **395 通过**；field_compute 76/77（1=BASE 预存）；双进程 repro false_reset=false；双 uvicorn 集成 **5/5**（evidence-integration-report.json 已更新）。
