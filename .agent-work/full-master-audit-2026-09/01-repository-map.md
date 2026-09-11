# 01 — Repository Map

census 全表：`census/production-source-census.csv`（1,588 文件 / 481,838 LOC，
含 path/language/loc/subsystem/review_owner/review_status/risk/findings 列，
review_status 由各 agent 的 coverage 文件回填）。

## 目录 → 子系统 → 审查归属

| 路径 | 文件数 | 子系统 | 审查 agent |
|---|---|---|---|
| app/agent_pi_bridge.py + app/services/chat/** + extensions | 45+ | Agent/Chat runtime | A |
| app/services/gis_harness/** | 101 | GIS Harness | A |
| app/tools/** | 64 | Tool plane | A |
| app/evaluation/** + session* + planning/collab/jobs | 50 | 评估/会话/任务 | A |
| app/lib/gis/** + geo_analysis/** + geo_raster/** | 118 | 算法/能力注册表 | B |
| app/lib/modelops/** + app/services/modelops/** | 37 | ModelOps | B |
| app/services/geocompute/** | 32 | 分布式计算 | B |
| app/services/workflow_runtime/** | 15 | 工作流 runtime | B |
| app/services/data_fabric/** + lakehouse/** | 83 | 数据平面 | B |
| app/services/network/temporal/spatial_decision/** | 52 | 领域服务 | B |
| app/lib/cartography/** + services/mapspec/** + gis_world_state/** | 65+ | 制图/MapSpec | C |
| frontend/**（生产代码） | 636 | Workbench/前端 | C |
| app/core/** + api/** + models/** + schemas/** + tasks/utils/adapters | 70+ | 平台/安全 | D |
| app/extensions_platform/** | 45 | 扩展平台 | D |
| migrations/** + scripts/** + docs/integration/** + 配置 | — | 集成/质量工具 | D |
| tests/**（1,141 文件，273K LOC） | — | 测试质量抽查 | 全员 |

## 其他顶层目录

- `vendor/pi`（git submodule，Pi agent runtime）
- `agent/`（旧 agent 壳，审查归属 A）
- `wayfinder/`、`.wayfinder/`（导航/地图工具）
- `specs/`、`extensions/examples/`
- `.worktrees/`：10 个待合并 V7/V8 分支工作树（见 00b-merge-decision.md）

## 平台事实

- 后端 FastAPI + SQLAlchemy async + alembic（39 revisions，单 head c0d8322aa2cb）
- Redis（可选，USE_REDIS=false 走内存实现）
- 前端 Next.js + MapLibre + Zustand + vitest
- Python 3.13；Windows dev 机存在 fcntl/SSRF 校验平台问题（M-1/M-4/M-5）
