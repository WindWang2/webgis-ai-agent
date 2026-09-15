# AGENT-08 审查备忘录：时空因果推演与动态微观仿真运行时

**分支:** `agent/08-spatial-causal-simulation-runtime`（worktree 隔离，基线 origin/master@3eb2cc6a）
**日期:** 2026-09-15　**ADR:** [0192](../docs/adr/0192-spatial-causal-simulation-runtime.md)　**Spec:** [spatial-simulation-spec](../docs/dev/spatial-simulation-spec.md)

## 1. 交付范围

| 交付物 | 路径 |
|---|---|
| ADR | `docs/adr/0192-spatial-causal-simulation-runtime.md` |
| 技术规范 | `docs/dev/spatial-simulation-spec.md` |
| 测试套件（TDD 先行） | `tests/unit/test_simulation_runtime.py`（57 用例） |
| 仿真子域包 | `app/services/simulation/`（contracts/state/laws/runtime/layers/errors/api/tasks + models/×2） |
| 工具面 | `app/tools/simulation_tools.py`（`run_spatial_simulation`，tier=3/heavy） |
| 平台接线 | `app/services/task_queue.py`（celery include）、`app/tools/__init__.py`（_TOOL_MODULES） |

## 2. 数值稳定性审查（重点项）

**结论：守恒与稳定不是"特性"而是可复核的契约，逐条对应测试。**

### 2.1 水文浸润（hydro_diffusion）
- **守恒恒等式**：空间交换全部为成对通量（一个格元的流出 = 邻元的流入，
  `np` 逐元素同值加减 → 浮点内严格抵消）；源（降雨）/汇（排水）显式记账。
  runtime 每步独立累积期望总量并比对，`test_mass_conservation_with_rain_and_drain`
  断言相对残差 ≤ 1e-9，`test_rain_duration_window_accounting_is_exact` 断言
  雨强窗口净源量与解析值一致到 1e-12。
- **负水深硬保证**：发送方比例限制器 `min(1, 0.45h_i/Σout)` 只缩放发出量，
  不破坏成对守恒，且 `h_new ≥ 0.55·h_old`（`test_no_negative_depth_and_no_divergence`）。
- **CFL 稳定域**：显式图拉普拉斯格式要求 `dt·max_i Σ_j k_ij ≤ 0.9`；
  `validate_stability` 每步重算（k 随水深变化）并在违例时抛
  `SimulationStabilityError`（context 携带 dt/dt_max 证据，
  `test_cfl_guard_rejects_oversized_dt`）。
- **发散防护**：总量 ≤ 初始 + 累计降雨（无生成项），全程有限性由
  `state.validate()` 每步强制。
- **fail-loud**：人为注入"漏水"law（每步 -10% 不记账）→
  `SimulationStateError` + status=failed（`test_conservation_violation_fails_loud`）。

### 2.2 交通潮汐（traffic_propagation）
- **守恒恒等式**：状态变量取排队车辆数；`Σq + exited = injected` 每步严格
  （服务为 min 封顶、转向权重归一化、溢流退回上游，均不产生/消灭车辆；
  `test_vehicle_mass_conservation` ≤ 1e-9）。
- **蔓延机理**：瓶颈注入 → 下游存容节流 → 未放行车辆退回上游边 →
  拥堵指数（q/storage ∈ [0,1]，天然有界）按步长向上游逐边蔓延且次序单调
  （`test_bottleneck_congestion_spreads_upstream`）；瓶颈时间窗关闭后容量
  恢复、排队消散（`test_bottleneck_window_can_expire`，对照永久瓶颈）。
- **有效性包络**：模型无条件数值稳定（min/比例封顶），`validate_stability`
  强制 dt ≤ 0.25·min(最小行程时间, 最小存容服务时间)——时间分辨率不足时
  诚实拒绝而非输出噪声（`test_stability_guard_rejects_unresolvable_dt`）。

## 3. Celery 异步调度审查（重点项）

