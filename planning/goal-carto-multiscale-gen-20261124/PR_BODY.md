# feat(carto-multiscale-generalization-20261124): 04｜多尺度制图综合与专业地图表达引擎

## 基线与范围

- **基线**：origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（#2266 合并后，执行时最新）
- **分支**：`feat/carto-multiscale-generalization-20261124`（独立 worktree，25 commits）
- **Pi pin**：未改动（vendor/pi 为符号链接，本任务不依赖 Pi 侧变更）
- **去重**：master 已有 zoom 带多尺度档案（scale_profile.v1，M1-M9 已合并）、G27 scale_portrayal、G34 print_layout、publication_ir 导出链 —— 全部复用不重做；本轮增量 = **固定纸面比例尺驱动的出版综合**（对齐目标书 §4 独占边界：制图综合策略/算法、派生制图数据、分尺度表达、综合专属 QA 与 export parity）。
- 与兄弟 PR 无文件交叠（#2272/#2273 属 02/03 任务；dependabot 仅锁文件）。

## 用户新增行为（全部真实链路 + 回归锁定）

### WP1 纸面目标比例尺契约（`multiscale/paper_scale.py`，21 测试）
- `PublicationScaleContract`：1:N × ISO A 系页面(mm×DPI) × 用途 × 投影声明 × 硬约束 × 误差预算；单位换算单一出口（m/mm、m/px、`equivalent_display_zoom` 唯一衔接 zoom 带引擎）；Web Mercator 纬度变形诚实披露（锚点分母 N·cos(φ)/cos(φ_ref)，南北边缘变化比 ≥5% 强制披露）；「1:50万」「A4横版」纯函数解析。

### WP2 四个综合引擎（86 测试）
- **拓扑一致共享边综合**（`topology.py`）：共享边链（结点顶点切分）只简化一次、两侧重建同一坐标 ⇒ 逐字节一致不可能缝隙；回退传播（链级全或无）；顶点未配齐数据诚实披露，真实缝隙交 QA 距离审计。
- **面小面积消除/合并**（`polygon_eliminate.py`）：低于目标尺度最小可辨面积按「共享边界最长」确定性并入；sum_fields Σ守恒（链式吸收增量不丢失）；未声明数值字段不平均不臆造权重；孤立小面保留披露。
- **网络连通性取舍**（`network_select.py`）：等级取舍 + 全队列 BFS 桥接（挂空枝不加、绕行链可修）；预算尽/无路径披露断连分量数。20k 段路网桥接 13,333 段 1.29s 全连通。
- **点要素可追溯聚合**（`point_aggregate.py`）：锚定 (0,0) 格网（数据集无关）、成员 id 全量保留、计数/Σ守恒、display-only 强制披露。

### WP3 出版冻结编译与会话服务（`publication_compile.py` + `paper_generalization/`）
- 纯函数管线（面：消除→拓扑简化顺序不可换；线：用途保留秩+桥接；点：5mm 格聚合）+ `paper_scale_plan.v1` 冻结计划块。
- 服务：QA **fail-closed** 门（blocking>0 拒绝冻结）→ 工件新 ref → UpsertSource+UpsertLayer 顺序 CAS（partial 断点续链复用工件 ref）→ 冻结计划落 `layer.scale_profile.paper_plans` + 表达窗口精确裁剪插入（跨度外保持原始数据）→ 回执环幂等。
- 导出同合同：真实 SVG 双 zoom 旅程测试证明纸面 zoom 走冻结工件、窗外走原始数据、原始 source 逐字节不变。

### WP4 QA pack 与语义检查（26 测试）
- 独立 oracle（不信任引擎自述）：几何有效性、逐对重叠 + **邻接缝隙距离审计**、最小可辨尺寸、计数单调 + sum 守恒、连通性（用途硬/软）、比例尺一致性+变形披露；`carto.paper_scale_plan` 语义检查接入 `evaluate_cartography_semantics`（计划↔窗口↔工件源跨面一致，无声明层零行为变化）。

### WP5 三面入口（同一服务）
- **Pi 工具**：`webgis_inspect/preview/apply_paper_scale_generalization`（ToolRegistry，tier=2 受控写，failure_modes 含 qa_blocked）。
- **REST**：`GET/POST /api/v1/chat/sessions/{sid}/paper-scale`（require_owned_session；preview 零突变；commit QA 门 + CAS）。
- **工作台面板**：`PaperScalePanel`（hud，图层样式面板内）—— 冻结计划清单/预览 QA 摘要/冻结提交，qa_blocked 如实呈现，epoch 守卫；i18n zh-CN/en-US 齐备。

### WP6 场景/性能/文档
- 同城多族 golden（双比例尺行政区无缝守恒、路网连通、小学聚合对账、脏数据 QA 拒绝、硬约束保小县、全管线确定性）。
- 性能实测（`MSP_PERF=1` 显式跑，EVIDENCE.md）：20k 桥接 1.29s / 506 面消除+拓扑 0.09s / 5k 点聚合 0.01s。
- 文档：`docs/cartography/paper-scale-generalization.md`（单位纪律/管线/QA 门/三入口/已知边界）。

