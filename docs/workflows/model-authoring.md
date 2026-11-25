# Model Authoring —— 可视化 GIS 模型构建器与可复用参数化模型

把一次对话分析沉淀成**用户可理解、可编辑、可反复运行、可验证、可发布**的
GIS 模型。模型作者域（model authoring）是既有权威之上的**用户面**：
不重写 runtime、不建第二 registry、不造平行状态机。

## 三层契约（不复制执行态）

```text
ModelDocument（作者草稿；CAS 修订；可变）
  │  validate_document  → typed findings（20 码，error 阻断发布；端口规则=运行时绑定门忠实镜像）
  │  compile_document   ↓
WorkflowPackage（不可变；umodel_ 前缀；canonical 指纹；semver）
  │  PackageRegistry.register / publish（既有 draft→published→deprecated）
  ↓
Instance（既有 workflow_runtime：绑定门/波次 driver/复用指纹/重算/inspector）
```

- **模型只保存声明/版本/ref**：草稿表（`model_drafts`）存文档与 CAS 修订；
  发布仅记指针（`latest_published_*`）；运行态一律读既有实例投影。
- 布局坐标（`layout`）是纯 UI 事实，**不参与科学指纹**（compile 显式剔除）。

## ModelDocument v1（词表摘要）

| 字段 | 约束 |
| --- | --- |
| `nodes[].kind` | `data_input / transform / analysis / cartography / output`（封闭词表） |
| `nodes[].node_id` | `^[a-z][a-z0-9_]{0,28}$`；transform 的 wire id = `transform:<op>:<slug>`（driver 算子解析唯一事实源；slug ≤29 保证 wire 边标签精确可逆） |
| `nodes[].role` | ⊆ `DATA_ROLES`（16 词表，`workflow_schema` 单一事实源） |
| `nodes[].capability` | ⊆ CapabilityRegistry 且 status=native（非 native 由 finding 阻断发布） |
| `nodes[].transform_op` | ⊆ `buffer/clip/dissolve/overlay/filter/reproject`（= driver geocompute 已接线词表，诚实子集） |
| `edges[]` | 端口到端口；**data_input 源端口名 = role 名**；to_port 空 = 主输入 |
| `params[]` | ≤16；kind ∈ number/integer/string/enum/boolean/field/unit/time_window；default 有界（标量/≤8 标量表） |
| 节点参数引用 | 值字符串 `"$param:<name>"` → 运行时经 `node_params` 注入既有 driver 合并点 |
| 上限 | 节点 ≤32（编辑器；runtime 64 余量给编译扩展）、边 ≤64、输出 ≤8、文档 ≤256KB |

端口兼容（作者面静态近似；运行时绑定门以真实 descriptor 复核）：
宽类型 `feature_collection` 双向可逆；analysis 输入缺省宽类型；output 汇点
空 artifact_type + required=False（收口点不做基数门——descriptor 契约
非 FC 载荷 feature_count 恒 0）。

## 版本与发布（受限声明式项目 scope）

- 发布 = compile（零 error 才可）→ `WorkflowPackage(package_id="umodel_<slug>")`
  → 既有 PackageRegistry `register(draft)` → `publish`（semver
  major/minor/patch 显式推进，`next_version` 拒绝非法 change）。
- 包不可变：历史版本指纹永不改写；重发布 = 新版本行（指纹幂等）。
- 可见性 = 既有 owner/org 域（`owner_scope_for` 哈希派生）：**无全局可见
  通道**——他人 owner 解析不到（不泄露存在性）。跨用户共享沿用平台既有
  治理边界（extension marketplace 是运维面），本域不另开旁路。
- 撤回 = PackageRegistry 既有 `deprecated` 语义；实例可继续解析已发布版本
  （不可变证据），但不再出现在默认解析（resolve 最新发布版跳过 deprecated）。

## 运行（全部复用既有 runtime）

`instantiate`（包解析+兼容硬门）→ `ModelInstanceBinder`（data_input 预绑：
ref 必须本会话可读、`REF_NOT_FOUND` 不泄露、幂等/改绑拒绝）→
`run_instance(node_params=...)`（既有 driver：绑定门/复用指纹/重试/取消/
补偿/子工作流/计划准入）。参数覆盖 = 逐节点 wire id 注入（transform 节点
键 = `transform:<op>:<slug>`）；运行解释/局部重算/事件 =
既有 `/workflow-runtime/instances/{id}` 的 events / recompute-plan /
nodes retry/cancel / clone（本域零重复端点）。

## 入口

- REST：`/api/v1/model-authoring/*`（capabilities / 草稿 CRUD+CAS /
  validate / publish / instantiate / run / catalog / export / import）
- Pi 工具（与 REST 同一 service 链路）：`webgis_model_builder_*` 8 个
  tier-2 工具；`patch` 只收白名单 op（add/remove node/edge、set_param、
  set_layout、set_meta），无自由代码/SQL/网络面
- 前端：模型构建器 rail tab（画布/palette/参数面板/findings/运行 overlay）
- 导入导出：`{"format":"webgis-model","format_version":"1",document}`；
  导入只建草稿，**绝不自动执行**；导出不含 layout/项目 id/用户信息

## 内置领域模板（WP6）

`app/services/model_authoring/templates.py`（每模板 docstring 记录领域
语义、科学边界与算子选型理由）：

| 模板 | 结构 | 科学边界 |
| --- | --- | --- |
| `facility_admin_count` | 设施点 → admin_aggregation → 统计 + 专题图 | 计数≠密度；密度结论需 denominator 角色 |
| `facility_service_area` | 设施 → buffer($distance_m) → overlay(boundary) → 分类统计 | 服务区是近似（欧氏缓冲≠路网可达） |
| `env_sample_stats` | 采样点 → filter → category_breakdown | 栅格算子未接线时的矢量等效；米制 CRS 义务 |
| `two_period_compare` | baseline/comparison_time → 各自聚合 → 并排输出 | 交叉表未接线；两期同构聚合 + 并排呈现等效 |

## 运行手册（本地复现）

```bash
# 环境（共享 venv，requirements.lock 已装）
cd <worktree>
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  /path/to/.venv/bin/python -m pytest tests/unit/model_authoring \
  -q --no-cov -p no:cacheprovider      # 全域（含 heavy 真实 driver e2e）

# 迁移（一次性 additive 表 model_drafts；downgrade 可逆）
DATABASE_URL="sqlite:////tmp/demo.db" alembic upgrade head

# 迁移 head 唯一性
alembic heads   # m0111_model_drafts（交付合并时按最新 head 重挂）

# 前端（frontend/ 下）
pnpm typecheck && pnpm exec vitest run components/model-builder \
  lib/workflow/model-graph.test.ts lib/api/model-authoring.test.ts test/i18n
```

## 已知边界（诚实声明）

- driver 直驱执行面 = cartography 真实渲染 + 3 个聚合能力（data_fabric
  AggSpec）+ transform 六算子；其余 analysis capability 经本域建模会被
  driver 以 `NODE_NOT_EXECUTABLE` 诚实阻断（不假装执行）——模板只装
  可执行等价结构，未接线能力的替代理由记录在模板 docstring。
- induced 技能（轨迹归纳）与本域的关系：本域发布面是**模型**（typed DAG
  包），不是技能 YAML；技能治理（#1327 留白）不因本域改变。
- 表产物 descriptor（feature_count=0）进 required 端口会被运行时绑定门
  拦截——模型把统计表接 output 汇点（required=False），这是 descriptor
  契约下的正确接线。
