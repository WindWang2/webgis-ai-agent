# G3D03 EVIDENCE — 验证证据账本

所有命令在 worktree `../webgis-wt-g3d03-scientific-analysis`、其独立 `.venv`
（`uv sync` + pytest/pytest-asyncio/pytest-cov/ruff）下执行；串行运行。

## 基线

- `origin/master` = `1115d683a5c8343e44d8a9777e4ce05e073c29e5`（2026-10-26 fetch）
- 开放 PR = 0；开放 issue 仅 #1694（不在授权范围）

## 本地验证命令与结果（2026-10-26）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `.venv/bin/python -m pytest tests/unit/lib/test_terrain_measurements.py -q --no-cov` | **25 passed**（含 covered_horizontal 不变量与 <2 有效采样守卫）（解析曲面 oracle：平面精确/抛物线闭式/曲率解析网格对照/垂直基准/nodata/取消/确定性） |
| 2 | `.venv/bin/python -m pytest tests/unit/test_terrain_measurement_tools.py -q --no-cov` | **9 passed**（含词表 parity 钉扎）（四工具证据块/GeoJSON/负例/ft/测地） |
| 3 | `.venv/bin/python -m pytest tests/unit/test_measurement_mapspec_wiring.py -q --no-cov` | **6 passed**（M5：dispatch 形状 → converter → MapSpecLayer/MapSpecDocument schema 校验） |
| 4 | `.venv/bin/python scripts/gen_science_oracles.py terrain && .venv/bin/python scripts/regen_science_oracle_manifest.py` | terrain.json 216→**229 cases**（+13 measurement 解析 cases），manifest 同步 |
| 5 | `.venv/bin/python -m pytest tests/science_oracles/ -q --no-cov` | **2298 passed**（含 corpus drift pin） |
| 6 | `.venv/bin/python -m pytest tests/unit/gis/test_science_contract_v4_ratchet.py tests/unit/gis/test_g02_gate_chain.py tests/unit/gis/test_g02_contract_semantics.py -q --no-cov` | passed（契约 ratchet 不回归） |
| 7 | `.venv/bin/python -m pytest tests/unit/gis/test_g02_science_oracles.py -q --no-cov` | passed（O12 计数钉扎 237→**241** 随注册表合法增长更新，注释标注 G3D03） |
| 8 | `.venv/bin/python -m pytest tests/unit/lib/test_terrain_science_vnext.py tests/unit/lib/test_terrain_v3.py tests/unit/test_terrain_compass.py -q --no-cov` | **42 passed**（既有 viewshed/terrain 输出零漂移） |
| 9 | `.venv/bin/python scripts/check_tool_capability_conformance.py --tool terrain_profile --tool line_of_sight_analysis --tool surface_distance_analysis --tool surface_area_analysis` | 四工具 **overall=pass**（8 面：registration/search/planner/pi_native/proxy/dispatch/result/science） |
| 10 | `.venv/bin/python -m app.lib.gis.execution_catalog_docs` + `pytest tests/unit/gis/test_execution_catalog*.py tests/unit/test_execution_catalog_docs_drift.py` | passed（catalog 再生成：algorithm 237→241 / tool 359→363，drift 门绿） |
| 11 | `scripts/gen_quality_report.py / gen_determinism_certification.py / gen_resource_certification.py / gen_capability_catalog.py / gen_quality_manifest.py / gen_science_benchmark_manifest.py` + `scripts/check_generated_staleness.py --update` | 生成物账本刷新，`pytest tests/quality/test_generated_artifact_graph.py tests/unit/gis/test_foundation_v2_infra.py` → **65 passed** |
| 12 | `.venv/bin/python -m pytest tests/unit/gis/ tests/unit/gis_tool_conformance/ -q --no-cov` | **852 passed, 3 skipped**（含 live registry audit / capability binding governance） |
| 13 | 同上合并回归（评审修复后） | **2445 passed**（lib 25 + 工具 9 + wiring 6 + oracle 全量 + terrain 全族 + ratchet/g02） |
| 14 | `.venv/bin/ruff check <changed files>` + `.venv/bin/ruff format <changed files>` | All checks passed（格式化后 #1–#5 复跑仍绿：1166 passed） |

