# Science V5 — Review Findings

## Round 0 — Subagent-A 架构挑战（Phase B，实现前）

结论：**修订后可实施**。核心数学断言（共享权重批量 SGS、pad ST 系统、
堆叠 LMC solve、fold 上收、工具注册安全）经验证正确；以下发现已全部
回写 01-architecture.md。

| # | 严重度 | 发现 | 处置 |
|---|---|---|---|
| 1 | CRITICAL | ST 窗口不足目标 V4 语义是 relax 后仍解系统，非邻域均值回退 | 架构 D3 修订：pad 逐目标 relaxed 列表；均值仅限 solve 失败；differential 加远时目标 fixture |
| 2 | CRITICAL | ST pad 约束行若保留 1.0 会把 μ 拉进全部有效权重 | 架构 D3 修订：约束/漂移行列同置零；3 邻域手算 oracle |
| 3 | MAJOR | 批量 solve 对近奇异片静默返回巨大有限值——隔离条件须含非有限检查 | 架构 D3 修订：`isfinite(sol).all(axis=1)` 掩膜 + 逐行隔离；oracle 植入奇异+退化行 |
| 4 | MAJOR | SGS 单一共享路径系统性低估 ensemble std（丢 between-path 分量） | 架构 D3 修订：P 组共享路径（默认 P=8），组间路径方差回入 ensemble |
| 5 | MAJOR | 固定 100 万窗口在 R=100 下不可达（ensemble 预算 20M 才是真约束；O(R·T) 工作矩阵 800MB） | 架构 D3/D4 修订：动态窗口 = MAX_ENSEMBLE_CELLS/R；预算表如实列 O(R·T)；双上限负例 |
| 6 | MAJOR | sgs/lmc/st 变体窗口按 feature_count 匹配是错单位（规模瓶颈是目标格点） | 架构 D4 修订：窗口按 raster_cells 空间声明 + notes；select_backend 测试钉死 |
| 7 | MAJOR | `backend="numpy_batched"` 不在封闭词表——validate 必红 | 架构 D4/D5 修订：variant id="numpy_batched"、backend="numpy"；tolerance 措辞 variant-scoped |
| 8 | MINOR | `_spatial_block_folds` 无外部消费方——上收安全；须 re-import 同一对象（别名非复制） | 采纳（W1 验收项） |
| 9 | MINOR | temporal_forward 同时刻样本跨边界会打破严格泄漏断言 | 架构 D1 修订：边界只落唯一时间值；unique<folds 类型化拒绝；z 过滤非有限 |
| 10 | MINOR | temporal 能力 output_artifact_types 仅 stats_table/raster_surface；numpy_batched 变体若工具不消费 select_backend 是死元数据 | W5/W7/W8 验收项：能力包 additive 扩展 + 工具接线 + parity 同步 |
| 11 | MINOR | 工具注册失败是 warning 不阻塞——须跑 registry validate 兜底；tier=2 不触 tier-1 守卫 | W8 验收项 |
| 12 | MINOR | plan_execution 用独立 frozen ExecutionPlan，不动 BackendDecision/resolver | 采纳 |
| 13 | MINOR | R<2 时 SGSEnsemble.std=0 是伪精确；跨估计器字段易混读 | 架构 D5 修订：estimator 必填；std None+披露；data_quality 不合成 |
| 14 | NIT | savgol 对含 NaN 窗口整体投毒；架构内存算式勘误 | 架构修订（4.7MB@k=24）；phenology 先 gap-fill 再平滑 + >max_gap 负例 |

## Round 1 — Subagent-A 最终 diff 审查（Subagent-A resume；R0 14 项全部验证关闭）

结论：核心数值（三批量求解器/两 P1 修复/CV splitter/水文/不确定性/
dispatch）验证正确且诚实测试；必修项如下，均已修复。

