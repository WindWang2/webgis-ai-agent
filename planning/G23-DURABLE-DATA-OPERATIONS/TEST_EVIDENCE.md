# G23 TEST_EVIDENCE — 测试与验证记录

BASE=`b8f2a6ecc`（origin/master 2026-11-11）｜HEAD 见 PR。
环境：Python 3.12.15（uv venv，requirements.lock+dev）；本机无 Redis/Postgres
（本地降级拓扑 = 项目自带 USE_REDIS=false + memory broker + 内存 session store，
与 G18 测试范式一致）。pyarrow 为本地补装（requirements 未列但 tests/delivery
需要；非本方向依赖变更）。

## 1. 单元/契约（deterministic）

```
.venv/bin/python -m pytest tests/data_operations -q --no-cov   # 24 passed
  test_runner.py     7  认领互斥/fencing/两段取消/幂等/孤儿清扫/进度fenced
  test_import_jobs.py 8  A01/A02/A03/A07-09/A11/A13/A16
  test_compute_jobs.py 6  job 化/收据短路/取消桥/排队收敛/A10/A14
  test_scale.py       3  300k RSS+行数完整/预算超限/ENOSPC（heavy 标记）
.venv/bin/python -m pytest tests/data_import -q --no-cov        # 41/41（BASE 语义保持）
.venv/bin/python -m pytest tests/jobs tests/delivery -q --no-cov # 288 + 57（durable 基座与 G18 回归）
.venv/bin/python -m pytest tests/data/test_field_compute*.py tests/unit/test_field_compute_tools_v1.py -q
                                                               # 123/124；1 失败为 BASE 预存
                                                               #（TestOpenApiCompatibility::test_existing_short_schema_names_preserved
                                                               #  在干净 BASE worktree 同样失败——OpenAPI schema 名消歧，非 G23 引入）
```

BASE 侧同条件复现记录：预存失败清单见上；data_import/jobs/delivery 全绿。

## 2. 双真实进程复现（P0）

- BASE 误复位：`bin/run_repro.sh`（A 提交 350k 行 CSV 中，B 读 → committing 被复位为 configured）→ evidence-p0-base-b.json.log（false_reset_reproduced=true）
- 修复后：同脚本 → evidence-p0-fixed-b.json.log（false_reset_reproduced=false，rev 不变，job_id=1 正常完成）

## 3. 双 uvicorn 集成（P7，真实进程）

`bin/integration_two_process.py <out>` → evidence-integration-report.json（5/5 pass）：
- 拓扑：2×uvicorn（独立 PID/端口）+ 共享 SQLite(WAL) + 共享 DATA_DIR + 真实 HTTP
- S1 跨副本可见+不重置（A03）｜S2 B 取消→双面 cancelled（A07/A08）｜S3 B 重提→completed
- S4 kill -9 A→B 诚实 committing→注入 stale（模拟 300s 心跳超时后的周期 sweep）→归一化→A 重启视图一致（A04/A25）
- S5 45k 面 geojson 导入(B)→compute 后台(B)→A 查 running→A 取消→cancelled（compute 面 A07；全链 import→compute→任务中心）
- 资源：RSS 峰 A 744,876KB / B 762,852KB（含应用基线 ~487MB）、DATA_DIR 峰 ~40MB（resource_curve.csv）
- 服务器日志（含 job 迁移关联）：evidence-integration-uvicorn-{a,b}.log
- 已知边界（如实记录）：本地无 Redis → session ref 进程内可见（S5 执行与 ref 同进程 B，查询/取消自 A）；GIS_MANIFEST_STRICT=0 为 direct-uvicorn 启动的仓库自带开关（BASE 既有，与被测语义无关）

## 4. 规模档位（P5，measurement not promise）

| 档位 | 结果 |
|---|---|
| 300k 行 CSV 导入（大于任何缓存） | 完成；receipts input=output=300,000 拒绝 0；RSS 增量 <1.2GB |
| 500,001 行（超 COMMIT_ROW_LIMIT） | ROW_BUDGET_EXCEEDED typed 拒绝，源完好 |
| 45k 面 geojson 计算（工作台上限内） | 完成/cancelled 双路径收敛（集成 S5） |

## 5. 未完成/受阻验证（如实）

- **Pi 真实子进程端到端（A22 全层）**：vendor/pi 未构建（`pi_compat: RPC entry NOT built`，master 既有状态，构建属 G22 方向所有权）。工具层经真实 ToolRegistry dispatch 验证（test_field_compute_tools 6/6、tests/data_import/test_tools_dispatch 4/4）。集成验证以 HTTP 层完成同等 submit→status→cancel→ref 链。
- **真实 Postgres 方言**：本机无 Postgres；所有 SQL 经 SQLAlchemy 核心表达式（UPDATE..WHERE rowcount、双方言主键 variant），SQLite 全量验证 + real_services lane（CI）覆盖。未声称已验证 Postgres。
- **浏览器层（E2E）**：前端改动为类型+渲染块+i18n（tsc 通过）；未跑 Playwright（时间的边界——非 mock 的 UI 验证留给既有 e2e 旅程回归）。
