# G38 TEST_EVIDENCE — 执行环境与真实证据

## 环境

- Worktree `webgis-worktrees/g38-data-join-authoring-20261114T151651Z`（base 1276692737）
- Python `.venv-g38`（uv 按 requirements.txt + requirements-dev 锁定安装；pandas 2.3.3 /
  geopandas 1.2.0 / shapely 2.2.0；pyarrow 本环境不可用 → 权威载体按运行时探测
  走 GeoJSON/rows-JSON 全保真路径，见 INTERFACES 已知边界）
- 前端 pnpm 10.34（vitest 4.1.12 `--no-file-parallelism`）；vendor/pi 自 pin
  b4afdddf 构建（scripts/build-vendor-pi.sh，仅本地验证）

## 真实 Pi 旅程裁决（PI_JOURNEY_E2E=1，最终 1 passed ~31s）

15/15 门（verdict.json）：

```json
{"warmup_ok": true, "describe_receipt": true, "plan_upsert_receipt": true,
 "plan_created_via_tool": true, "preview_full_counts": true,
 "run_completed_db": true, "receipt_counts": true,
 "result_total_rows_full": true, "output_file_rows": true,
 "value_oracle": true, "crs_preserved": true, "identity_preserved": true,
 "render_ref_rows": true, "delivery_completed": true,
 "delivery_reread_rows": true, "delivery_reread_values": true}
```

链路：stub LLM（node）→ 生产 uvicorn（真实 vendor Pi bridge 子进程）→
`/pi-tools` dispatch → ToolRegistry → `webgis_data_join_*` → service →
durable job → 终态包 + DataObject → 渲染 ref → G18 `/data-delivery/jobs` →
下载回读（1101 行、抽值 pop=10000、CRS EPSG:4326 与收据一致）。
工具指标（进程 digest 实录）：execute 768ms / preview 61ms / describe×2 /
result 7ms / plan_upsert 4ms，errors=0。

oracle 摘要：左 1101 面（1100 匹配 + 1 无人口区）× 右 1101 行（1100 匹配 +
1 无边界行）→ matched_pairs=1100，output_rows=1101（left keep），未匹配区
pop=None + `_join_status=unmatched`，首区 `_wfid=wf1-b0` 保留。

## 性能曲线（本机 16C/62G，单进程，重阶段锁内）

属性关联（高匹配率，1:1，inner）：

| 输入（每侧行数） | 输出行 | 耗时 |
|---|---|---|
| 1,000 | 1,000 | 0.25s |
| 5,000 | 5,000 | 0.14s |
| 20,000 | 20,000 | 0.62s |
| 50,000（默认源预算上限） | 50,000 | 1.57s |

空间点面（within_or_boundary + pick_by_field，2000 点 × 40 面）：输出 2000 行 /
候选 1269 对 / 0.11s。进程峰值 RSS ≈ 130MB（50k 行装载 + 执行全程）。
取消响应：J20 测试实测 —— DB 取消事实后看门狗 ≤2.5s 轮询点燃检查点，run 行
落 cancelled、无终态包（staging 清理）。

## 后端单测：61 passed（tests/unit/data_join_authoring/，-q --no-cov）

## 前端：vitest 4 passed；tsc --noEmit 干净；eslint --max-warnings 0 干净

## 迁移实测

`DATABASE_URL=sqlite:///tmp/g38-alembic.db alembic upgrade head` →
0107 落表（data_join_plans/data_join_runs，CHECK 词表与模型一致，
alembic_version=0107_data_join_authoring）。

## master 既有失败（干净 base 1276692737 worktree 复现，与本分支无关）

1. tests/test_alembic_metadata.py::test_alembic_upgrade_head_survives_percent_encoded_url
2. tests/unit/test_geocompute_execution.py::TestExecutor::test_reuse_and_invalidation
3. tests/quality/test_findings_ratchet_gate.py::test_ratchet_passes_against_committed_baseline
4. tests/quality/test_quality_scenario_corpus.py::test_slice_geometry_states_green[science_]
5. tests/test_api_auth_required.py::TestAllMutatingRoutesAuth::test_mutating_routes_use_auth_enforcing_depends
6. tests/delivery 5 项（geoparquet 写读/fast-path/ids-parquet 有界读等）：本任务私有
   venv 按 requirements.txt+dev 安装不含 pyarrow（仓库将其定位为运行时探测的
   可选依赖，`arrow_available()`，requirements 无声明）—— 干净 base 同一 venv
   逐位复现（5 failed / 52 passed 完全一致），属环境既有而非回归；本域 GeoParquet
   投影路径在同一探测语义下诚实降级（见 INTERFACES 已知边界）。

