# EVIDENCE — GOAL 04 验证证据

## 测试运行记录（全部本 worktree 实测；Python=main 仓 .venv 解释器 + 本树代码）

### R1 / M1（commit c5f70c7）
- `tests/cartography/multiscale/test_scale_profile_v1.py`：19 passed
- `tests/cartography/test_mapspec_schema_v18_multiscale.py`：10 passed
- cartography 全套：3003 passed / 1 master 既有红
  （`test_mutation_parity_corpus[refresh_data_revision_stamp]`，stash 对照证实基线即红）
- vitest contract-manifest：23 passed；tsc --noEmit 0 错误
  （tsconfig.test.json 3 错误为 master 既有，vnext 同基线对照证实）

### R2 / M2+M3（commit 0e5c354）
- `test_importance_v1.py`（16）+ `test_generalize_v1.py`（15）全绿

### R3 / M4（commit 364a791）
- `test_plan_v1.py`：11 passed

### R4 / M6（commit 26ab4e8）
- 后端 `test_export_contract_v1.py` + 死码门 + 导出 parity：175 passed
- 前端 window-source + carto-ir + mapspec-compiler + runtime：690 passed

### R5 / M7（commit a4db0d6）
- `tests/unit/gis_harness/scale_profile/`：13 passed（服务 7 + 工具 6）
- 邻域回归：`test_audit4_tools_meta.py` + map_operations 套件 80 passed

### R6 / M5（commit f9d0753）
- `test_semantic_checks_scale_profile.py`：5 passed；multiscale 全套 82 passed

### R7 / M8（本 commit）
- 拓扑不变量：`test_topology_perf_v1.py` 快速族 7 passed（无自交引入/洞保持/
  面积误差界/共享边界覆盖面积界/主干留存/多带等价）
- 性能基准（`MSP_PERF=1`，本机 4 核 CachyOS，OMP=2；453s 全程）：
  - PERF_50K = `{"n": 50000, "elapsed_s": 13.43, "peak_mb": 92.6,
    "bands": 4, "artifact_sizes": {"world": 30000, "province": 40000,
    "city": 50000}, "same_output": true}`
  - PERF_200K = `{"n": 200000, "elapsed_s": 56.1, "bands": 4,
    "artifact_sizes": {"world": 120000, "province": 160000,
    "city": 200000}, "same_output": true}`
  - 口径：elapsed = 纯耗时（合成数据生成不计入）；peak_mb 在 50k 以
    tracemalloc 测得（该跑耗时被采样放大 ~25×，不进对照）；取舍比例
    world=60%（5 类词表前 3）、province=80%、city=100% 与
    DEFAULT_KEEP_RANKS 一致。
  - 优化前基线：10k 44.2s（shapely.ops.transform 逐坐标 Python 回调 +
    每带重复解析）→ 优化后 10k 3.2s（numpy 向量化 shapely.transform +
    单遍多带 + 线亚像素 DP 等价快路径），13.6×；复现脚本 = 本测试文件
    `_run_benchmark`（MSP_PERF=1）。

## 非破坏性证据
- `test_apply_commit_writes_profile_and_artifacts`：提交前后主源条目
  逐字节快照相等；工件全部为新 ref（`ref:gen:*`）。
- generalize 引擎：`test_input_features_not_mutated`（输入快照）。

## 诚实披露（not_evaluated 面）
- Playwright/headless 浏览器视觉回归：未执行（本环境无浏览器依赖安装；
  前端行为以 690 项 vitest + tsc 契约锁定）。
- MapLibre live 渲染实测（真实浏览器交互）：not_evaluated；窗口源覆盖的
  渲染面由 compiler/runtime 单测 + mapspec-to-svg 双孪生测试锁定。
- 真实第三方边界数据（自然地球行政区）：仓库无现成 fixture；M9 场景用
  确定性合成「真实形状」样本（共享边界网格 + 洞）如实披露。