- **算力隔离落地**：`tasks.py::run_simulation_forecast` 走 geocompute 同款
  durable-job 纪律——生产派发必经 `submit_durable_job`（幂等键、原子认领、
  celery_task_id 回填）；worker 侧 `durable_job()` 上下文内按步上报
  `job.progress(5+90·tick/n)`，`ensure_not_cancelled()` 后
  `finish_job(result=有界摘要, result_ref=产物提货券)`。
- **eager/测试路径**：`job_id=None` 直调显式告警、无持久语义（先例一致）；
  无 Redis 时平台 `task_always_eager` 语义不变。
- **接线守卫**：任务模块登记进 `task_queue.py` include 列表 +
  `celery_app.tasks` 名称在册，双断言（`test_task_module_registered_in_celery_include`）。
- **运行时不感知 broker**：`runtime.py` 零 Celery import；数值核可在
  解释器内裸跑（56 项单测离线通过即证据）。
- **载荷再验证**：Celery 边界入参视为不可信，worker 侧 pydantic 重验
  （`test_task_rejects_invalid_params_with_typed_error`）。

## 4. 时态产物与 MapSpec 兼容

- T0..TN 按 output_stride 切片（含末步），产物契约 `TemporalLayerProduct`
  携带 tick/模拟时刻/提货券/bbox/截断标记。
- **MapSpec 兼容以真实 schema 校验为准**：`build_mapspec_bundle` 产出
  v1.2 文档骨架（逐切片 geojson source + layer，`layout.frames` 时间轴、
  逐帧只点亮当期图层），测试直接 `MapSpecDocument.model_validate(bundle)`
  （`test_mapspec_bundle_validates_against_mapspec_document`）。
- 帧数守卫在**构造期**强制（`SimulationRunConfig` 校验切片数 ≤ 50，
  与 `MAX_SPEC_FRAMES` 同口径对账 + 漂移告警）。
- GeoParquet：复用 `vector_carrier.features_to_arrow + table_to_geoparquet`；
  pyarrow 是平台既有的可选依赖——缺席时**诚实降级**为内存记录
  （`format="memory"` + degraded_note，`test_geoparquet_sink_honest_degradation_without_pyarrow`），
  在场时往返可读（roundtrip 测试，本机无 pyarrow 故 skip）。

## 5. 约定符合性与边界

- geocompute 包式组织、`PlatformError` 域错误层次（稳定 code + 固定
  user_message）、工具面 `std_error_response`（不向 LLM 抛栈）✓。
- **数据面红线**：数值核不 import DB/Redis/Celery/app.tools/config；
  `test_geocompute_boundary` 199 项相关守卫全绿 ✓。
- **零新增环境变量**：规避 `test_env_hygiene` 守卫 ✓。
- 新增依赖：无（ruff 为本地门禁工具，未入 requirements）。
- 与 `what_if_simulate` 的边界在工具描述中显式声明（数值推演 vs 规则速查）。

## 6. 独立审查与修复记录（Review Agent 全量审查）

独立 review 对全部新代码做了对抗性审查（含可复现实证），发现并已**全部修复**：

| 级别 | 发现 | 修复 |
|---|---|---|
| P0 | 水文：排水与出流并发（退水期薄水膜）时 `h_new = h − out − min(h, dt·drain)` 可达负值，"h≥0 硬保证"被击穿 | 排水改为受"流出后余量"封顶：`drained = min(max(h−Σout·s, 0), dt·drain)` → `h_new ≥ rain ≥ 0` 严格成立；ADR/spec 公式同步修正；新增回归 `test_drain_with_outflow_never_negative` |
| P0 | 交通：重力权重归一化的 ulp 残差使 Σallowed 微超 served，分岔路网 + 欠饱和需求（served==q 常态）下产生负队列 → run 以 SimulationStateError 崩溃；原有测试全用线性链（单出边 w≡1 精确）恰好绕过 | Σallowed > served 时等比收回 + 退回量 `max(…, 0)` 双兜底；新增分岔路网回归 `test_branched_network_no_negative_and_conserved` |
| P1 | runtime：`step()` 完全绕过生命周期状态机（PENDING 直调/终态加跑均可执行） | step() 强制 RUNNING 态检查；取消检查先于初始化 |
| P1 | tasks：durable job 取消令牌未接入推演循环（取消后最多 10 万步跑完才停）；进度未按 stride 节流 | `_progress` 内 `job.ensure_not_cancelled()` + 按 output_stride 节流；runtime 将 OperationCancelled 归一为 cancelled 终态（与 job 行对齐） |
| P1 | from_checkpoint 不读 status：终态检查点可复活 | failed/cancelled 拒绝续跑（completed 是合法续跑点——"到达配置视界"后换目标步数续推正是检查点用途） |
| P2 | parse_params 丢弃 pydantic 字段级错误；`_enqueue_simulation` 私有名被跨模块 import；两处弱断言（`raises(Exception)`、OR 短路断言）；示意布局 O(E²)；栅格 bbox 返回全图而非要素范围 | 字段级摘要入 context → correction_hint；更名公共 `enqueue_simulation` 并入 __all__；断言强化；首入边查表 O(1)；bbox 按选中格元实际范围 |

