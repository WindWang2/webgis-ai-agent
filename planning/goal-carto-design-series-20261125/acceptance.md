# Acceptance — goal-carto-design-series-20261125

## 全局验收（任务书 §6 最低验收映射）

| # | 条款 | 验证方式 |
|---|---|---|
| A1 | 从一张真实已生成地图提取可复用规范 | 集成测试：真实 MapSpec（合成成都 fixture）→ extract → 校验无数据值/ref/credential 泄漏 → spec 落库带 digest |
| A2 | 绑定另一地区/时相不同字段，既有模型/模板/组件真实生效 | 集成测试：spec apply 到第二张 MapSpec（不同字段名/地区），真实 mutation 通过 CAS，渲染链消费 |
| A3 | 用户手工锁定不被覆盖 | 锁定字段（user-wins）在 apply 中跳过并披露；负例：agent 传播不覆盖锁 |
| A4 | 不兼容单位/资产 fail closed | 负例：rate vs count 共享色带 → typed 拒绝；退役资产引用 → violation 拒绝/明确降级 |
| A5 | 系列具有可比较规范、可审阅传播计划、逐图真实回执、真实导出文件/manifest | 端到端：3+ 图系列 → 传播 preview → apply → 逐图 receipt → 真实 SVG/PDF/PNG + series manifest（含 digest） |
| A6 | 局部变更只重做受影响项 | 缓存重用测试：改 1 图 → 其余输出字节不变/复用；失败图状态 visible 不伪装 |
| A7 | 五类案例各有真实产物和负例 | WP6 案例库：设施密度/行政区对比/时间变化/OD 流动/多图综合 → 各有 golden + 负例测试 |
| A8 | 用户与 Pi 共用同一服务/权威 | 工具与 REST 同 service 层；Pi 经 webgis_execute 直达（现有机制），dispatch 面测试 |

## WP1 验收（设计规范权威模型）

- W1.1 `CartographyDesignSpec` + 版本快照（不可变、max+1 savepoint 重试、软废弃），
  migration `g50`（down=0109），upgrade/downgrade 本地验证。
- W1.2 typed payload（pydantic，extra=forbid，有界）：引用面（theme/palette/model/
  component variant/composition template/print profile/G40 asset id+revision）全部
  对照权威 registry 校验，violation typed codes；未知 spec 版本拒绝。
- W1.3 继承链（parent_version_id 同 spec 内深合并 ≤16 + 环检测）；digest：structural
  （canonical JSON sha256）+ resolved（palette/typography/classification 投影）。
- W1.4 overrides_policy 词表：用户可覆盖字段 vs 锁定字段；显式覆盖必须记录披露。
- W1.5 命名查重（同 scope 内唯一 name）；租户 scope 同 templates/scope.py 语义。
- W1.6 preview：支持/不支持渲染目标（renderer capability）、冲突列表、有效继承链版本。
- 负例：引用不存在 palette / 循环继承 / 超深链 / 数据值写死（检测 payload 携带数值断点数组
  → 拒绝，分类只允许 method 声明）/ payload 泄漏 ref:/credential 形态字段。

## WP2 验收（数据驱动模板与绑定）

- W2.1 recommend：purpose/geometry/value semantics/unit/extent/output → 排序候选（既有
  template/model/spec），带 evidence 与不可用原因；负例：count 无分母不推荐 density。
- W2.2 role binding：spec 角色 → 字段绑定，六态（复用 role_binding 状态语义）+ 单位/
  定义/分类可比性检查（dataset semantics + preflight）；缺失/空/全零/负数/无数据类别
  区别处理（负例各自一条）。
- W2.3 shared classification：冻结数据并集上算一次（classify.py），按数据指纹缓存；
  各图独立表达必须显式披露不可比。负例：rate+count 混合 → 拒绝共享。
- W2.4 extract：MapSpec → spec 草案，仅可移植表达；带 session selection/ref/credential/
  数据值的字段被排除并披露。负例：含 inline credential 形状 → 排除+披露。
- W2.5 apply：经既有 mutation 通道；dry-run diff（意图变更清单，不落盘）；缺资产/
  不兼容字段/旧版本回退 typed；用户锁定保留。

## WP3 验收（系列作者与一致性）

- W3.1 MapSeries 实体（有界成员集合、共用 spec pin、比较维度、每图角色/标题/范围、
  可比性规则）；成员引用既有权威（recon B 定案）。
- W3.2 传播 preview：受影响图、锁定字段、legend/classifier 变化、失效资产、不可用产品。
- W3.3 逐图 CAS apply + 逐图 receipt（committed/superseded/failed+原因）；批量语义如实
  （分批/失败重试），不宣称原子（除非既有机制支持）。
- W3.4 一致性检查结构化 findings：同指标单位/分类/颜色一致、不同指标不强制同色域、
  比例尺/范围按用途、标题/副标题、字体/边距、来源日期、图例完整性、资产版本；
  finding 定位具体图件/组件。负例：跨指标强制同色域不被检查器误报。
- W3.5 不把某图 ref 传播到其他图（负例）。

## WP4 验收（工作台）

- W4.1 工具面：design spec inspect/list/extract/recommend/preview/apply + series
  plan/bind/propose/propagate/consistency/freeze/status（命名查重后定案），inspect/
  preview 只读，apply/propagate 走 CAS+权限；args Pydantic + typed errors + 有界回执。
- W4.2 REST router 同 service 层（/design-specs /map-series），require_owned 授权。
- W4.3 前端工作台面板：spec 草案/版本选择、角色/约束查看、锁定/覆盖、适配矩阵、
  系列图卡+定位、批量 dry-run/apply/回执；真实地图预览走权威 MapSpec。
- W4.4 i18n zh/en 完整；宽窄屏、空系列、失效版本、双 tab stale、保存/刷新恢复、键盘。
- W4.5 vitest 组件测试 + journey（mock 模式）1 条。

## WP5 验收（出版与 QA）

- W5.1 freeze plan：冻结 data/artifact/map revisions、template/assets fingerprints、
  legend/classification、extent/CRS、source/license、DPI/纸张/output。
- W5.2 durable job（沿用现有机制）：逐图预检+QA；取消/恢复；缓存按指纹复用。
- W5.3 真实导出（SVG+PDF 或 SVG+PNG，按 runtime 能力）+ series manifest（每图状态/
  文件 digest/尺寸）；图例、比例尺、标题语义在真实产物中可验证（解析 SVG/PDF 文本）。
- W5.4 缺版权/来源/图例不完整/资产不可用/取消/部分失败 → 按图可见状态+原因；
  未完成不标完整。负例各一条。

## WP6 验收（案例库与规模）

- W6.1 ≥5 类案例（fixture 有 ground truth，不虚构真实地理数据）：
  ①设施分布/密度（同分母断点）②行政区专题对比（同指标）③时间变化（共享表达+日期）
  ④OD 流动（引用 G21 语义）⑤多图综合专题（composition/布局）。
- W6.2 golden/semantic tests：空值/超界/正负/单位不同、CJK 长标题、类别溢出、资产退役、
  用户覆写、MapSpec stale、跨项目/未授权资产、图件局部失败、输出与预览一致性。
- W6.3 20/50 图 series preflight + 缓存重用 + UI 列表虚拟化 + export memory 测量
  （perf marker 或有界计时断言，资源预算记录）。
