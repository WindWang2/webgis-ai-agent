# LOOP_STATE — 04｜多尺度制图综合与专业地图表达引擎（20261124）

## 环境事实

- worktree：`/home/kevin/projects/webgis/webgis-carto-multiscale-generalization-20261124`
- 分支：`feat/carto-multiscale-generalization-20261124`
- 基线：origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（PR #2266 合并后）
- vendor/pi：符号链接（指向历史 worktree 路径，本任务不改 gitlink、不依赖 Pi vendor 源码变更）
- Python：复用 `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（shapely 2.2.0 / pydantic 2.14.2），
  在本 worktree 目录内跑 pytest（pythonpath=. 指向本树）
- gh 可用；open PR：#2272（02 科学方法实验引擎，兄弟任务）、#2267-#2271（dependabot）；无 open issues
- 兄弟 worktree：01/02/03/05 四个（只读参考公开提交，不碰工作区）

## 去重矩阵（master 已有 → 不重做）

| 已有能力 | 位置 | 状态 |
|---|---|---|
| 缩放带多尺度档案 ScaleRepresentationProfile v1（[0,24) 四带） | app/lib/cartography/multiscale/profile.py | 已合并（M1） |
| 语义重要性 RuleEngine（road_class/admin_level 词表） | multiscale/importance.py | 已合并（M2） |
| 几何综合：bounded_simplify + shared_boundary_protect（顶点锚点） | multiscale/generalize.py | 已合并（M3） |
| 带计划构建器 + 统计口径 | multiscale/plan.py | 已合并（M4） |
| QA 语义检查（结构类） | multiscale/semantic_checks_scale_profile.py | 已合并（M5） |
| 导出窗口源裁决 window.source（后端+TS 孪生） | multiscale/export_adjudication.py + mapspec_to_svg + frontend/carto-ir | 已合并（M6） |
| Pi 工具 inspect/preview/apply_scale_representation | app/tools/scale_profile_tools.py | 已合并（M7） |
| 50k/200k 性能路径（单遍多带） | generalize.py + test_topology_perf_v1 | 已合并（M8） |
| G27 scale_portrayal（zoom→标注/paint 策略） | app/lib/cartography/scale_portrayal.py + services | 已合并 |
| G34 print_layout.v1（纸面版式 mm 几何） | app/lib/cartography/print_layout_schema.py | 已合并 |
| 出版 IR / atlas / SVG-PNG-PDF 导出 | publication_ir.py 等 | 已合并 |

## 本轮 delta（gap → 新增行为）

1. **G1 纸面目标比例尺契约**（WP1）：现有综合全部 zoom 带驱动（屏幕）；无
   「1:50万 + A4 + DPI + 用途 → 地面分辨率/容差/最小尺寸」的纸面契约。
   → `multiscale/paper_scale.py`（PublicationScaleContract，纯函数 + 单位换算单一入口）。
2. **G2 真拓扑一致共享边综合**（WP2）：现有锚点保护只钉共享**顶点**，锚点间的
   共享边两侧各自简化仍可产生缝隙/重叠。→ 边链级共享边一次简化、两侧重建，
   oracle：派生集合两两 intersection≈0 且 union 覆盖差 ≤ 容差。
3. **G2b 面小面积 eliminee/merge**（WP2）：低于目标尺度最小可辨面积的面合并进
   邻接（共享边最长者），带成员归属与计数守恒披露；不可满足时回退原始表达。
4. **G3 网络连通性保持线取舍**（WP2）：现有 class 取舍可断网。→ 端点吸附建图、
   junction 度不变量、被抑制段的连通性修复（最小重加）或诚实披露断连。
5. **G4 点要素可追溯聚合**（WP2）：现有去密只切渲染模式（无工件、无成员）。
   → 确定性格网聚合工件（代表几何 + 成员 id + 计数 + 可选字段和），display-only 标记。
6. **G5 固定比例尺出版冻结编译**（WP3）：综合计划 → 出版工件以 additive 命名空间
   落 layer.scale_profile，导出 zoom 经既有 window.source 裁决取冻结工件；
   不动 zoom 窗口语义。
7. **G6 综合 QA pack**（WP4）：几何合法/共享边无缝/连通性/计数守恒/覆盖差/最小
   显示尺寸/位移界，接 evaluate_cartography_semantics。
8. **G7 Pi 工具 + 前端审阅面板**（WP5）：paper scale 计划/应用工具 + 工作台面板。
9. **G8 golden 场景 + 性能复测 + 文档**（WP6）。

## Owned paths（本轮）

- 新：`app/lib/cartography/multiscale/paper_scale.py`、`topology.py`、`network_select.py`、
  `point_aggregate.py`、`polygon_eliminate.py`、`qa_pack.py`、`publication_compile.py`
- 新：`app/services/gis_harness/paper_generalization/service.py`（或贴近 scale_profile service 模式）
- 新：`app/tools/paper_scale_tools.py`
- 改（最小接线）：`app/lib/cartography/semantic_checks.py`（或其 evaluate 入口）、
  ToolRegistry 注册面、前端工作台面板 + carto-ir 类型（如需）
- 不碰：Pi vendor、02/03/05 owned paths、既有 multiscale 三档策略语义、
  representation.py 词表、G27/G34 既有面

## 测试基线

- tests/cartography/multiscale/ 全量：后台运行中（含 50k/200k 性能锁）
- master 既有红灯（沿用 LOOP_STATE 旧记录，待本轮复证）：
  test_mutation_parity_corpus[refresh_data_revision_stamp]；前端 tsconfig.test 3 错误

## 轮次记录

### R0 / Phase 0（侦察 + 基线）
- fetch + PR/issue 清单 + worktree 创建（见上）；读取 multiscale 全模块 +
  scale_profile service + G27/G34 面接缝；差异矩阵与本轮 delta 定稿。
- 基线红灯（纯净 master `8fe3d7663` 逐字复现，不认领）：
  1. `tests/cartography/multiscale/test_importance_v1.py::test_admin_level_
     numeric_ordering`（陈旧期望，#2225 后权威语义为字符串词表）→ 本任务
     owned area，已修复（commit 248c9e3e2）。
  2. `test_carto_ir_corpus_v1.py::...[scale_portrayal_cases]`（KeyError 'spec'）
  3. `test_component_variants_v4.py::test_v4_variant_count_target` +
     `::test_v4_bidirectional_variant_template_coverage`
  4. 前端 `tsc -p tsconfig.test.json` 5 错误（flow-product-panel ×4 +
     live-spec ×1）—— 本轮前端零新增错误。

### R1 / WP1-WP5 实施（全部原子 commit）
- commit 1: 陈旧 admin_level 测试对齐（基线清偿）
- WP1 paper_scale.v1 契约（21 测试）
- WP2a topology.py 共享边一致综合（12）｜WP2b polygon_eliminate（13）
- WP2c network_select 连通性桥接（11+）｜WP2d point_aggregate（10）
- WP4 qa_pack（16）+ carto.paper_scale_plan 语义检查接入（10）
- WP3 publication_compile（10）+ paper_generalization 服务（11+2 旅程）+
  导出旅程（真实 SVG 双 zoom 证据，2）
- 附带清偿：scale_profile service inline 无属性 descriptor 崩链（本任务
  测试暴露的共享路径潜在缺陷，最小修复单独 commit）
- WP5 Pi 工具 ×3（7）+ REST 路由对（3）+ 前端 PaperScalePanel（5 vitest）
- 引擎修复记录：桥接明细帽误停主循环（帽只截断回执明细）；面积口径
  外环−洞；链保护传播需重建全部引用者
- 验证状态：multiscale+paper_generalization+paper-scale-routes 221 passed；
  cartography 全量 3781 passed / 3 master 既有红灯；前端双 tsconfig 零新增

### R2（待办）
- WP6 场景 golden + heavy 性能数字（跑批中）+ EVIDENCE 记录
- 独立 review（subagent C 只读全量 diff）两轮 + 修复
- push + PR