审查确认无问题项：限制器成对守恒索引代数、CFL 边界与 ADR 一致、交通记账严格性、
pydantic frozen/validate_assignment 组合行为、分层红线 import、工具面无栈泄漏、接线守卫。

## 7. 门禁结果

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单测（任务书指定命令） | `pytest tests/unit/test_simulation_runtime.py -v` | **62 用例：61 passed, 1 skipped**（skip=pyarrow 缺席的往返用例，降级用例在跑） |
| 全量单测回归 | `pytest tests/unit -q -n 2 --no-cov`（worktree 最终代码 vs master 基线同口径对照） | 见附录 |
| Lint | `ruff check app/services/simulation/ app/tools/simulation_tools.py app/tools/categories.py tests/unit/test_simulation_runtime.py app/services/task_queue.py app/tools/__init__.py` | **All checks passed** |
| 边界 + env 卫生 | `pytest tests/unit/test_geocompute_boundary.py tests/unit/test_env_hygiene.py` | **199 passed** |
| 工具面全链路冒烟 | init_tools（332 工具）+ registry dispatch + 错误路径 | **通过**（守恒残差 0.00e+00） |

守恒类断言阈值：水文/交通残差 ≤ 1e-9（相对），雨强窗口解析对账 1e-12。

## 8. 已知限制与后续（非阻塞）

1. 交通模型逐边 Python 循环（确定性优先），万边×万步规模需向量化迭代器。
2. MVT 切片 sink 未实现（`TemporalSink` 协议已预留；mvt.py 服务在位）。
3. ST-GCN（B011）作为第三个 law 接入的预留位：`build_law` 注册表 +
   `DynamicPropagationLaw` 接口，训练侧不在本 ADR 范围。
4. 水文为线性化扩散波，非全动力波；不等价填洼请用 `app/lib/geo_analysis/terrain.py`。

## 附录：全量回归归因（worktree 最终代码 vs master 基线，同口径 `-q -n 2`）

| 运行 | 结果 |
|---|---|
| master 基线 | 11678 passed / **38 failed** / 118 skipped |
| worktree（本分支） | **11730 passed / 38 failed / 128 skipped** |

worktree 失败集合与 master 的差分**全部为并行/环境抖动**，逐项核验：

- 差集 10 项（geocompute v5_scheduler / v6_routes / v6_chaos）：串行复跑 46/47
  通过；唯一串行失败的 `test_delayed_heartbeat_reclaim`（心跳时序混沌）在
  worktree 重跑 3/3 通过 —— 负载敏感抖动，非本改动。
- master 独有 9 项（runtime_validator×8 需 Node CLI、worker crash 回滚）：
  同类时序/环境抖动。
- 交集 28 项：Windows 环境固有（PostGIS 拒连、Node CLI 缺失、rlimit 不可用、
  真实 socket、双进程并发等）。

**结论：本任务改动引入的回归失败为 0。** 顺带观察：master 误 track 了 33 个
`.coverage.root.*` 覆盖率临时文件（任何覆盖率运行都会触发删除），本分支保持
变更集聚焦未夹带清理，建议单独开 chore 分支处理。
