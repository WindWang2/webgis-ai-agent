# feat(g31): 跨图层拓扑质量工作台 —— 声明规则 → 权威快照检查 → 稳定问题 → 例外 → 修复 → 复验

## 用户问题

现有质量面只有两类：单数据集样例审计（`QualityPanel`：catalog preview 前 100 条 → inline audit，`sampleOnly` 披露且禁止整库修复）与提交期局部拓扑闸（G14，单次编辑范围）。用户无法声明"地块应覆盖管理区、排除湖面；建筑须被地块覆盖；管线端点须连接设施"这类**跨数据集业务约束**，也没有绑定真实数据版本的问题列表、定位、例外与修复复验闭环。

## 行为增量（对照 base ca7b3ec27）

- **规则作者**：5 类封闭词表规则（`polygons_no_overlap` / `area_coverage`（AOI − ∪coverage − ∪exclusions）/ `objects_covered_by_target`（covered|within 边界政策）/ `cross_layer_no_overlap` / `line_endpoints_connected`（tolerance_m + connectors + 合法出口区））；规则集版本化（内容变更 → version+1 + 新 digest，历史运行持旧 digest 可读）；容差分离建模（线性 `tolerance_m` 与面积 `*_threshold_m2` 不可互换，词表拒绝）。
- **权威快照**：session_ref / project_artifact（durable 修订 + 摘要校验）/ bbox-AOI；G24 选择句柄**提交时**解析为可重放 scope（bbox/where/ids，bbox 持续披露粗超集语义）；complete 角色（coverage/aoi/target/connectors/exclusions/exit_zones）携带 scope 被 typed 拒绝（局部选择 + 全量语义 = 虚假缺口）；执行期逐角色指纹比对，提交后源漂移 → `source_changed_after_submission` typed failed（绝不悄悄换 latest 续算）。
- **科学语义**：关系运算在源 CRS 空间（顶点级 UTM 变换会在共享边界产生伪缝隙——已实测修复）、度量在 UTM 分量级变换（`MeasureSpace`，度量/关系/展示三 CRS 披露）；反经线/极区(>83.5°)/超大范围(>30°×20°) typed blocked；无效/空/null/超复杂几何**剔除并计数披露**（绝不 buffer(0) 洗白，unknown 范围保留）。
- **持久问题实体**：`topology_findings` 稳定身份（规则 digest + 快照内自然身份 + witness 分量 digest；同对要素多片不连通交叠各自独立 key；分页换序不变）；witness 几何走 BlobStore 内容寻址（行内只存 key/sha/bbox），读取带摘要校验与 **stale 判定**（源当前 revision 前进即如实标注，不移动到别的要素）。
- **例外（用户决定面）**：理由必填 + 锚点固化（规则 digest + 源指纹 + locator 身份 + witness digest）；源或规则变化 → 例外 stale，绝不按 bbox/文本相似自动延续；撤销回 open。**工具面不提供接受例外操作**（无状态权限的模型输出不能充当用户例外决定）。
- **修复桥**：候选修复（snap_endpoint / trim_to_target / fill_gap / resolve_overlap）与可提交计划明确区分；归属选择类（缺口填谁/重叠归谁）必须用户提供 keeper，系统不自动判定；真正提交走 **G14 既有链**（`feature_edit.build_and_store_plan` → 用户提案卡确认；CAS/journal/undo/局部拓扑闸全部继承）——本模块绝不另写数据；只读源给 editable-copy/new-ref typed 指引。
- **复验**：同规则集 + 最新源重跑 → finding-key diff；`resolved` 仅当该规则 `computation=full` 且 finding_key 消失（证据确实消失，非修复函数返回 success）；新问题自然出现在新运行。
- **诚实性契约**：运行状态与质量结论分离（applicability / input_completeness / computation / outcome 四维）；全部规则 blocked/unknown → `all_skipped_or_unknown=true`（UI 显式警告，不显示绿）；预算触顶（finding 5k/规则、候选对 200k、wall-clock 120s）→ `partial` + 计数披露；findings total 是**全量计数**（非当前页）；运行复用 = 完整问题集克隆（input_digest 覆盖全部源内容指纹 + 规则 digest + 方法版本；非完整结论不作缓存）。
- **入口**：REST 17 端点（`/api/v1/topology-workbench/*`，全部强制认证）+ 普通 `@tool` × 6（ruleset 配置/运行启动/状态/问题分页/详情/修复预览，tier≤2，有界回执）；前端 `topology` 资产页签（维度分离渲染、筛选分页、相机定位、例外表单、复验按钮）；旧样例审计面板原样保留。

