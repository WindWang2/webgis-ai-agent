# 多源空间实体融合工作台（Data Fusion Authoring）

> 对同一地理实体的多来源观测建立**可证明的匹配、冲突、合并、质量与
> 审阅语义**：两份机构名录合并出唯一学校清单、两年行政区对齐出变化
> 配对、修测地块关联不强行合并。预览只读不合并；冲突不可被覆盖、只
> 可被显式裁决；提交产出**不可变融合数据集版本**（来源证据/字段
> provenance/许可署名/质量报告随附），可被既有统计/热力/聚合/交付链
> 直接消费。Pi 与普通 UI 走同一服务、同一权限、同一收据。

- ADR：`docs/adr/0300-multi-source-entity-fusion.md`
- 后端：`app/services/data_fusion_authoring/`（contracts/plan/textsim/
  matching/adjudication/executor/outputs/review/incremental/service）
- HTTP：`/api/v1/data-fusion-authoring/...`（`app/api/routes/data_fusion_authoring.py`）
- Pi 工具：`webgis_data_fusion_*`（`app/tools/data_fusion_authoring_tools.py`）
- 前端：`frontend/components/sidebar/data-fusion/`（数据工作台融合面板）
- 存储表：`data_fusion_plans` / `data_fusion_runs` / `data_fusion_decisions`
  （migration 0110）
- 测试：`tests/unit/data_fusion_authoring/`（契约锁定 / 匹配 oracle /
  裁决 / 真实 DB 旅程 / 规模验证）

## 1. 它能做什么

| 能力 | 说明 |
|---|---|
| 来源资格卡 | 逐源三态（ok / unresolvable）：行数、CRS 与可比性分类、几何形态、计划声明字段存在性、时间字段可解析样本率、许可/署名声明。装载时验证事实 ≠ 来源自我声明，分项标注 |
| 融合计划 | 版本化（revision CAS + normalized digest）：实体种类（point_poi / area_link / line_link）、两源锚与字段声明（名称、可选 id + **共享 id_space**、地址、类别词表映射、时间字段）、输出字段映射与逐字段裁决规则、五类证据权重（必须归一）、阈值/歧义边际/距离窗口、几何采纳政策、提交政策、预算（clamp 到域硬顶） |
| 融合预览（只读） | 与执行同一内核：双源资格、覆盖对比、真实匹配运行的有界投影（自动/审阅/歧义/硬阻断/未配对计数）、样本对、预算与警告；缓存按 digest 失效 |
| 实体匹配 | 三通道 blocking（ID 桶 / 空间格网 3×3 halo / 名称桶）→ 五类证据逐项评分（双缺失跳过重归一、单侧缺失按 0）→ 互最优 + 边际 + 阈值 + 硬阻断 + **id 钉定** → matched / needs_review / ambiguous / unmatched |
| 冲突检测 | 配对内逐输出字段 identical/conflict/missing_a/missing_b/missing_both；名称字段与计划归一化同一口径；几何距离/重叠证据入档 |
| 审阅与裁决 | 审阅队列分页（含 matched+字段冲突项）；逐对决定 accept/reject（可附人工字段值/几何侧），CAS revision 乐观并发；决定挂 plan 级、键 = 实体内容指纹（跨刷新重挂靠）；locked 锁定唯一推翻路径是显式删除 |
| 受权提交 | require_resolved 门（未决项 typed 阻断）→ 组装 → 原子发布 → 文件字节哈希收据 → DataObject → session 台账 `ref:data-fusion/<run_id>` → 渲染投影（帽 10k） |
| 融合成果 | 不可变 FeatureCollection：每要素 `_fusion` provenance（来源/逐字段裁决与双侧原值/匹配证据/几何采纳/决定记录）；origin ∈ merged / single_a / single_b / withheld_review |
| 质量报告 | 全真实计数 + 逐项「适用/需审阅/不适用」依据；无单一神秘评分 |
| 增量刷新 | 与基底 run 比较实体内容指纹 → added/removed/影响面占比；决定三态（inherited/stale/orphan）；超 35% 安全阈 typed `refresh_not_safe`；重跑走同一 durable job 路径（base_run_id 链，旧版本可追溯） |

