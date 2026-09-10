# Science V5 — Plan（Phase C 执行序 · 允许按审计重排）

Waves（合并原建议 26 项为 12 个可提交波次；scope 不减项）：

| # | Wave | 内容 | 关键交付 |
|---|------|------|---------|
| W1 | CV 框架 | `cv.py`（spatial_block/temporal_forward/index + run_cross_validation + leakage guard）；kriging `_spatial_block_folds` 上收 | cv.py + 单测（正/负/边界）+ kriging 兼容 |
| W2 | Variogram v5 | compare_variogram_models（RSS/AICc/旗标）+ _fit_model 确定性 multi-start polish | kriging.py + oracle case + 单测 |
| W3 | LMC 批量 | cokriging_lmc 批量 solve + 病态隔离 | differential 逐位测试 |
| W4 | ST 批量 + ST CV | kriging_st 批量系统 + st_cross_validate（时间前向+空间块） | vertical slice 主体 |
| W5 | SGS 双实现 | batched SGS（共享路径）+ reference 保留 + 上限分档 + 统计 differential | kriging_simulation.py |
| W6 | Uncertainty | uncertainty.py artifact + from_sgs/from_kriging/from_st + 工具挂接 | uncertainty.py |
| W7 | Backend dispatch v5 | plan_execution + descriptor numpy_batched 变体窗口 | backend_selection.py + descriptor |
| W8 | 立方体+物候 | temporal_cube.py + phenology.py + anomaly + descriptor + 工具模块 | 新模块 + 工具面 |
| W9 | Hydrology | multilevel Pfafstetter + topology validation + terrain 工具 | terrain.py |
| W10 | Oracle v5 + benchmark | gen_science_oracles science_v5 域 + work-count/结构 benchmark | oracle corpus + benchmark |
| W11 | 文档/生成物 | catalog/manifest 再生成 + CHANGELOG 最小追加 + progress 收口 | 生成物一致 |
| W12 | Review | Round 1 (Subagent-A) → 修 → Round 2 (Subagent-B) → 修 → rebase 复测 | 清零 BLOCKER/CRITICAL/MAJOR |

每个 wave：实现 → targeted tests → ruff → progress 更新 → 独立 commit。

## 验证车道（本地，不依赖 CI）
- `pytest tests/unit/lib/test_cv_framework_v5.py tests/science_oracles -x -q`（串行）
- `pytest tests/unit/lib -q -p no:cacheprovider`（邻接回归）
- `pytest tests/quality -q`（quality gates）
- `pytest tests/benchmarks/test_spatial_science_benchmarks.py tests/benchmarks/test_backend_scale_decisions.py -q`
- `ruff check app/lib/geo_analysis app/lib/gis app/tools tests`
- registry validate（192+ 算法 0 错误）+ 生成物 parity 测试