## 复用与范围（不重复造）

- 快照解析复用 G18 `source_resolver` 语义（`read_content` + ArtifactRevision + is_complete）；G24 `handle_store` 提交时消费；度量 CRS 复用 `crs_safety.recommend_metric_crs`；where 谓词复用 `feature_pages` 同源校验；durable jobs / BlobStore / 进度 / 取消 registry 全部既有设施。
- 不改 G14 executor/identity/journal（只读桥接）；不导入 G32 模块；不动 G29/G30 面；旧 `data_quality` DSL 与样例审计零改动（回归全绿）。

## 实际验证（详见 planning/G31-CROSS-LAYER-TOPOLOGY/TEST_EVIDENCE.md）

- **真实 Pi 旅程**（`PI_JOURNEY_E2E=1`，独立 driver 进程：node stub LLM → uvicorn 生产 app → 真实 vendor/pi 子进程）：tool_search 发现 → `topology_ruleset_configure` → `topology_check_run` 成功回执（run_id）→ REST 复核 run completed、findings 全量计数披露、witness Polygon 可读非 stale、**缺口 39,919.6 m² vs 人工 oracle 40,000 ±1%** → 8/8 旅程断言通过。
- **语义 oracle**（20 例）：覆盖缺口 40k m²/湖面排除 90k m² 披露/嵌套 10k m²/共边零面积不误报/越界 9k m² 双源 locator/反经线·极区·超大 typed blocked/病态几何分类剔除/0.5m-vs-2m 端点在 1m 容差区分/出口豁免计数/重跑 key 稳定/预算触顶 partial。
- **API 旅程**（13 例）：完整示范（检查→定位→例外→修复→复验 resolved）+ T12/T13/T14/T15/T16/T18/T19/T20/T21/T23 + all-blocked 不显绿 + 导出回读核验。
- **资源曲线**（10k 级，单进程实测）：跨层 10k×10k 贴邻网格 0.05s（候选 0）/ coverage 10k 面 4.0s（100 缺口）/ 线端点 10k×10k 4.2s（候选 10k，finding 触顶 5k → 诚实 partial）；峰值 RSS 390–420MB。
- **兼容回归**：`tests/data/` 全量 1212 passed + feature_edit 123 + selection_handles + quality（api-docs drift / alembic / migration wiring 全绿；api-docs 已再生成）。
- **前端**：面板 10 例 + 旧面板回归 + i18n key-completeness 全绿；tsc 72 errors = 干净 base 基线（本任务零贡献）。
- **Pre-existing 失败**（2 例，干净 base 同样失败，与本任务无关，已留证据）：`test_api_auth_required`（data_import.py 六路由缺 auth Depends，G17 遗留）、`test_field_compute_review_v1` openapi 快照（快照本身缺 PreviewResponse）。

## 评审与修复

- 独立 reviewer（未参与实现）完成 review → 修复 → 复审：结论见 `planning/G31-CROSS-LAYER-TOPOLOGY/REVIEW.md`（含每轮发现与清偿记录）。

## 迁移与回退

- migration `0105_topology_workbench`：4 张 additive 新表（可重入 DDL guard；downgrade 反序 drop）。无数据迁移、无既有表变更 —— 回退 = `alembic downgrade 0104` + 移除代码。
- 新增路由/工具为纯增量；旧端点契约零变化（api-docs 生成区 diff 只有新增条目）。

## 未测项与边界（如实）

- 真实浏览器 E2E（Playwright）未启动（面板为 jsdom/Testing Library 覆盖）；多副本 Redis/celery worker 未验证（eager 同形路径；G23 同边界）；GEOS 不可中断单步的取消延迟以预算上限约束（120s/规则 + 批间 2s 探针），未做注入实测。
