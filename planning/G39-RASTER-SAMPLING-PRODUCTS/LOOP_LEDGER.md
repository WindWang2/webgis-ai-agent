# G39 LOOP_LEDGER — 执行账本

- base：`1276692737a8cf58d0604fb40ae0e70fc61779a2`（origin/master @2026-11-14 复核）
- 分支：`zcode/g39-raster-sampling-products-20261114T172106Z`
- worktree：`/home/kevin/projects/webgis/webgis-worktrees/g39-raster-sampling-products-20261114T172106Z`
- 预算声明：宿主未提供 Goal usage budget 配置口；实际可用额度未度量的部分如实标注。本任务消耗（主 Agent 上下文 + 1 Explore subagent ~324k tokens + 即将开始的 review subagent）。

## 轮次记录

### R1 — 基线与盘点（完成）
- PR 审计（Explore agent）：#2094-#2100/#2078/#2080/#2089 review 通道全空（verified）；#2117(G37) 只碰 tools/__init__(+1)/agent_pi_bridge/pi_rpc_client/migration g3701 → 避让；工具命名收敛 webgis_ 前缀；G24 materialize 通道确认。
- 关键基线实读：probe.py/source.py/budgets/measurements/terrain_measurements/RasterReader/runner/compute_jobs/data_delivery/lakehouse data_object/artifact_registry/selection_handles/main.py include/tools __init__/field_compute 先例（BASELINE.md 表）。

### R2 — 后端实现（完成）
- 9 个服务模块 + schema + 路由 + 工具 + JobKind + 2 处共享文件加性接线（INTERFACES.md）。
- 修正项：kernel 插值需显式 continuous（原只拒 categorical）；reader 调度 open_raster 上下文管理器两阶段（collect 一次开、批读一次保持）；decoded_bytes 按真实块布局记账（窗口面积→块面积，S18 语义）；materialize_geoparquet 不回显 path → fabric_parquet_path 派生；RasterExpressionError→RasterSamplingError 映射；执行前绑定现行性复核（S31 收据短路漏洞）。

### R3 — 测试与旅程（完成）
- tests/raster_sampling 53 passed；Pi 旅程 1 passed（stub 转义 JSON 的 rsp_ token 扫描修复 + 每轮 tool_search 激活 + ref_cursor args_model 修透明解引用误伤）。
- 面板 vitest 8 passed；tsc 绿。
- S29：真实 G18 durable job 交付 CSV 全行回读。

### R4 — 邻域回归（完成）
- raster_authoring+science_oracles 513 / data_operations+jobs 247 / delivery 163+1 预存在失败（干净 base 同败：`cd /tmp/g39-base-check && pytest tests/delivery/test_service_flow.py::test_fabric_parquet_fast_path` → 同样 KeyError 'path'；base 工作树 127669273）。
- registry ratchet 8 / i18n+tab 16 全绿。

## 待办

- [ ] R5 独立 review → 修复 → 复审
- [ ] R6 最终 fetch master 复核 + commit + push + PR

## 决策记录（摘）

- 计划/收据存储走 BASE_STORAGE_DIR 文件先例（field_compute 同款），不加 DB 迁移。
- 长表走 materialize_geoparquet（fabric ref 可被 G18 直接交付 + 台账 + CAS 一体），不自建 store。
- 显示曲线不落库：由 results API 从长表按需抽稀（单一事实源）。
- 工具面 session 经 `_resolve_session_id`（runtime ctx 注入，与 upload/field_compute 同款）。

### R5 — 独立评审 → 修复 → 复审（完成）
- 第一轮：2×P0（POST session_id 布线 422 + body session 跨会话漏洞）、2×P2（无效原因塌缩、wait 无钳制）、7×P3 —— 全部修复（详见 REVIEW.md）。
- 根因发现：facts.nodata 服务路径从未填充（G20 band_meta 无 nodata）→ descriptor 回退修复；端到端分类回归测试修复前红后绿。
- 第二轮复检：真实守卫零 override 探针（422/404/body 攻击不可能/跨会话 404）、三因分类含真 GDAL mask 洞、数学复检、全套复跑一致 → **APPROVED**；遗留 P3 卫生项随后清理（57 绿复跑）。

### R6 — 终局（进行中）
- 终局 fetch：origin/master 仍为 127669273（base 未移动，无 rebase）；开放 PR #2117(G37)/#2118(G36) 为兄弟域，仅共享 app/tools/__init__.py 与 app/main.py 的加性行（无文件级冲突）。
- 最终验证汇总：tests/raster_sampling 57 passed；Pi 旅程 1 passed（真实子进程）；raster_authoring+data_operations 107 passed；delivery 163 passed + 1 预存在失败（干净 base 同败）；registry ratchet 8；前端 panel+i18n 18 passed；tsc exit 0；ruff（uvx）G39 文件全绿。