## 2. 用户旅程（数据工作台 → 融合面板）

1. **描述来源**：两份 session ref（如学校名录 FC + 开放地图 FC）→
   资格卡确认字段/CRS/许可；不可解析源直接给 typed 修复提示。
2. **建计划**：声明实体种类、字段映射与逐字段规则、类别词表、权重
   与阈值；两源 priority 必须不同且带理由（来源信任 = 带理由的声明，
   不是绝对正确性）。
3. **预览**：查看自动/审阅/歧义/硬阻断/未配对计数与样本 —— 只读，
   不合并、不建成果。
4. **执行**：durable job 匹配；收据含真实计数与冻结源指纹。
5. **审阅**：逐对看证据分项（名称相似度分项、距离米数、类别词表
   比对、id 比对）与字段冲突；接受（可给人工值）/拒绝（保持分开）；
   CAS 冲突时刷新重读，绝不覆盖他人决定。
6. **提交**：require_resolved 下未决项阻断并给计数；提交产出融合
   版本 + 质量报告；未决对（allow_unresolved 政策）两侧按单源输出
   并披露 —— 不假装已合并。
7. **消费**：结果 ref 进入既有统计/热力/行政聚合工具；导出走既有
   交付链（附许可/署名/冲突/未知说明）。
8. **刷新**：来源更新后 dry_run 看影响面与决定三态；安全则受控重跑
   （人工锁定保留、旧版本可追溯），变更面过大则显式全量重算。

## 3. 诚实边界（红线）

- **空间邻近 ≠ 实体同一**：距离只是证据之一；别名落在审阅带，同名
  远距不自动合并；歧义（边际不足/争抢/候选溢出）恒不自动合并。
- **缺数据 ≠ 零值**：缺失按 missing 语义记录于 provenance，绝不折算
  零/空串/满分。
- **不同 id 体系不可比**：external_id 证据要求两源声明同一 id_space。
- **冲突不可被静默覆盖**：采纳必须可追溯到规则或人工决定；双侧原值
  永远在 provenance。
- **几何绝不自动 dissolve**：采纳侧由政策/人工决定。
- **ID 冲突是硬证据**：共享 id_space 内 id 不一致的对恒不自动合并
  （同 id 跨城同样进审阅 —— 数据可疑）。
- 不把「匹配完成」说成「已融合」：run 完成只是匹配包冻结；成果以
  commit 后的不可变版本为准。
- 不自动联网抓取未授权来源；源凭据不进 LLM/前端；跨会话/跨项目
  引用 typed 拒绝。

## 4. 与相邻能力的职责对照

| 能力 | 它做的 | 本域不做的 |
|---|---|---|
| G38 数据关联 | 左表骨架属性富集（join 语义） | 不做实体消歧/融合版本 |
| G10 数据刷新 | 项目数据版本感知/影响/采纳 | 不做融合自身的实体级重配 |
| G17 导入 | staged 多文件导入 | 不重复导入面 |
| G18 交付 | 多格式成果交付 | 融合成果导出复用它 |
| G45 坐标操作 | 可审阅/可固定重投影 | CRS 不可比时 typed 提示走 G45 |
| G44 字段契约 | 字段业务契约/校验 | 融合输出可被其约束 |

## 5. 已知边界与限制

- v1 固定 pairwise（两源）；N 源以「融合成果作为下一计划源」组合。
- line_link 的重叠证据 = 交长/短边长；无方向性对齐（道路转向语义
  待后续）。
- 栅格覆盖对齐不在本域（走既有栅格管线）。
- 增量重配 = 决定继承 + 影响面报告 + 确定性引擎重放（同输入同
  输出）；不做部分候选增量评分（超安全阈走全量，诚实语义）。
- 渲染投影帽 10k 要素；超限走 G18 交付获取完整数据。