## 独立评审与清偿

- **R1**（独立只读评审员，全量 diff + 自写脚本复现）：结论「不可合并」，P0×2 + P1×4 + P2×7 + P3 若干。两处 P0 为**评审员实际复现**的引擎缺陷（topology 环起点在共享边反向遍历恒伪回退；MultiLineString part 摊平幻影边使 navigation 硬门 fail-open）。
- **R1 清偿**（commit `f1cefc0af`）：P0×2 各带回归测试、P1×4（链式吸收 Σ守恒、纬度分母方向修正+锚点、硬约束用户面全链透传、邻接缝隙距离审计）、P2×5（stats_match 双计、缺省 CAS 基线、partial 续链复用工件 ref、米空间线长、page.dpi 锚定、preview 复用编译）、P3 廉价项（回执参数指纹、REST 兜底 500、assert 炸弹移除、dpi 保留原值交 validate、helper 收敛单源、文档漂移、面板 epoch 守卫）。
- **R2 复审**（同一评审员，重跑其 R1 复现脚本逐条核对）：结论「**有条件可合并**」——P0×2 与 P1-1/1-2 确认真实清偿（独立脚本复跑通过），结点切分顺带治愈了 R1 未点名的共享链空洞配对与 T 结点裂缝风险；相邻生产路径（scale_profile、intents CAS 链、导出旅程）无伤害。两项合并条件（N1 消除路径缝隙审计静默跳过、N2 keep_values 硬约束假保障）+ P3 N3-N6（指纹参数缺口、gap 距离口径、inspect dpi 一致性、resume/keep_region 测试）**已在后续 commit 全部就地清偿**（「fix(multiscale): 独立评审 R2 清偿」）。

## 本地验证（真实命令与结果）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 本任务回归 | `pytest tests/cartography/multiscale/ tests/unit/gis_harness/{paper_generalization,scale_profile}/ tests/unit/api/test_paper_scale_routes.py --no-cov` | **254 passed, 4 skipped**（R2 清偿后 238 passed 复核） |
| cartography 全量 | `pytest tests/cartography/ --no-cov`（deselect 4 个基线红灯） | **3843 passed** |
| 前端单测 | `vitest run components/hud/paper-scale-panel.test.tsx components/hud/layer-style-panel*.test.tsx test/i18n/{workbench-panels,key-completeness}.test.tsx` | **19 passed** |
| 前端 typecheck | `tsc --noEmit -p tsconfig.json` / `-p tsconfig.test.json` | 主 config **0 错误**；test config 5 错误为 master 既有（纯净基线逐字复现） |
| Ruff | `ruff check`（本任务全部 owned paths） | clean |
| heavy 性能 | `MSP_PERF=1 pytest -k TestPerfHeavy` | 3 passed（数字见 EVIDENCE.md） |
| production build | `next build --webpack` | **exit 0**（全部路由编译成功） |

**master 既有红灯（纯净 `8fe3d7663` 基线逐字复现，不认领、未改动）**：
1. `test_importance_v1::test_admin_level_numeric_ordering` —— 陈旧期望（#2225 后权威语义为字符串词表）；属本任务 owned area，已单独 commit 修复（`248c9e3e2`）。
2. `test_carto_ir_corpus_v1::...[scale_portrayal_cases]`（KeyError 'spec'）
3. `test_component_variants_v4::test_v4_variant_count_target` + `::test_v4_bidirectional_variant_template_coverage`
4. `test_component_variants_v3::test_every_descriptor_variant_has_template_entry`
5. 前端 `tsc -p tsconfig.test.json` 5 错误（flow-product-panel ×4 + live-spec ×1）

## 附带清偿的共享路径缺陷

`scale_profile/service.py` inline 载体无属性要素的 descriptor 构建崩链（`collect_field_schema` 返回 `(None, True)` → `dict(None)`）—— 本任务测试以真实脏数据暴露的潜在缺陷，最小修复单独 commit（`3cb05e1f2`）。

## 未执行 / not_evaluated（诚实声明）

- **真实浏览器 journey**（断连重连、截图视觉对照）：无浏览器环境未执行；导出证据为真实 SVG 编译 + 结构化 feature/legend/诊断断言（`test_paper_export_journey_v1`），视觉对照标 not_evaluated。
- **线上 CI**：未等待（本地可复现证据为准，不改 workflow、不动保护策略）。
- **栅格族**：不进本通道（走既有重采样/金字塔能力，文档明示）。
- 万级面以上的 QA seam 审计为 O(n²)（bbox 短路实测 2M 对 ≈1.5s；1 万面分钟级）——规模帽未实现，列为后续跟进项。

## 与其他四条线的集成点（最小）

- 02（科学实验）：其 artifact 可作为本通道输入 ref（既有 ref/descriptor 契约，无代码耦合）。
- 05（候选方案治理）：仅通过既有 MapSpec mutation/preview 消费冻结结果，无新接口。
- Pi 宿主：零改动（工具经既有 ToolRegistry 注册面 additive 一行接线）。
