# TASK_PLAN — GOAL 03 temporal map products

Oracle（验收，冻结自任务书 3 节）：
1. 版本化 temporal view 契约 + 真实 dataset/ref 连接；每帧时间解释与数据一致、无虚构期数。
2. MapSpec 单份真相；年份变化触发真实数据/样式修正且读到新 observation；user pin 生效。
3. 点事件/行政聚合/≥1 raster(cube) 路径生产化或诚实降级。
4. 地图↔图表↔表格同一时间窗（同 where fingerprint），无静默错配。
5. 时间轴多轮指代、play/pause/step/seek/reload、取消与 stale-frame 不乱序；无数据不伪造图层。
6. 单帧/系列输出复用既有出口，源/时间/分类/图例证据一致。
7. 可重复两遍的本地 fixture E2E、前后资源指标、独立 reviewer 证据、PR 创建成功。

## M0 审计 ✅（BASELINE.md / PR_OVERLAP_MATRIX.md 已立）

## M1 TemporalViewContract.v1 + MapSpec v1.8
- 新 `app/lib/cartography/temporal_view_contract.py`：`MapSpecTemporalConfig`（schema_version="temporal_view.v1"、enabled、layers[]（layer_id+join）、time_field、time_semantics=event|snapshot|valid_time、time_representation=iso_string|date_string|epoch_s|epoch_ms|year_int、timezone(IANA)、granularity、extent[2]（观测值）、frames[]（id/label/start/end/feature_count——仅来自真实数据）、cursor_frame、playback{playing,rate}、compare{enabled,frame_id_b,classification{breaks,scope}}、missing_policy=strict|nearest|skip、quality[]、source_ref、content_revision、user_pinned、render_path、disclosures[]）。
- mapspec_schema.py：KNOWN_VERSIONS+"1.8"、LATEST_VERSION="1.8"、identity upgrader、MapSpecDocument.temporal 字段、SCHEMA_EXPORT_MODELS 导出；ts_projection 再生 types.generated.ts；quality_loop cartographic_projection +"temporal"。
- 测试：契约校验（拒绝伪造 frames/坏时区/坏表示）、1.7→1.8 迁移 identity、指纹含 temporal、旧 spec 无 temporal 不变。

## M2 数据绑定（时间窗口 → 数据真值）
- 新 `app/services/gis_harness/temporal_products/time_filter.py`：时间窗 [start,end) → where AST（值按 time_representation 归一：iso/date 字典序同格式恒真、epoch 数值、year_int 数值）+ MapLibre filter 表达式 + `time_filter_fingerprint`（sha256(ref+revision+field+window+representation)）。
- 帧/契约推导 `frames.py`：用既有 temporal profiler（`app/services/temporal/profiler.py`）对 ref 采样探测时间字段/表示/跨度 → 真实 distinct 时间桶 → frames（feature_count 实数）；单期 → typed fallback `single_period_unsupported`；不规则间隔 → quality flag `irregular_spacing`。
- 3 类展示路径：点事件（MVT/inline 客户端过滤 + count 聚合同 where）、行政聚合（aggregate group_by=区县 + where）、raster cube（lakehouse window 全范围单读 + 客户端切片，x0=y0=0 规避 #1837；超 8M cells → typed 降级）。

## M3 Agent 工具 + Requirement IR + CAS/user-wins
- 新 `app/tools/temporal_view_tools.py`：`temporal_view_bind`（探测+契约+落 MapSpec）、`temporal_view_control`（seek/step/play/pause/rate/interval）、`temporal_view_compare`（两期+shared breaks）、`temporal_view_unbind`、`temporal_view_export`（单帧导出凭证：帧窗口→导出请求参数）。全部经 lifecycle engine CAS（origin=agent）；user_pinned=true 时 control 拒改 cursor（user-wins，回执 correction_hint）。
- intents.py +SetTemporalViewIntent（严格 dataclass）；mutation_handlers +_handle_settemporalview（COW temporal 分支）；registry 注册 codec；REST 复用 mapspec/mutations 端点（零新路由）。
- Requirement IR：TimeSpec + compare_start/compare_end/playback_requested/hold_layers（additive，SceneSpec 样板）；normalize.py 短语（「上一年/最近3个月/对比A与B/暂停/继续播放/只换年份」）映射。
- capability manifest 归类（CI 契约门）。