## 数值 oracle 摘要（解析期望，非实现回声）

- 斜平面 z=0.5·(row+col)，cell 1 m：4 m 水平线 → surface = 4·√1.25 = **4.4721360**、
  ascent = **2.0**、slope = surface（同一直线）；面积 = (n−1)²·√1.5、粗糙比 = √1.5。
- 抛物线 z=t² 剖面：闭式 ∫√(1+4t²)dt = **64.991556**（mpmath 复核锚点；
  0.25 m 步长弦和离散误差 ~1.1e-3 相对；bilinear 在凸函数上略高估 ——
  测试注释披露。首轮评审 P1-1 抓出 asinh 系数错误并已修正）。
- LOS 曲率：独立 400k 点解析网格 f(s)=h(1−s/D) − s(D−s)/(2R_eff) 对照
  min_clearance（D=7000/9000 × earth/earth_refracted 四场景，|Δ| < 8e-3 m）
  与 visible 判定逐一一致；R_eff = 6371008.8/(1−0.1307)。
- 墙遮挡：首遮挡点 = 墙前沿首个墙内采样 x=4.5（步长 1 m，精确）。
- ft：z_ft = z_m/0.3048 → ×0.3048 精确回米（面积/距离差 < 1e-4）。

## 负例矩阵（≥6 组科学负例，全部类型化）

| 负例 | 通道 | 断言 |
| --- | --- | --- |
| 1 无 DEM/不可读 | 工具 | `RasterReaderError`（共享读取护栏） |
| 2 nodata 洞 + policy=error | lib+工具 | `NO_VALID_OBSERVATIONS`（含修正提示） |
| 3 垂直基准不一致混算 | lib+工具 | `VERTICAL_REFERENCE_MISMATCH`（拒绝而非换算） |
| 4 z_units 非法词表 | 契约 | `parameter_contract_violation` |
| 5 超采样预算 | lib | `RESOURCE_SCALE_MISMATCH`（先拒绝后分配 + 建议步长） |
| 6 中途取消 | lib | `OperationCancelled`（段边界 checkpoint） |
| 7 零长折线/重合端点 | lib | `DEGENERATE_DATA` |
| 8 采样越界（error 政策） | lib | `NO_VALID_OBSERVATIONS` |
| 9 端点落 nodata | lib | `NO_VALID_OBSERVATIONS`（LOS 无定义） |
| 10 polygon 全在栅格外 | 工具 | `NO_VALID_OBSERVATIONS`（no candidate quads） |
| 11 曲率/nodata_policy 词表 | lib | `ValueError`（决策表） |
| 12 nodata skip 超阈值 | lib | `NO_VALID_OBSERVATIONS`（诚实失败） |

## 独立评审（两轮）

第一轮两个独立评审（科学面 + 工程面）均 approve-with-fixes；处置后本地
全套件 2445 + 治理门 111 passed、四工具 8 面对齐 overall=pass。报告原文与
逐条处置见 INDEPENDENT_REVIEW.md；第二轮复审结论以评审 A 实际返回为准。

## 未运行/替代说明

- 前端 Vitest/typecheck：本 PR **不改动 frontend/**（M5 走既有 MapSpec 通道，
  GeoJSON 3D 坐标为 MapLibre 原生兼容），故无前端测试面变更。
- 线上 CI：按工作单不等待；本地等价门（#9–#12）已覆盖对账与治理。
- 全量 `tests/`（数万级）未整跑：改动面（terrain 域 + 注册表投影 + 生成物）
  的定向回归 #8/#12 + 生成物账本门 #11 已覆盖；live registry audit 对全注册表
  生效（852 passed 含之）。
