# G47-DORLING-CARTOGRAM — TEST_MATRIX

> 原则：**每一条都要有实际执行过的证据**。没有跑过的写"未执行"，不写"应当通过"。
> 运行环境：`/home/kevin/projects/webgis/webgis-worktrees/.g37-venv/bin/python`（shapely 2.2.0 / pyproj 3.8.0 /
> weasyprint 70.0）、`rsvg-convert`、node v22.22.2。命令统一 `-p no:randomly -q --no-cov`。

## A. 数量语义（面积 ∝ 数量的前置裁决）

| 能力 | 用例 | 结果 |
|---|---|---|
| 正/零/缺失/非法四类**分列**，零≠缺失，负值不取绝对值 | `test_g47_quantity_semantics.py`（32） | ✅ |
| 布尔先于 int 判定（`True` 不是 1） | 同上 | ✅ |
| rate/密度/混合量纲拒绝（`RATE_OR_MIXED_UNIT`） | 同上 + `test_g47_contract_errors.py` | ✅ |
| 无正值 → `NO_VALID_QUANTITY`；全零 → `ZERO_TOTAL` | 同上 | ✅ |
| 极端跨度拒绝（边界 1.01× 通过 / 0.99× 拒绝，可复算） | 同上 | ✅ |

## B. 地理资格与邻接

| 能力 | 用例 | 结果 |
|---|---|---|
| 身份/几何有效性/轴序猜测/支持矩阵（跨度 ≤180°、\|lat\| ≤66.5°） | `test_g47_geography_eligibility.py`（22） | ✅ |
| 共享边邻接（STRtree + 最小共享长度），角点相触不算邻接 | 同上 | ✅ |
| 非面要素/空几何/重复 id 等拒绝路径 | 同上 | ✅ |

## C. 求解与确定性

| 能力 | 用例 | 结果 |
|---|---|---|
| Jacobi 更新：行序不影响结果 | `test_g47_solver_determinism.py`（16） | ✅ |
| 同输入两次 → 只允许 `elapsed_s`/`layout_fingerprint` 差异 | 同上 + `test_g47_layout_artifact.py` | ✅ |
| 每轮可取消，取消以 typed 异常穿出管线 | 同上 | ✅ |
| 收敛判据 = 位移变小 **且** 无超阈重叠（`stalled_with_overlap`） | 同上 | ✅ |

## D. 独立质量 oracle

| 能力 | 用例 | 结果 |
|---|---|---|
| 面积相对误差按**落盘半径**复核（不恒为 0） | `test_g47_quality_oracle.py`（18）+ `test_g47_review_regressions.py` | ✅ |
| 交叠 lens 面积解析式（与数值积分对拍） | 同上 | ✅ |
| 两道重叠门互不派生；`max_pair_overlap_ratio` 报真实最大值 | `test_g47_review_regressions.py::test_fraction_gate_is_not_dead_and_reports_true_max` | ✅ |
| 位移只作披露，不参与 pass/fail | `test_g47_quality_oracle.py` | ✅ |
| 邻接保留/丢失/新增接近对统计 | 同上 | ✅ |

## E. 布局 artifact（坐标隔离 / 指纹）

| 能力 | 用例 | 结果 |
|---|---|---|
| `coordinate_space=diagram`、`units=diagram_unit`、`is_geographic=false` | `test_g47_layout_artifact.py`（25） | ✅ |
| 无 CRS / 无经纬度圆心 / 无伪 GeoJSON（键集与值域双向断言） | 同上 | ✅ |
| 参考轮廓落在画布内、保留九宫格相对形状 | `test_g47_review_regressions.py` | ✅ |
| 指纹可从 artifact 复算且等于回填值（无自引用） | `test_g47_layout_artifact.py` | ✅ |
| 落盘精度一处量化：`min_radius` 与 `sqrt(k·min_value)` 一致 | `test_g47_review_regressions.py` | ✅ |
| 大明细有界（圆上限、参考点数上限、`truncated` 如实） | `test_g47_layout_artifact.py` | ✅ |

## F. 导出三格式（真实内容，独立回读）

| 能力 | 用例 | 结果 |
|---|---|---|
| SVG：XML 解析出真实圆集合/半径/面积比/图例/披露/参考 path 在画布内 | `test_g47_publication_readback.py` | ✅（14 passed，见 §J 复跑记录） |
| PNG：PIL 读像素，橙色覆盖面积 vs `π·s²·Σr²`；96/300 DPI 面积比 = (300/96)² | 同上 | ✅ |
| PDF：pypdf 页几何 A4 横向 + zlib 解压内容流确认矢量填充与曲线算子 | 同上 | ✅ |
| 图例参考圆保持半径比例（无逐圆钳位） | `test_g47_review_regressions.py` | ✅ |

