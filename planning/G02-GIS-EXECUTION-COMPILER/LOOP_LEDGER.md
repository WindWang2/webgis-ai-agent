# G02-GIS-EXECUTION-COMPILER LOOP LEDGER

| 轮 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|
| 1 | M0 审计：master/PR/分支/Issues 实时重查 + 管线调用点追踪 → BASELINE.md（4 断链 C1-C4） | git/gh 实查；vnext 分支 0-ahead 验证 | ✅ | characterization |
| 2 | M1 characterization 19 测试（钉 C1-C5 断链） | pytest 红（ModuleNotFoundError 实证断链） | ✅ 红=目标 | 实现核心 |
| 3 | M2/M3 execution_compiler 六模块 + GC-01 收割 + 验收桥接线 + compile 工具 | characterization 19 绿；requirement 500 绿 | ✅ | 语料 |
| 4 | GC-10 语料 23 类：首跑 14/23 → 修编译器（risk_exposure/temporal_trend/spatial_equity 映射、raster 硬排除、near 权重 6.0、edit 口径 patch、followup 走真实 patch 路径） | 语料 23/23 绿 + 508 回归绿 | ✅ | oracles |
| 5 | M6/M7 oracles 30 测试（确定性双跑/#2005/#2006 回归/live conformance/生产桥/对抗有界/并发纯度）+ 文档两份（子代理 A 冷读起草） | 49/49 绿；子代理抓出 `__init__` 未 re-export 生产断链 → 修复+回归钉 | ✅ | 质量门 |
| 6 | 质量门：ruff --fix 全清；mypy 42→46→42（4 个新增 Provenance kwarg 修复，42=基线既有） | ruff 0 error；mypy=基线；469+49 测试绿 | ✅ | review |

## 失败→修复记录（可审计）
- F1 语料 hazard/网格率/网络距离 5 例失败 → 根因：理解层缺「最近设施」
  cue + 任务映射缺失 + tie-break 靠字母序 → 修 `_NEAR_CUE_RE` 收割 +
  `_TASK_TYPE_FAMILY` 补映射 + 关系权重 6.0 + raster 排除（不迁就期望）。
- F2 edit-statistic 失效面失败 → 根因：edit 话语口径 cue 不进 patch
  journal（genesis-only 收割）→ diff_to_patches 补 emit（白名单路径）。
- F3 子代理发现 `execution_compiler/__init__.py` 未导出
  `evaluation_view_with_workflow_rows` → 生产 pipeline import 静默降级
  → 补 re-export + oracles 钉死 importable。
- F4 mypy 新增 4 错（Provenance kwarg）→ 显式 turn/rationale。
