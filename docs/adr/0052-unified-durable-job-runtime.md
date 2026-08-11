# ADR-0052：统一 Durable Job 运行时与取消生命周期

- 状态：Accepted
- 日期：2026-08-12
- 关联：ADR-0013（工具 dispatch 收敛）、ADR-0024（ToolExecutionPipeline 深模块）、
  审计 S33/S34（任务 API 跨租户）、SEC-08（匿名会话 owner_token）

## 背景：三套互不相通的任务状态

改动前系统里同时存在三份「任务状态」，语义与生命周期都不一致：

```
TaskTracker._tasks          进程内 dict     Agent turn 的步骤热态，重启即失
AnalysisTask (analysis_tasks) PostgreSQL 表  有完整状态机与进度列，但零生产调用方
TaskQueueService._task_owners 进程内 class dict  Celery 任务归属的唯一事实源
```

由此产生的具体缺陷（全部在代码里核实过，不是推测）：

1. **取消只是 UI 状态。** `TaskTracker.cancel()` 只翻一个 bool，chat 引擎在「每轮之间」
   和「每个工具完成之后」轮询它。正在跑的 NDVI 或缓冲区分析会一路算完（30–60s）
   才停 —— CPU、worker、内存都没有被释放。原 docstring 自己也承认了这点。
2. **API 重启丢任务归属。** `_task_owners` 是进程内 dict：API 一重启，合法用户查
   自己的 Celery 任务也是 404，更取消不了；多副本部署下「注册在哪个 pod 就只有那个
   pod 认」。
3. **Agent 任务与后台 job 毫无关联。** `task-xxxxxxxx` 与 `celery_task_id` 是两个独立
   命名空间，前端只能显示两条互不相干的条目。
4. **worker 崩溃 = 永久 running。** 没有心跳、没有租约、没有清扫，DB 里的
   `status=running` 永远不会变。
5. **进度契约分裂。** Celery 侧是 `update_state(PROGRESS)`（只存在于 Redis result
   backend），Agent 侧是 `step_start/step_result` 事件，前端拿不到统一百分比。
6. **产物不是原子的。** NDVI 直接写最终路径，取消或崩溃会留下一个看起来正常、其实
   只写了一半的 GeoTIFF。

## 决策

**演进 `AnalysisTask` 为统一 durable job 记录，而不是新建第二套 Job 表。**

它已经具备状态 CHECK、`progress`、`retry_count`、queued/started/completed 时间戳、
`org_id`/`creator_id` 归属与 JSON 载荷列 —— 缺的只是「关联」与「取消/租约」字段。
迁移 `0013_unified_durable_job_runtime` 因此是 additive 的：只加列/索引 + 放宽两处
约束，老数据行不需要改写。

### 1. 单一生命周期契约（`app/services/jobs/lifecycle.py`）

```
pending → queued → running → completed
                     ↓  ↘
              cancelling   failed / stale
                     ↓
                cancelled
```

状态名沿用表原有 CHECK 值，只新增 `cancelling`（取消中，非终态）与 `stale`
（worker 失联）。两条最强不变式由显式迁移表保证：

- **`cancelled` 与 `completed` 没有任何后继**（`IMMUTABLE_STATUSES`）。worker 的
  late success 不可能覆盖 cancelled；取消也因此永远不会被 retry。
- **`cancelling` 只能走向 `cancelled`/`failed`**。worker 若在取消期间跑完，结果按
  取消处理并丢弃产物。

`failed`/`stale` 是终态但**可重试** —— 只能经显式新 attempt 回到 `queued`，
`attempt` 递增且保留首次失败的 `error_trace` 作为证据。

### 2. 并发安全靠原子条件更新，不靠 read-modify-write

每次迁移都是

```sql
UPDATE analysis_tasks SET status = :target, ...
 WHERE id = :id AND status IN (<target 的合法前驱>)
```

合法前驱集合由 `lifecycle.sources_for()` 统一提供，规则只定义一次。`rowcount == 0`
说明状态已被别人改走，调用方据此走幂等分支而不是盲写覆盖。这直接消灭了
`cancelled → completed`、重复 finalize、以及迟到进度复活终态三类竞态。

批量 UPDATE 之后的读取一律走**列级 SELECT**，不复用 ORM 身份映射里的对象 ——
`expire_on_commit=False` 加上 `synchronize_session='evaluate'` 会让一次失败的条件
更新在内存里改写掉那一行的属性，基于它做判断会得出错误结论。

### 3. 取消贯穿执行路径（`cancellation.py` + `worker.py`）

```
Task API → 落库 cancel_requested_at（持久事实）
         → CancellationRegistry 点燃本进程 token
              ├─ asyncio 侧：执行引擎 await token.wait()，取消到达即抢占式
              │   cancel 在飞的工具任务（不再等当前工具自然结束）
              └─ 线程/CPU 侧：token 经 contextvar 传入同步 GIS 代码
                  （asyncio.to_thread 会复制 context，几十个工具签名不用改），
                  长循环在 jobs.checkpoint() 处协作退出

跨进程 worker → 后台看门狗线程按 500ms 轮询 DB 并**推**送取消给 token
```

看门狗必须是「推」而不是「拉」：探针如果只在任务体调用 `checkpoint()` 时才读 DB，
一段不可中断的 numpy 调用期间没人会去看 DB，取消依旧要等计算跑完 —— 那就退回到了
「取消只是 UI 状态」。看门狗同时按 30s 刷新 `heartbeat_at`，因为心跳不能依赖进度
上报（进度是节流的，长时间不变的 job 会被误判为 worker 已死）。