## G. 纸面 `diagram` 接缝（G34）

| 能力 | 用例 | 结果 |
|---|---|---|
| schema 严格校验；缺 spec 拒绝；`crs`/`bbox` 混入被拒 | `test_g47_print_diagram_seam.py`（10） | ✅ |
| 单一 uniform scale（等比 contain，纸面面积比不变） | 同上 | ✅ |
| 参考轮廓与圆套同一 `scale/origin`（同一框内） | `test_g47_review_regressions.py` | ✅ |
| HTML 内联 SVG data URI 自包含 + 转义 | `test_g47_print_diagram_seam.py` | ✅ |

## H. 服务生命周期（同代 / CAS / 诚实门 / 导出端点）

| 能力 | 用例 | 结果 |
|---|---|---|
| 全链 inspect→plan→run→status→apply→export | `test_g47_service_lifecycle.py`（9） | ✅ |
| 二次 apply 记 `previous`（undo 可用） | 同上 | ✅ |
| `expected_revision` 不符 → `CAS_SUPERSEDED`，layer 无证据块、revision 不变 | 同上 | ✅ |
| 未达门 + 无 `allow_diagnostic` → `QUALITY_GATE_FAILED`；显式草稿 → `diagnostic=true` | 同上 | ✅ |
| 源代次变化 → `STALE_SOURCE`；显式 acknowledge → 记 `stale_source=true` | 同上 | ✅ |
| 跨会话 → `LAYOUT_NOT_FOUND` / `JOB_NOT_FOUND` | 同上 | ✅ |
| 幂等重放：同 `mutation_id` 不产生新提交（`duplicate` 透传） | 服务面 + 回执字段 | ✅ |

## I. Pi 真实接入（子进程 + 生产扩展 + 生产 dispatch + 本域服务）

| 能力 | 用例 | 结果 |
|---|---|---|
| 6 个工具全部注册、模块加载零异常 | `test_g47_review_regressions.py` | ✅ |
| 端到端旅程（stub LLM SSE → uvicorn(app) → `/chat/stream` → `tool_search` → 6 工具 → 回读 artifact/层/job/导出） | `tests/integration/test_pi_g47_dorling_cartogram_journey.py` | ⚠️ 已编写，本轮**未执行完**（重阶段锁被其它 worktree 长时间占用；夜间 `quality-e2e` 会覆盖） |

## J. 前端

| 能力 | 用例/命令 | 结果 |
|---|---|---|
| 等比图面数学（`fitContain`/统一 scale，禁非等比） | `lib/cartogram/geometry.test.ts` | ✅ |
| 图面交互（命中/键盘/空状态/示意坐标提示） | `components/cartogram/dorling-diagram.test.tsx` | ✅ |
| Vitest 合计 | `vitest run lib/cartogram components/cartogram --maxWorkers=2` | ✅ 19 passed |
| 类型 | `tsc --noEmit` | ✅ 0 错 |
| lint（含 CJK 门禁） | `eslint … --max-warnings 0` | ✅ 0 错 0 告警 |
| i18n 键完备（zh/en 同键集、占位符一致） | `vitest run test/i18n` | ✅ 4/5 通过；`no-raw-cjk` 的失败项全在 base 未触碰文件 |

## K. 已知未执行 / 残留

| 项 | 状态 | 说明 |
|---|---|---|
| 真实浏览器 E2E（缩放/平移/选择联动/键盘） | ⚠️ 未执行 | 需 Playwright 1 worker + 起 dev server；重阶段锁与时间预算内未跑 |
| 接近上限的大样本（400 单元 / 8000 边） | ⚠️ 未执行 | 参考轮廓 9000 点上限 + 简化容差会随规模变化，需一次真实压测 |
| `stop_reason=time_budget` 下的逐位一致性 | ⚠️ 边界未声明 | 慢机会得到不同布局与指纹；`elapsed_s` 已在 `_VOLATILE_KEYS`，`stop_reason` 未纳入 |
| `scripts/check_generated_staleness.py` 报 `docs/quality/*` 陈旧 | ⚠️ base 既有债 | 与本分支无关（未触碰），不在此修复 |
