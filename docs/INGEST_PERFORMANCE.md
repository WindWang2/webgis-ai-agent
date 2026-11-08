# INGEST_PERFORMANCE — 摄入管线本地基准（G03 GD-10 / ADR-0286）

> 状态：随 zcode/goal-geodata-readiness-fabric-20261107 PR
> 基准测试：`tests/perf/test_g03_ingest_bounded.py`（断言机器无关不变量；
> 下表墙钟/内存为**本地实测记录**，非断言阈值）
> 环境：单机 Linux（CachyOS x64），Python 3.13.17，进程内 session store
> （无 Redis/外部服务），pytest 串行单 worker。**未验证边界**：无真实
> PostGIS/外网 OGC 联机；多用户并发下的绝对数字未测。

## 摄入管线（IngestPipeline，Point FC，3 属性字段，EPSG:4326 声明）

| 档位 | 载荷 | 端到端墙钟 | 峰值内存 | 峰值/载荷 | 备注 |
|---|---|---|---|---|---|
| 50 要素 | 7 KB | 108 ms | 99 KB | 14×（固定开销主导） | warmup 后 |
| 5,000 | 747 KB | ~4.2 s | 4.3 MB | 5.7× | |
| 100,000 | 15 MB | ~68 s | 84 MB | 5.6× | 缩放 100k/5k = 16.1×（线性 20× 内） |
| 1,000,000（heavy） | 152 MB | 280 s（store 前） | 272 MB | **1.79×** | store=**REJECTED**（见下） |

- 不变量断言（测试锁定）：`峰值 ≤ 8×载荷 + 512KB`；`100k/5k 耗时比 < 60`。
- 1M 档步级：guard 2.4 s（O(features) 单遍）、profile 0.23 s（有界采样窗）、
  其余为 152MB canonical JSON 序列化 + sha256 指纹（I/O 主导）。
- **1M 载荷被 session store 正确拒收**（`SESSION_STORE_MAX_BYTES` 默认 50MB
  → `STORE_FAILED: byte budget`，session_data.py #912）——这是有界资源
  纪律的生产证据，不是失败：大文件走声明式 local_file/fabric 物化车道，
  不整包进会话存储。拒收发生在 store 步之前，无 ref 铸造、无回滚对象。

## 恶意输入守卫开销（GD-09）

- 守卫为 O(features) 单遍 + 深度帽剪枝：1M 要素 guard 仅 2.4 s（占端到端
  <1%）；50k 要素守卫+摄入全链 < 60 s 断言（实测远低）。

## 远程 GeoJSON URL adapter（geojson_url）

- probe + describe + preview + query 四面访问 = **1 次网络取回**（30 s
  进程内短缓存；`requested_urls == 1` 测试锁定）。
- 下载预算：字节帽（生产默认 ≥16MiB，`limits.max_response_bytes`）+
  要素帽（≥1000，截断如实披露 `truncated`）；429/5xx → typed
  `SourceUnreachableError`；假 Content-Type/非 FC 体 → typed
  `SourceBadResponseError`（测试锁定）。

## 协作取消（GD-06）

- 阶段边界检查（detect 后/validate 后/profile 后/store 后）；store 后取消
  先回滚孤儿 ref（`rollback-on-cancel` 步入 steps_completed）；
- 取消→重试同载荷：指纹 dedup 幂等（会话产物台账恰 1 条，测试锁定）。

## 观测口径（GD-10）

`IngestResult.step_timings_ms`（步级毫秒）+ `elapsed_ms`（端到端）随每次
摄入如实返回（`to_dict()` 键：`step_timings_ms`/`elapsed_ms`）；不虚构
速度——上表数字即本机原始输出。
