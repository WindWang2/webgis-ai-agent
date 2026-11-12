# G23 DUPLICATION_MATRIX — 能力现状 vs 本任务增量

BASE: `b8f2a6ecc`（2026-11-11 master）

| 能力 | 现状证据 | owner/PR | 本任务剩余增量 | 共享接口 | 明确不做 |
|---|---|---|---|---|---|
| durable job 存储/生命周期 | `app/services/jobs/store.py`+`lifecycle.py`（ADR-0052，#1398 fencing） | 已合并（多 PR 演进） | **零**——直接复用 | `DurableJobStore.*`, `JobStatus`, `JobKind` | 不改迁移表/不加状态 |
| worker 运行时（Celery 同步体） | `jobs/worker.py`（watchdog/心跳/原子产物） | 已合并 | **零** | `durable_job()` 用于 Celery 域 | 不为 async 操作强套同步上下文 |
| 进程内 async runner 范式 | `data_delivery/service.py:232-400`（G18） | #2080 | 提取为可复用 `data_operations/runner.py`（async 认领/心跳/取消探针/fenced 终态） | 新模块内部复用 store | 不改 data_delivery 本体（回归即可，A24） |
| 导入 staging/计划/预览/提交 | `data_import/`（G17） | #2079 | 仅执行接线：commit→durable job、恢复判据、取消桥接、进度 | `DataImportService` 公共方法 | 不动 staging 布局/解释器/发现预算（budgets.py 已有 A18） |
| 字段计算 typed IR/预览/执行/收据 | `data_preparation/field_compute/`（G19） | #2074 | 仅执行接线：execute→durable job、跨进程取消、进度 | `service.execute/request_cancel` | 不动 parser/interpreter/snapshot（A14 保持） |
| 任务中心 UI/API | `/api/v1/jobs` + `JobView` | 已合并 | `JobKind` 两个新值 + `member_status` 有界投影（仿 atlas `page_status`） | `job_from_record` | 不改既有端点形状 |
| Pi 工具 | `data_import_tools.py`（commit 同步阻塞）、`field_compute_tools.py` | #2079/#2074 | commit/apply 改 submit+handle；新增 status 查询工具 | ToolRegistry `@tool` | 不加长等待；不动其它工具 |
| 心跳/stale/孤儿清扫 | `store.py sweep_stale/sweep_orphans` | 已合并 | 确认有周期驱动（否则读路径惰性恢复兜底；见 RECON） | — | 不新建第二清扫器 |
| 多进程部署 | docker-compose.prod：api+celery-worker 共享卷 | 既有 | 双 uvicorn+SQLite 集成验证（P7） | — | 不改 compose/部署 |
| G09 批量分析 / G21 OD flow | 其他方向 worktree 在途 | 非 G23 | 零 | — | 不碰 |
| Lakehouse GC / R1 专项 | issues #2070 等（有 owner） | 非 G23 | 登记依赖即可 | — | 不修 |
| ADR 编号 | — | — | 如需 ADR 用方向命名文件 | — | 不抢占编号 |
| MapSpec adoption | 导入产物 → ref → 图层引用既有链 | #2079 | 零（产物注册不变） | — | 不改 MapSpec schema |
