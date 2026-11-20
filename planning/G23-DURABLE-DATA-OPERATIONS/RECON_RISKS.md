# G23 RECON — 技术缺口与部署风险（调查员报告）

## R1. jobs 无 per-member checkpoint 结构（代码证实）
analysis_tasks 只有 progress/message/result_summary/result_ref。先例=atlas pages 塞 result_summary+views 投影（views.py:92-123）。import 成员级事实源已在 batch.json 收据——job 只承载批次状态+指针（G18 result_ref 同款）。

## R2. 两套幂等域错位（代码证实）
jobs 键=(task_type, session/owner, canonical params) 1h TTL；import=(member_key, plan_hash) 无 TTL；compute=(fingerprint, input_ref, content_hash)。G23 显式两层幂等：job 键纳入 plan/compute fingerprint；成员/收据级复用由既有收据层负责。

## R3. fencing/lease 现状（代码证实）
- jobs 有 worker_id+attempt 条件更新 fencing（#1398）。
- 无显式 lease 过期列：租约=heartbeat(300s)+stale sweep；无自动续跑，retry 需人工或 dispatch_spec。crash 后 ≤300s running→stale。
- import 现状 fencing=`_committing_ids`（进程内）→ R5 误判源。

## R4. 长事务/检查点（代码证实）
- 单成员 commit 不可分段；检查点在解释行循环（每 1000 行）+成员间；IngestPipeline 有自己 cancel_event 阶段边界。
- `ensure_not_cancelled()`（强制 DB 读）应映射到"收据落账/ingest store"不可逆点（现只查本进程 event，commit.py:115-119）。
- compute 批边界检查直接映射 checkpoint。

## R5. 生产多副本（代码证实——重要）
- k8s：API Deployment `replicas: 2`（deploy/k8s/02-api-deployment.yaml:21）+ HPA min2/max10（07-hpa.yaml:22-23）；粘性=ClientIP+ingress cookie（自注 WEAK）。Celery replicas 1 --concurrency=2（03-celery-deployment.yaml:12,49）；api+celery 共享 RWX PVC DATA_DIR。
- docker-compose.prod：单 api + 单 celery-worker 共享卷。
- **含义：生产 API ≥2 副本，进程内锁/事件/dict 取消在副本间不互通——误判路径真实存在。粘性路由不可作正确性依据。**

## R6. 跨进程取消缺口（代码证实）
- Celery 路径 OK（watchdog 2.5s DB 轮询）。
- **G18 进程内 runner 不轮询 DB 取消**——跨副本取消靠完成边界 late-success 收敛，长导入长时间不响应。G23 runner 必须加 DB 取消轮询。
- import 现状跨副本取消：副作用已发生但显示 cancelled。

## R7. SQLite/Postgres 双方言（代码证实）
- 默认 sqlite:///./data/webgis.db；生产强制 PG（config.py:526-538）。
- analysis_tasks 主键 BigInteger+variant（db_model.py:110-113）为 SQLite 自增——**G23 不加列则无迁移负担**；若加必须双方言。
- 原子条件更新 rowcount 两方言均成立；SQLite 并发写靠单写者+WAL。
- 业务事实源在文件系统不受方言影响；状态搬进 analysis_tasks 后继承双方言约束。

## R8. Redis 用途（代码证实）
①Celery broker/backend；②SessionStore（ref:upload-*/ref:table-*/ref:fc-* 跨副本可读正依赖此）；③distributed_lock；④cache_broadcast 等；⑤celery_origin 投递池。
本地/测试=无 Redis 降级拓扑（eager+内存 store）——**G23 durable 语义必须在该模式成立**（G18 测试范式）。取消/进度/心跳不经过 Redis。

## R9. 其他缺口
1. JobKind 词表加值（无 DB CHECK，加性）。
2. dispatch_spec 取舍：in-process runner → max_retries=0+重提语义（G18 注释）。
3. 进度粒度：JobProgress 有 phase/current_step/total_steps 可承载两级进度。
4. 同步面兼容：Pi `background=False` 期望即时收据——需保留同步等待面或轮询面。
5. staging 48h TTL 惰性清理发生在任意副本读路径——job 化后过期语义保留。
6. 创建期（大 zip 解压分钟级）是否 job 化：本期不做（保持创建请求内），记录边界。
7. **batch.json 跨进程并发写**：job 化后若 API 侧仍可写（save_plan/preview/cancel/delete），与 runner 有并发写窗口——需"活跃 job 持有批次写权"：存在活跃 job 时非 job 写路径一律 409/拒绝；`rev` 字段已在但未校验，本期至少以活跃 job 门禁替代 CAS。