| # | 严重度 | 发现 | 处置 |
|---|---|---|---|
| 1 | BLOCKER | st_cross_validate 自条件泄漏：predict_fn 用全量样本做条件集，测试样本以 τ=0 进入自身条件集（时间守卫不可见） | cv.run_cross_validation 契约改为 predict_fn(model, train_idx, test_idx)；st_cross_validate 闭合改为仅训练子集条件；对抗测试（扰动最后折测试值 → spy 捕获预测逐位不变） |
| 2 | MAJOR | oracle corpus 94% 哑弹（33/35 case 以 _identity 探针自比，永不失败） | builder 全部重写为生产目标绑定（cv/lmc surface/sgs surface/st surface/phenology/anomaly 适配器/pfafstetter/topology + select 路径），_identity 探针删除；23 case |
| 3 | MAJOR | cube/phenology/anomaly 无 H×W 守卫（T≤512 单独挡不住 34GB 栈），descriptor 声明与实现不符 | build_cube 单一咽喉加 CUBE_MAX_ELEMENTS=8,388,608 类型化拒绝；descriptor envelope/notes 诚实化 |
| 4 | MINOR | SGS auto 恒选 batched（声明序偏好），架构文档窗口分档表述过时 | 架构文档 D4 修订为声明序偏好语义（本表下方） |
| 5 | MINOR | ST 变体注记"逐位一致"过claim（仅无填充行成立） | 注记收敛："无填充行逐位一致、填充行数学等价" |
| 6 | MINOR | temporal.phenology/anomaly 声明 uncertainty_outputs 无产出（死元数据） | 声明移除（声明即产出纪律） |
| 7 | MINOR | 多级 Pfafstetter 父码 ≥10 时拼接不可十进制解读 | descriptor limitation 披露（唯一性/层级语义保持） |
| 8 | MINOR | cv usable 措辞 overclaim；topology docstring "不混计"与 ≥ 实现矛盾 | 两处措辞修正 |
| 9 | MINOR | SGS differential 阈值放宽（已在测试 docstring 披露）；anomaly nanstd n=1 RuntimeWarning | warning 抑制；阈值放宽保留（披露） |

## Round 2 — Subagent-B 最终 diff 审查（性能/安全/并发/兼容）

结论：无 BLOCKER/CRITICAL；3 MAJOR 全部修复，MINOR 全部收口。

| # | 严重度 | 发现 | 处置 |
|---|---|---|---|
| 1 | MAJOR | validate_flow_topology O(N) 纯 Python 扫描无取消点，descriptor 声明 coarse 未兑现；50M 格网上不可取消 | 链头种子化（bincount 入度 0）+ flatnonzero 拾取环链 + 每链 checkpoint；cpu_cost medium→high 诚实化 |
| 2 | MAJOR | st_kriging_surface 执行计划传 feature_count+raster_cells——feature 优先使 raster_cells 窗口声明失效（R0-#6 回潮） | 驱动只传 raster_cells（镜像 SGS） |
| 3 | MAJOR | phenology gap-fill 峰值瞬态 ~15× 单数组（cap 处 ~0.8-1GB）与"64MB"注释不符 | 削减冗余临时数组（复用 v、去掉多余 astype/中间量）+ CUBE_MAX_ELEMENTS 注释如实披露 ~10-15× 倍数 |
| 4 | MINOR（正确性） | SGS batched chunk<k 时 sim 块对角写错位置（k_sim+sdiag 应为 k+sdiag） | 索引修正 + **chunk ≥ k 钳制**（k_sim<k 病态区制实测锚定跌至 ~0.6 且不随 R 收敛——直接消灭该区制）+ 回归测试 |
| 5 | MINOR | LMC/ST 批量 happy-path 仍有逐行 numpy 微调用 | 有限性掩膜一次向量化（逐行 matmul 保留——逐位 differential 锚依赖同一 BLAS 求和次序，如实注释） |
| 6 | MINOR | ST 邻域解析从 chunk 局部升为全量 O(n_t·k_query) 常驻；d_all 死变量 | tau/窗口/argsort 移回 512-chunk 循环；d_all 不落盘 |
| 7 | MINOR | 工具 JSON 先拷贝后守卫 | _precheck_json_size（shape/len 预检，零拷贝）前置到三个工具入口 |
| 8 | MINOR | cv.py 模块 docstring + 类型注解残留 2-arg 旧契约 | 同步 3-arg（防消费者按旧 docstring 重引入泄漏） |
| 9 | MINOR | test_sgs_window_unit_is_raster_cells 判别力为零（两解释下都过） | 改判别性断言（rationale 含「像元数」+ 纯 raster profile deferred 语义） |
| 10 | NIT | 工具测试格式、未用 logger、sim_values 瞬态披露、phenology 冗余 astype | 均已处理（重复 W9 commit 标题为历史不可改，PR 描述说明） |

## 验证（R2 修复后）
- tests/unit/lib + tests/science_oracles + tests/quality 合并车道：
  **2405 passed, 0 failed**（含新对抗/钳制/判别性回归）
- 生成物链再生成（manifest/report/certifications/drift/ledger）