## M4 前端时间轴与联动
- `frontend/lib/temporal-map/`：controller（帧序列、apply(seq) 乱序拒绝、displayedAt 门控=applied spec 观测后）、frame-cache（key=fingerprint）、prefetch(±1)、abort/route cancel、metrics 计数器。
- `frontend/components/map/temporal-time-controller.tsx`：play/pause/step/seek/slider/rate/区间、当前时间可见、缺帧提示（quality/missing）、ARIA/键盘（复用 lakehouse 播放器范式）、i18n zh/en。
- map-panel.tsx 最小插槽：committed spec temporal.enabled → 挂控制器；时间过滤与 selectionFilters 同模式合成 per-layer filter（reconcile 后应用）。
- catalogue + temporalCommands（temporal_seek/temporal_play/temporal_pause/temporal_rate/temporal_compare/temporal_clear）；用户操作 origin=user 提交（user-mutation 模式）→ user_pinned。
- 图表/表格同窗：viewport-aggregate/paged-table hooks + time where（同 fingerprint）；表格 where 下推服务端。

## M5 两期/多期对照
- compare 契约（M1 已含）：frame_a/frame_b + classification{breaks,scope=shared}（两窗聚合 min/max/quantile 公共分级，服务端一次计算）。
- 前端：固定 breaks 双帧（同层 twin 或 comparison-view）；变化图层 = 既有 `temporal_change` 工具产物（分母零/缺年/未匹配键 → 既有披露 + 本层 disclosures）；趋势 chart = 既有 chart_panel timeseries + temporal_aggregate。
- 图例/统计随帧更新（legend 数据源同 where fingerprint）。

## M6 导出
- 单帧：vector-pdf/pdf 复用（导出请求携带当前帧 where → 后端 compile 时应用时间过滤与时间注记）；PNG 前端导出时以 applied spec 组装（既有 exporter 路径）。
- 系列：atlas time driver（AtlasPolicy.driver=+time、AtlasPageBinding + time_window、每页 filter 编译、manifest + time 证据（窗口/来源指纹/分级规则/frame_count）；帧 QA ≠ 整本 QA 沿用既有 QA 门。

## M7 性能
- frame cache key(time window+source revision+style)；bounded prefetch ±1；out-of-order rejection（seq）；路由切换 AbortController；拖动防抖 + 指纹缓存命中测试（重复拖动 0 新请求断言）；指标：requests/bytes/内存增长（vitest 计数器 + 后端 aggregate 调用计数）。

## M8 E2E fixtures（可重复两遍，确定性）
- F-synth 数据：5 年小学点（GeoJSON ref）、2 期区县面（choropleth ref）、单期拒动 ref、小 raster cube ref。
- E2E-1 播放：bind→frames==5→seek 2024→where AST/MapLibre 过滤/aggregate 全一致→displayed_at=2024→reload 恢复。
- E2E-2 对照：compare 2023/2026→breaks 固定→两帧统计口径一致→变化层披露。
- E2E-3 拒动：单期 bind→typed fallback，无 temporal 写入，无时间轴。
- 快帧取消/缺期/日期边界（[start,end) 语义）/被撤销 ref（404 typed）。

## M9 文档/Review/PR
- F29+ 本地验收清单（对齐 G05 task acceptance 最新编号）；EVIDENCE/TEST_MATRIX 更新；独立 subagent review（P0/P1/P2 + file:line）→ fix → re-review；push + gh pr create（不合并）。

资源纪律：-j2；pytest -q 聚焦路径；pnpm vitest run <file> --maxWorkers=2；不跑线上 CI。