不可逆副作用之前用 `ensure_not_cancelled()`（强制读一次 DB）而不是 `checkpoint()`
（只读内存 token），避免「取消已落库但看门狗还没轮询到」的瞬间给已取消的 job 留下
资产记录。

**取消是先协作、后 revoke，hard terminate 只作为有界最后手段** —— `terminate=True`
不再是所有任务的第一取消手段。

### 4. 归属持久化

`_task_owners` 降级为快路径缓存，durable 行才是事实源。归属有三条证明链，任一命中
即可：`creator_id == 已认证 user_id`、`owner_token` 精确匹配（镜像 SEC-08 的匿名
能力令牌）、`session_id ∈ 调用方已证明拥有的会话`。三条都不成立时归属谓词是**恒假**，
绝不退化成无过滤全表扫描。

auth 层的字面量 `"anonymous"` 在写入前归一化为 `NULL` —— 它不是 `users` 表里的一行，
直接写 `creator_id` 会在 PostgreSQL 上违反外键，匿名用户于是完全无法创建 durable job
（SQLite 因默认不校验外键而会掩盖这个问题）。

### 5. Agent Turn → Tool Step → Durable Job

`JobOrigin` 经 contextvar 承载「我是谁派生出来的」。工具在执行期间创建 durable job
时自动带上 session/owner/run/turn/tool_call/agent step 关联，新 job id 回流到该
step，并出现在 `step_result` SSE 的 `background_job_ids` 里。前端因此把后台 GIS job
挂到对应步骤下面，而不是显示两条无关条目。

### 6. 进度契约与写入速率上界

统一为 `{phase, progress, message, current_step, total_steps}`，并**允许
`progress = null` 表示不确定进度** —— 不是所有 job 都能算出真实百分比，假装 99% 然后
卡十分钟比 indeterminate 更糟。节流规则「进度变化 ≥1% 或距上次 ≥500ms」把 10 万次
上报压到约 100 次落库（基准见下）。

### 7. 产物原子提交

```
temporary output → compute success → os.replace → ready
```

失败/取消删除临时文件，已 finalize 的成功产物绝不删除。覆盖 NDVI 输出、
`raster_math` 的窗口写入循环，以及 `DurableJobHandle.artifact()`。

### 8. 重试只在真正可靠时提供

`retry` 必须**真的**把任务重新交给 worker —— 只改状态而不入队会留下一个永不推进的
job，比不提供 retry 更糟。为此在提交时持久化 `dispatch_spec`（`{task, args, kwargs}`）：
`parameters` 是脱敏+截断后的展示摘要，无法用于重跑。`dispatch_spec` 携带敏感键或
超过体积上限时整体丢弃并标记不可用，此时 retry 明确拒绝而不是假装成功。该字段
**永不**通过 API 返回。

## 拒绝的方案

- **新建 `Job` 表。** `AnalysisTask` 已有 70% 需要的列，再建一套会立刻产生第四份
  任务状态 —— 正是本 ADR 要消灭的问题。
- **`reject_on_worker_lost=True`（worker 丢失时重投）。** 重投会重复执行不可逆的 GIS
  操作。改为 `acks_late=True` + 不重投，重复投递由 job 的「认领」语义
  （`pending|queued → running` 条件更新只有一个能成功）挡住，worker 真死掉的 job
  由 stale 清扫收敛。
- **Temporal / Airflow / Kafka / 新微服务。** 目标是让现有 Celery + PostgreSQL 组合
  可靠，不是引入新的调度平台。
- **把所有 GIS 工具 Celery 化。** 短操作直接 await 更快也更简单；只有重操作走
  durable job（阈值由调用方决定）。
- **新 websocket 通道。** SSE 仍是主推送路径；任务中心只在浏览器刷新后用**有界**轮询
  兜底（无活跃 job → 0 请求，tab 隐藏 → 暂停，连续失败 → 停止）。

## 后果

正面：

- 取消真正释放算力（基准：取消后 10000 个 chunk 只执行 51 个，省下 99.49%）。
- API 重启后合法 owner 仍可查询与取消自己的任务；其他用户仍然 404。
- worker 崩溃不再导致永久 running。
- 进度写入速率有硬上界（10 万次上报 → 100 次落库）。
- 巨型结果与凭据不进 task 行；任务中心响应约 478 B/job。

代价与已知限制：

- `analysis_tasks` 多了 17 列（全部 nullable）。
- 每个 running job 多一个看门狗线程与约 2 次/秒的 DB 轮询。
- **stale 检测有延迟**：心跳超时 300s + 清扫周期 60s，所以 worker 崩溃后用户最多
  会看到约 5 分钟的「执行中」。这是有意的保守取值（避免误判活着的慢 job）。
- **不可中断的原子计算无法被打断**：一次巨大的 `np.divide` 内部没有检查点，取消只能
  在它返回后生效。要更细的粒度需要把波段运算改成分块循环，属于后续工作。
- **跨进程真实 worker 未在本地验证**：环境缺少 Redis，Celery 走 eager 模式，
  测试覆盖的是任务体全部代码路径与 durable 状态机，**不覆盖** broker 重投、
  revoke/terminate 与 prefork 隔离。相关测试文件已显式标注这一点。
