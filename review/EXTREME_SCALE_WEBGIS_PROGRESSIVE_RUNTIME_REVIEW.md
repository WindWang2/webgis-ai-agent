# Review — Extreme-Scale WebGIS Progressive Rendering Runtime V2

- branch: `perf/extreme-scale-webgis-runtime-v2`（worktree `../webgis-wt-extreme-scale-v2`）
- baseline: `origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（执行窗口两次复核无漂移）
- review 形式：1 名独立对抗性 review subagent（非实现者），逐文件读 diff + 亲跑测试 + 写一次性 repro 测试验证每个怀疑点（跑完即删）
- 处置纪律：P0/P1 必须 `复现 → 红测试 → 修复 → 回归`；本文记录全部 findings 与处置

## Findings 总览

| 级别 | 数量 | 处置 |
|---|---|---|
| P0 | 0 | — |
| P1 | 2 | 全部复现 → 红测试 → 修复 → 回归（commit `e2962c08`） |
| P2 | 7 | 6 修复 1 降级记录（见下） |
| P3 | 6 | 3 顺手修复，3 记录 |

## P1（阻断项，已修复）

### P1-1 gzip 分支 ETag 非确定性 — 304 在生产永不命中
- **证据**：`gzip.compress(body, 6)` 默认嵌入当前时间 → ETag 每秒漂移；reviewer 亲测 1.3s 间隔同 FC 的 ETag 漂移、条件请求返回 200。原 304 测试两次请求落同秒 → 同秒假绿。
- **修复**：`gzip.compress(body, 6, mtime=0)`（mtime 为 keyword-only 参数，与 tile 端点 mtime 纪律同源）。
- **红测试**：`test_gzip_compress_mtime0_is_deterministic_across_time` —— 固定不同 mtime 产生不同字节（锚定 mtime 是唯一时间无关来源）+ `inspect.getsource` 源码契约锁定 `mtime=0`。

### P1-2 worker 协议断裂 — ≥20k 要素源视口刷新生产挂起
- **证据**：主线程要求结果携带 `jobId`，但 `handleViewportComputeRequest` 构造的结果不含该字段 → 结果被静默丢弃；repro 显示 `computeFilterThinAsync` 永不 resolve。原"worker path"测试在脚本 worker 里**手工补 jobId** —— mock 假验证。
- **修复**（四层）：
  1. 协议贯穿：`ViewportComputeJob.jobId` 为必填，handler 三条结果路径（成功/unknown-token/异常）全部原样回传；
  2. 兜底：job 硬超时 10s + worker `onerror` → `killWorkerAndFlush`（pending 全部 resolve null，句柄丢弃重建）—— 视口刷新绝不永久挂起；
  3. unknown-token（worker 端 FIFO 逐出）→ 主线程自动重传 init-raw + 同 jobId 补发（幂等，settle 恰一次）；
  4. renderer 对 resolve null 走 `_filterForViewport` 同步兜底（与 master 行为等价，绝不丢裁剪）。
- **红测试**：worker path 测试重写为**驱动真实 handler**（与 viewport.worker.ts 同一条注册路径，零协议字段手补），锁定 jobId 回传、init-raw 一次、unknown-token 重传、worker 成功后写 memo。

## P2（6 修复，1 降级记录）

1. **排队期单飞失效**：dedup 只查 inflight，槽位饱和时同 key 请求排队成第二次网络拉取 → 修复为 inflight + queue 双查；红测试锁定并发饱和形态。
2. **fetchImpl 同步抛错楔死**：契约不保证 async，同步异常逃逸使 inflight/pending 永不清账 → `Promise.resolve(...)` 包裹统一走失败结算；红测试锁定槽释放 + 同 key 可重试。
3. **stale 且无 ETag 永久 cache-hit**：对不发 ETag 的部署数据无限过期 → serve 条件收紧为「新鲜窗内」；红测试用注入时钟锁定 stale 无 etag 必重拉。连带发现默认 cache 未共享调度器时钟（构造顺序修复）。
4. **观测计数生产恒零**：deduped/cacheHits/etag304/fetchOk/fetchFailed/cancelled 六个计数只有 scheduler 私有 stats → 增加结算事件桥（`onEvent`）接入 observability 统一账本。
5. **perf 基准断言错误对象 + 未 commit**：确定性断言原先对准未压缩 body，而生产 ETag 是 gzip 字节 → 改为对准 `gzip(mtime=0)` 真实路径；文件已入库。
6. **恢复链 unhandled rejection 风险 + 非法哨兵 bbox**：`requestRefFC(...).then(...)` 无 catch → 补 `.catch` 兜底；`bounds ?? [0,0,-1,-1]` 非法哨兵 → `PlanViewport.bounds` 改为可选（缺省 = 保守视口内）。
7. **【降级记录】宣称能力未全部接线**：pinRef / cancelRefSession 显式 API / deferOffViewport 生产路径未消费。处置：注释与文档全部降级为「预留 API，本期未接线」（ref-service/cache/scheduler 三处头注 + map-state-restore deferred 死分支注释），不虚报能力。取消语义本身经 per-request signal（会话切换/卸载）已生效并有测试锁定；deferred 保持不可达 = master 全量恢复语义不变（见 P3-② 结论）。

## P3（3 修复，3 记录）

- **修复**：①renderer fallback 路径双重 setData（applySourcePatch 内部已整包写入，renderer 只记账返回）；②unpin 即逐出边缘（`evictLocked(key)` 豁免刚 unpin 条目）；③worker 同视口 memo 在成功结算时写入（原只在同步回退分支写）。
- **记录**：
  - ③指定怀疑点结论：restore 未传 `deferOffViewport` → deferred 分支不可达，恢复语义与 master 一致不丢层；zoom 启发名义 bounds 只影响排序。**已上膛的枪**：未来启用 defer 必须配「可见性变化补拉」，已注释锁死前提。
  - ref-source-resolver 未迁移（双缓存并存）：其自带 24 条 LRU + 墓碑 + 会话守卫语义完整，迁移收益（ETag/预算）不抵回归风险；两边同引用共享无大内存放大。留作下一轮统一。
  - kill-switch：Phase 0 文档曾拟 `WEBGIS_EXTREME_SCALE_RUNTIME` 默认关。实际未实现环境开关 —— 回滚 = revert 本分支（无数据迁移/schema 破坏/接口删除）。决策变更记录于 DECISIONS.md。

## 误报倾向（核查后不成立）

- 「transport onResponse 破坏 401 刷新」：additive、可选、每 attempt 一次、只读 header，不 consume body、不影响重试判定 —— 无问题。
- 「304 跨会话泄漏」：ETag 内容寻址 + 缓存键 session-scoped；304 无 body，内容不同必 200 —— 无泄漏面。
- 「调度器 waiter 竞态窗口」：dedup 分支的 pending 查询在单线程 JS 下与 settle 不交错（防御性代码，无 bug）。

## 与最新 master / open PR 的交叉

- 执行窗口 open PR：#1335/#1336/#1351–#1356。本分支触碰文件与 #1356（cartography/multiscale-scene）重叠：`renderer.ts`、`layer.py`、`map-kit` 目录。#1356 对 renderer.ts 的改动集中在 renderer 主体/导出面，本分支改动集中在 `addGeoJsonSource` 的 diff 块与 `refreshGeoJsonSourcesByViewport` 内环 —— 同文件不同函数，rebase 可解（以其版本为基准备语义合并）；layer.py 为 append 型冲突（ETag 逻辑 + `_etag_matches` 纯新增函数体改动）。**若 #1356 先合入，需一次 rebase 消化语义冲突，无需 integration PR。**
- #1353（cockpit）触碰 `workbenchSlice`/cockpit 组件，与本分支零交集。

## 关键验证（连续两遍一致）

- 前端（接线面全宽）：`vitest run lib/data-plane lib/map-kit lib/hooks/use-sse-stream lib/session lib/store lib/mapspec-runtime lib/api` → **两遍均 99 files / 1114 tests passed**
- 后端（数据面相邻）：`pytest tests/test_layer_api.py tests/test_layer_fc_etag.py tests/test_layer_feature_endpoint.py tests/test_layer_fetch_abort.py tests/perf/test_data_plane_fc_budget.py tests/perf/test_mvt_cache_pressure_benchmark.py -q --no-cov` → **两遍均 36 passed**
- `tsc --noEmit` ✓；`ruff check`（touched files）✓；`git diff --check origin/master...HEAD` ✓

## 结论

无未处理 P0/P1。P1×2 已按红测试→修复→回归闭环；P2 除一项诚实降级记录外全部修复。分支可进入 review/merge 流程（**Local evidence only; no online CI wait; do not auto-merge.**）
