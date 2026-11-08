# GIS Harness Reliability Benchmark — G07 M6 性能切片报告

日期：2026-11-08 · 子代理 C · worktree `webgis-wt-harness-reliability-lab-20261107`

## 结论摘要

| 切片 | before (p50) | after (p50) | 判定 |
|---|---|---|---|
| resume `_save()` 每场景全量重写（140 场景 suite） | 468.5 ms | 265.1 ms | **改善 ~1.77×**（真实运行，同一脚本内 flush_interval=1 vs 16） |
| `context_bytes_proxy` UTF-8 口径修正 | str() 0.154 ms/调用 | canonical_json 0.262 ms/调用 | **未改善速度（如预期）**：这是计量正确性修正，不是优化 |
| suite wall 缩放（warm, seed=11） | small 256.4 / medium 330.2 / large 598.8 ms | 同左（基线） | 基线数据，无回归声明 |
| 冷态首跑（capability graph 一次性投影） | 4504 ms（2 场景 warm-up） | — | 一次性成本，不进 before/after；drift.py 记忆化已生效 |

## 1. context_bytes_proxy 计量修正

**问题**：`app/lib/harness/replay/bench.py` 的 `context_bytes_proxy` 用
`sum(len(str(op.arguments)))` 计的是 Python 字符数（repr），不是 UTF-8 字节。
非 ASCII 参数下系统性偏低（实例：`{"name": "中文图层", "query": "éèê",
"tags": ["点", "线"]}` → str 字符 52 vs UTF-8 JSON 字节 61）。

**修复**：改用与 digest 同一 `canonical_json(...).encode("utf-8")` 计字节
（`bench.py` `run_one` record_perf 分支）。这是 #1998 UTF-8 口径在 bench
计量面的对齐。

**防回归测试**（不与实现共享公式）：
- `tests/harness_replay/test_replay_bench_cli.py::TestContextBytesUtf8` — 非 ASCII
  固定样例硬编码期望 61 字节 + ASCII 样例硬编码 8 字节。
- `test_perf_profile_scales_bytes_but_keeps_semantics` 的缩放断言改用测试内
  独立的 compact-JSON UTF-8 参照公式（旧版本用 `len(str(...))` 自证）。
- `tests/benchmarks/test_harness_reliability_bench.py::TestContextProxyUtf8`。

**性能影响（如实记录）**：纯计量函数微基准 str 0.154 ms vs canonical_json
0.262 ms（large profile、~131 KB payload/场景）。**未改善、也未劣化到可感知
程度**（suite 级差异 < 0.1 ms/场景，淹没于 run_one 的其他成本）；该修正
的价值在计量正确性。ASCII filler 下 str() 因 `", "` 分隔符反而比 compact
JSON 长 13 字节（131310 vs 131297），进一步说明 str 口径从来不是 bytes。

## 2. resume `_save()` flush 降频

**问题**：`run_suite` 的 resume 状态文件旧实现**每完成一个场景**就
`json.dumps` 全量 entries dict —— O(N²) 序列化。此前微基准（字符口径下的
条目形状）估算 140 场景累计 ~0.17 s、400 场景 ~1.5 s。

**修复**（最小兼容，JSON 形状与跳过语义不变）：
- 新增 `flush_interval` 参数（默认 16，必须 ≥ 1，否则 `ValueError`）。
- 每 `flush_interval` 个完成场景写一次 + suite 收尾强制 flush（无新条目则
  跳过，避免空转重写）。
- 中断代价：最多重算最近 `flush_interval - 1` 条已完成场景；重算结果在
  同 seed 下确定性等价，resume 跳过语义不受影响。

**真实 before/after**（`scripts/perf/bench_harness_reliability.py`，全量
corpus 140 场景、seed=11、5 次重复、顺序执行）：
- before（`flush_interval=1` = 旧每场景重写语义的精确等价）：
  wall p50 **468.5 ms** / p95 498.0 ms
- after（`flush_interval=16`）：wall p50 **265.1 ms** / p95 304.3 ms
- 参照：同 suite 无 resume 路径 p50 256.4 ms → after 已接近无 resume 下限
  （残余 ≈ 9 次 flush 的序列化 + 写盘）。

**测试**：`TestResumeFlush`（test_replay_bench_cli.py：flush 次数、收尾完整性、
恢复语义、参数校验）+ `TestResumeFlushSemantics`（focused perf 文件：interval=1
等价旧写次数、interval=16 只写一次且状态完整、宽松不变慢冒烟）。

## 3. 测量方法与环境

命令（可复现）：
```
python scripts/perf/bench_harness_reliability.py          # 全量 140 场景 × 5 重复
BENCH_LIMIT=30 BENCH_REPEATS=3 python scripts/perf/bench_harness_reliability.py  # 冒烟
pytest tests/benchmarks/test_harness_reliability_bench.py -m perf -q
```
原始数据：`docs/quality/evidence/harness_reliability_bench_report.json`
（每次运行覆盖；含每次 run 的 wall/CPU/maxRSS/payload bytes/entry 数与
environment 块）。`unmeasured` 另行标明 artifact bytes、生产 SSE emitted events、
渲染 frames 与 Redis mutation scaling 均为 `not_evaluated`，不把 payload bytes
改名成 artifact bytes，也不虚构浏览器帧数。

环境：CPython 3.13.17 (uv)、x86_64、16 核、并发=1（bench 顺序执行纪律）、
无网络/无 LLM。maxRSS 全程 ~359 MB（capability graph 一次性加载为主，
非 replay 路径）。

## 4. 局限与未测面

- **T2 `replay_mutations` 指纹链（疑似 O(N²)）未验证**：本环境无
  localhost:16379 Redis，`mutation_session_lock` 直接 `LockDegradedError`。
  留待有环境的代理补 Redis-available 条件下的 scaling 测试。
- resume before/after 是**同一进程内**对比（graph/digest 记忆化已 warm）；
  冷态绝对值会高 ~4 s（一次性 graph 投影），已单独披露。
- `flush_interval=1` 与旧实现语义等价（每次写完整 entries dict），但旧代码
  无收尾空转跳过 —— N=140 时两者写次数相同（140），对比成立。
- cProfile 下 deepcopy 占比虚高（per-call 探针开销放大）；隔离实测
  `deepcopy(large-profiled scenario)` 仅 ~0.08 ms/场景，**未作为优化目标**
  （删除会引入 corpus 被占位符原地污染的风险）。
- 未改 `perf/budgets.json` 或任何阈值；本切片无 perf-gate 断言。
