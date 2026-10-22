# G01 TEST MATRIX

运行约定：`.venv/bin/python -m pytest <paths> -q --no-cov --disable-warnings -p no:cacheprovider`（venv 用主仓 .venv，cwd=worktree）；ruff 检查改动路径；不跑全量套件（#1753 挂起风险）。

| ID | 层 | 内容 | 命令/文件 | 状态 |
|---|---|---|---|---|
| T0 | 基线 | 既有 dataset_semantics 3 文件 | pytest 3 files | 42 passed (11.64s) @ b1b2ec23 |
| T1 | W1 单测 | 角色绑定：多角色确定性、ambiguous（同名）、乱序 ref 稳定、损坏 descriptor fail-closed、stale（expected 指纹）、partial | tests/unit/dataset_semantics/test_role_binding_v1.py | ✅ 21 passed |
| T2 | W1 契约 | primary_session_descriptor 向后兼容（行为不变：单数据 session） | 同上 | ✅ 含 compat 断言 |
| T3 | W2 集成 | 编译入口（compiler_v4）角色级 descriptor 事实可达；缺 descriptor = 现状不变 | tests/unit/dataset_semantics/test_role_qualification_wiring_v1.py | ✅ 10 passed |
| T4 | W3 失败注入 | project_runtime resume：指纹一致=valid、版本前进=stale、损坏=unknown、ref 淘汰、跨项目拒绝 | tests/unit/dataset_semantics/test_recovery_reuse_v1.py | ✅ 8 单测 + 1 集成 |
| T5 | W4 端到端 | 六场景：分布/数量/密度(分母)/双期/缺边界(typed gap)/同名(ambiguous) | tests/unit/dataset_semantics/test_scenarios_chengdu_v1.py | ✅ 9 passed |
| T6 | W4 preflight | 混合 CRS、单位冲突、时间不重叠、bbox 不相交、数量vs密度 | tests/unit/dataset_semantics/test_preflight_v1.py | ✅ 15 passed |
| T7 | W4 性能 | preflight/角色绑定 0 数据扫描（mock 计数）；p50/p95 vs master | 手动基准 + 断言查询计数 | ✅ 零扫描+O(refs)+p95<50ms 断言 |
| T8 | W5 复归 | 全部新增+既有相关测试重跑 | 汇总命令 | ✅ 150+136 全绿 |
| T9 | lint | ruff check 改动路径 | ruff | ✅ All checks passed |

既有失败披露：无（T0 全绿）。新增失败必须区分「本 PR 引入」vs「master 已有」。
