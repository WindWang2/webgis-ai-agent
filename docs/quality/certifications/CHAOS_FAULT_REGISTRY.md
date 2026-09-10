# Chaos 故障注册表（ADR-0104 Wave 7+8）

> 本文件是**派生物**：唯一事实源 = `tests/fixtures/chaos.py` 的 `FAULTS`
> 注册表。再生：`python scripts/gen_chaos_registry.py`；字节一致性闸：
> `tests/quality/test_chaos_foundation.py::test_chaos_fault_registry_document_current_and_complete`。

生产关闭是**结构性的**：注册表只存在于 `tests/` 包内，`app/` 零引用
（`test_no_app_source_references_chaos_module` 全量扫描锁定）。确定性纪律：
只接受显式次数/序列的 schedule，无 RNG、无同步用途的 sleep；每次注入
记录 journal（armed → fired → disarmed），测试必须断言「故障真的开火」。

| Fault ID | 子系统 | 描述 | 注入方式（接缝） | 期望系统行为 | 注入点（审计 05 证据） |
|---|---|---|---|---|---|
| `CACHE_CAP_SHRINK` | CACHE | 制品缓存字节上限骤减，LRU 驱逐路径在压力下运行 | unittest.mock.patch 补丁 MAX_ARTIFACT_BYTES 常量（补丁超时/容量常量接缝） | 最老条目（.tif+.meta 成对）被驱逐，最新条目存活，缓存总字节回到上限内 | `app/lib/artifact_cache.py:191 _evict_if_needed（审计 05 #1）` |
| `CACHE_COPY_FAIL` | CACHE | publish 的原子改名 os.replace 中途失败（ENOSPC） | app.lib.artifact_cache.os 替换为仅 replace 抛 OSError 的透传代理（monkeypatch 接缝） | tmp 临时件被清理、无半截发布可见（get_artifact=miss）、调用方拿到直出 fallback 路径 | `app/lib/artifact_cache.py:165 publish_artifact（审计 05 #2）` |
| `CACHE_META_WRITE_FAIL` | CACHE | .meta sidecar 在 .tif 发布后写失败 | _meta_path 补丁指向必然打不开的黑洞目录（monkeypatch 接缝） | publish 不崩溃照常返回；读取侧诚实 miss（.meta 缺失不可见，孤儿清扫兜底） | `app/lib/artifact_cache.py:176-185 publish meta 写入（审计 05 #3）` |
| `CACHE_META_CORRUPT` | CACHE | .meta sidecar 内容损坏（坏 JSON 字节） | 直接覆写 meta 文件为垃圾字节（字节级损坏接缝） | get_artifact 诚实 miss（类型化吞掉 JSONDecodeError），绝不崩溃、绝不返回无 meta 的 .tif | `app/lib/artifact_cache.py:123-126 get_artifact（审计 05 #3）` |
| `LOCK_RENEW_ERROR` | LOCK | 分布式锁续约 eval 连续抛连接异常（Redis 抖动/持续错误） | ChaosLockClient.renew_fail_times 显式次数 + 补丁 _RENEW_INTERVAL_S 常量 | 瞬时抖动（预算内）被容忍、锁不丢；连续失败覆盖整个 TTL 窗口后 lost=True 且停止续约（fail_on_lost 调用方在退出时拿到 LockLostError） | `app/services/distributed_lock.py:239 _renew_loop except（审计 05 #4）` |
| `LOCK_ACQUIRE_DEGRADE` | LOCK | 锁获取相位 Redis SET NX 抛连接错误 | ChaosLockClient.acquire_error（确定性假 Redis 接缝） | fail_on_degraded=False 时透明降级 in-process（mode=degraded 可观测）；=True 时抛 LockDegradedError | `app/services/distributed_lock.py:185-197 __aenter__（审计 05 #5）` |
| `REGISTRY_DEGRADED_EXCLUSION_DROP` | REGISTRY | artifact_registry 关键段拿到的 session lock 因 Redis 不可达降级 —— 跨 Pod 互斥被静默放弃 | 补丁 SessionLockRegistry._get_client 返回 SET 必失败的假客户端 + USE_REDIS=true save/restore | 操作照常成功（fail_on_degraded=False 的文档化取舍）；降级可观测（lock.mode=='degraded'）；无崩溃 | `app/services/artifact_registry.py:444,552,592 fail_on_degraded=False（审计 05 #5）` |
| `INGEST_DUP_RACE` | INGEST | 两路并发摄入同时通过 dedup 检查（check-then-act 竞态窗） | Event 屏障包装 _find_duplicate：wait_for 个检查全部通过后才放行注册（asyncio 接缝） | 现状钉死：竞态窗内两个 ref 都注册成功（重复 ref 文档化为残余风险，见审计 #6） | `app/services/data_ingest/pipeline.py:157-166 dedup（审计 05 #6）` |
| `INGEST_REGISTER_FAIL` | INGEST | 并发摄入中一路的制品注册被拒（register 返回 None） | 计数包装 register_artifact：第 fail_after+1 次起返回 None（monkeypatch 接缝） | 失败方 ref 被补偿删除（无孤儿）、error_code=REGISTER_FAILED_ROLLED_BACK、成功方账本恰好一条 | `app/services/data_ingest/pipeline.py:204-227 register+rollback（审计 05 #6）` |
| `CANCEL_MID_WRITE` | CANCEL | 制品分块写入中途到达取消信号 | CURRENT_TOKEN contextvar 绑定 CancellationToken，写入循环 chunk 边界触发 cancel()（生产 checkpoint 接缝） | 下一个 checkpoint 抛 OperationCancelled；无部分制品被晋升（缓存 miss、临时件被丢弃） | `app/lib/cancellation.py:163 checkpoint + cancellable（审计 05 §3.7）` |
| `LLM_TIMEOUT` | LLM | LLM 读取相位超时（provider 挂起不响应） | httpx.MockTransport handler 抛 ReadTimeout（transport 接缝，test_provider_contract_v2 同款） | 诚实抛错（不假成功）；读取超时不在连接相位重试白名单内，单次尝试即失败 | `app/services/chat/llm_client.py:358-407 重试边界（审计 05 §3.1）` |
| `LLM_MALFORMED_STREAM` | LLM | 流式响应在 finish_reason/[DONE] 之前被截断 | MockTransport 返回只有内容帧的截断 SSE（transport 接缝） | ProviderStreamTruncated 显式抛出，绝不把断流包装成 done 帧（防假成功） | `app/services/chat/llm_client.py:601-609 截断判定（审计 05 §3.1）` |
| `STORAGE_TRANSIENT_FAIL` | STORAGE | 制品账本存储后端瞬时写失败（Redis/磁盘抖动）后恢复 | 计数包装 session_data_manager.store/overwrite：前 fail_times 次抛 OSError（monkeypatch 接缝） | 调用方拿到类型化异常（不静默丢数据）；账本 alias 不前进（无半截提交）；恢复后重试成功 | `app/services/artifact_registry.py:227-240 _save_records（Quality V2 W9）` |
| `JOBS_WORKER_LOSS` | JOBS | worker 心跳丢失：running/cancelling job 心跳超时后无人续约 | 包装 DurableJobStore.find_stale 强制 stale_after_s=0（monkeypatch 接缝；等价心跳停更），随后 sweep_stale 真实运行 | running → failed(stale)（可重试终态）；cancelling → cancelled（不给被取消任务开重跑后门）；重试转移 failed → queued 合法 | `app/services/jobs/store.py:805-937 stale 清扫（Quality V3 W13）` |
| `CANCEL_STORM` | CANCEL | 取消风暴：N 个并发请求同时取消同一 token | asyncio.Barrier 编排 N 路并发 cancel()（纯编排注入；接缝 = CancellationToken.cancel 的幂等/CAS 语义） | 恰好一次返回 True（其余 False）；cancelled 状态与 cancelled_at 单调稳定；无异常泄漏 | `app/lib/cancellation.py:71 cancel（Quality V3 W13）` |
| `JOBS_STALE_REVISION_CAS` | JOBS | stale revision CAS：并发状态转移中失败方携带过期 expected | 确定性交错：胜者 transition 提交后，败者携带已作废的 expected 再 transition（纯编排注入） | 恰好一路成功；败者返回 False（诚实拒绝，不覆盖、不部分写）；终态 == 胜者目标 | `app/services/jobs/store.py:384-445 transition CAS（Quality V3 W13）` |
| `DB_TRANSIENT_SEQUENCE` | DB | DB 瞬时故障序列：job 创建路径前 N 次连接级失败 | 包装 DurableJobStore.create，前 fail_times 次抛 sqlalchemy OperationalError（monkeypatch 接缝） | 类型化异常透传调用方（不静默吞）；无半截行落库；恢复后重试创建成功 | `app/services/jobs/store.py:188-255 create（Quality V3 W13）` |

共 17 个注册故障点；子系统：`CACHE`、`CANCEL`、`DB`、`INGEST`、`JOBS`、`LLM`、`LOCK`、`REGISTRY`、`STORAGE`。
