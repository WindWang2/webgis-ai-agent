# feat(multiscale): GOAL 04 多尺度地理综合、比例尺表达与专业图件一致性

## 目标

在不破坏专题统计、拓扑、图斑边界与用户手动设置的前提下，为 MapSpec 建立
「语义重要性 → 尺度分级 → 几何表现/要素取舍 → 版面约束 → 导出镜像」的
非破坏性多尺度表达层：v1.6 representation 协议中 `generalized`/
`aggregate_grid` 的 capability seam 从「声明即信」升级为**真实工件生产 +
窗口源覆盖消费**闭环。

- 分支：`zcode/goal-multiscale-cartography-v1`（worktree `webgis-wt-goal-multiscale-carto`）
- master 起点：`6c5e71c2c`（2026-10-31，PR #1875 merge）
- **版本号协调**：open PR #1877（GOAL 03，先开）已用 1.8=时态层 —— 本 PR
  登记 1.8 为协调占位（语义归 #1877），**1.9 = 多尺度表达档案**；任意合并
  顺序不撞号。

## 真实差异（新增 vs 复用）

**新增**（所有权 `app/lib/cartography/multiscale/`、`frontend/lib/map-kit/multiscale/`）：
- `profile.py`：ScaleRepresentationProfile.v1 纯数据契约（双基准比例尺
  display 512px / computation 256px 显式分口径、带计划 4 策略块、统计
  口径、user overrides、语义检查、确定性指纹）
- `importance.py`：语义重要性规则引擎（词表全匹配 fail-honest —— 道路
  等级中英双语别名 + 行政级别 0-5；缺字段宁不取舍不伪造主干）
- `generalize.py`：拓扑安全几何综合（bounded_simplify 逐要素不变量复核
  + 回退；shared_boundary_protect 锚点守卫；`generalize_features_multiband`
  单遍多带；numpy 向量化投影）
- `plan.py`：带计划构建器（importance × generalize × SCALE_TIERS 编排）
- `export_adjudication.py`：window.source 唯一裁决读取面
- `semantic_checks_scale_profile.py`：carto.scale.profile QA
- 服务 `app/services/gis_harness/scale_profile/`（inspect/preview/apply：
  双载体解析、顺序 CAS 链、幂等回执环）+ 工具
  `app/tools/scale_profile_tools.py`（ToolRegistry 三工具）
- ADR-0279

**受控扩展**（最小适配）：
- MapSpec v1.9 additive：`MapSpecRepresentationWindow.source` +
  `layer.scale_profile` + 迁移注册（mapspec_schema.py）+ ts_projection 再生
- `mapspec_to_svg.py`：src_id 解析点 4 行窗口源覆盖 + 诊断
- 前端 `carto-ir/representation.ts`（归一携带 source + windowAtZoom）、
  `compiler.ts`/`runtime.ts`（克隆子层 source 覆盖，各 3 行）、
  `mapspec-to-svg.ts`（导出同裁决）
- `render_diagnostics.py`：`layer_generalized_artifact` 码（双端发射注册）
- semantic_checks 接线 3 行

**复用不重写**：representation.py（只读）、scale_rules/SCALE_TIERS、
symbol-law.ts（只读 import）、label 引擎零改动、MVT 零改动、
#1870 candidates 零改动、#1874 事务面复用（apply_gis_mutation + 回执环模式）。

## 里程碑

M0 基线（2728a2f）→ M1 契约+v1.9（c5f70c7）→ M2+M3 引擎（0e5c354）→
M4 构建器（364a791）→ M6 同合同（26ab4e8）→ M7 工具闭环（a4db0d6）→
M5 QA（f9d0753）→ M8 基准（a5fb9277）→ M9 三场景+ADR（bb35b5c）→
版本协调（07715c0）→ 评审清偿（见 INDEPENDENT_REVIEW.md）。

## 测试输出与未跑项

- 后端：cartography 全套 + scale_profile 服务/工具 **3080 passed / 2
  skipped / 3 xfailed**；唯一红灯 `test_mutation_parity_corpus[
  refresh_data_revision_stamp]` 为 **master 既有**（stash 对照证实）
- 前端：vitest（carto-ir/mapspec-compiler/map-kit-multiscale/
  mapspec-runtime/symbol-law）**714+444 passed**；`tsc --noEmit` 0 错误
  （tsconfig.test.json 3 错误为 master 既有，vnext 同基线对照证实）
- 性能（`MSP_PERF=1` 显式，同机 453s 全程）：
  - 50k 线要素：13.43s（纯耗时口径）/ 峰值 92.6MB（tracemalloc 口径）
  - 200k 线要素：56.1s；确定性复跑同输出；取舍比例与词表秩一致
  - 优化前基线 10k=44.2s → 优化后 3.2s（13.6×，复现脚本=同测试）
- 未跑（not_evaluated 诚实披露）：Playwright 视觉回归（环境无浏览器
  依赖）；MapLibre live 真浏览器交互；跨 pod Redis 并发

## 科学正确性证据

- 双基准换算与 frontend `meters-per-pixel.ts` 常数级一致（512·2^z）；
  同 zoom 与 scale_rules 256px 基准恰 2 倍（契约测试）
- 拓扑不变量族全绿：无自交引入/洞保持/面积误差 ≤2%/共享边界覆盖面积
  相对误差 ≤8%/多部件保持/主干道路（高速+国道+省道）1:500000 不丢
- 非破坏性：apply 后主源条目逐字节快照相等；工件恒为新 ref；输入要素
  非变异（测试锁定）
- 统计口径：classification_basis 恒 original_source；legend breaks 指纹
  入档案；三尺度导出填色/图例逐字节一致（M9 场景 3）

## Review-fix-review

独立评审 R1（对抗性，含数学正确性/契约并发/测试真假）findings 与清偿
映射见 `planning/goal-multiscale-carto/INDEPENDENT_REVIEW.md`；修复后
R2 复审通过记录同文件。

## 风险与已知边界

- `aggregate_grid` 工件本期未实现（词表保留，seam 语义不变 —— ADR-0279）
- 共享边界保护是锚点级（顶点存活）非弧级拓扑重建（topology_risk 披露）
- filter 缺陷族 #1809/#1810/#1811 open：本 PR 绕开（取舍在工件期物化），
  不认领修复
- 与 #1877（v1.8 时态层）零文件交集冲突：mapspec_schema.py 的
  KNOWN_VERSIONS 文字冲突已按「1.8 协调占位」注解预先消解

## issue/PR 重叠排除矩阵

| 对象 | 关系 |
|---|---|
| #1876/#1877/#1878/#1879（open） | 零功能交集；仅 #1877 有 mapspec_schema.py 版本词表文字交叠（已协调） |
| #1868-#1875（merged） | 只读复用（vocabulary/aggregate/事务模式），不改其行为 |
| #1809/#1810/#1811 | 绕开不修（运行时不改 layer.filter） |

## 如何手工验证

```bash
pytest tests/cartography/multiscale/ tests/unit/gis_harness/scale_profile/ -q --no-cov
MSP_PERF=1 pytest tests/cartography/multiscale/test_topology_perf_v1.py::TestPerformanceBenchmarks -s --no-cov
cd frontend && npx vitest run lib/map-kit/multiscale/ lib/carto-ir/ --maxWorkers=2
```
