# AC-07 决策日志（ac-07-decisions）

> 配套 ADR-0156。每条决策：背景 → 决定 → 理由/证据。执行期新增决策追加在文末。

## D1. 任务书三处基线偏差的裁决

- **事实**（P0 勘察，证据见 ac-07-layout-recon.md §2）：inset_map 已全链
  native（非 planned）；COMPONENT_OUTSIDE_CANVAS 已 fail+auto_safe；后端
  solver 只到 V3（v4 不存在）；「22 个组件」实为 20 类型 / 19 descriptor /
  18 渲染器。
- **决定**：不重做已实现能力。P6 收窄为状态钉住 + source 隔离测试 +
  注释漂移修正；P1 聚焦 LAYOUT_COLLISION 建议与断环；组件数字以真值表述。
- **理由**：防重复复核纪律 —— 不造第二套已有实现。

## D2. `pip install -e .` 在 master 上不可构建 → 按 CI 等效方式

- **事实**：pyproject 无 build-system/packages 配置，editable 安装被
  setuptools 自动发现拒绝；全部 CI workflow 只跑
  `pip install -r requirements-dev.txt` 后在仓库根执行 pytest。
- **决定**：环境搭建按 CI 等效；偏差记入《复核纪要》。

## D3. LAYOUT_COLLISION 修复建议的 repairability 分级

- **决定**：`status` 保持 `warning`，附 `repairability=auto_safe` +
  `suggested_fix`（operation=resolve_layout_collisions）。
- **理由**：quality_loop 只自动修 `fail` —— 若升为 fail 会改变全链行为并
  侵蚀 user-wins；若标记 not_repairable 则违背「可自动修复」目标。warning +
  auto_safe = 前端/评审可执行、自动通道零回退。

## D4. COMPONENT_LINK_CYCLE 走 `auto_with_semantic_risk`

- **决定**：fail 不变，附断环建议（remove_links + 权重证据）与
  auto_with_semantic_risk。
- **理由**：断边移除的是语义声明（requires/under），非纯呈现；
  quality_loop 对该级别本就要求 explicit intent（quality_loop.py:671-677），
  不为其白造 executor —— 断环由 live composition-repair 与评审通道执行。

## D5. autofill 注入域限定 chrome 族（诚实渲染边界）

- **决定**：`AUTOINJECTABLE_TYPES = {scale_bar, north_arrow, attribution}`；
  title/legend/graticule/inset_map 缺席时只记 advisory 决策。
- **理由**：chrome 族无需数据即可诚实渲染；数据承载件无数据注入即伪造
  （与 graticule「不虚构网格」、inset「不虚构范围」同一原则）。必配清单
  完整记录在 decisions，供 09 线评审采纳。

## D6. attribution 占位署名是有意的行为变化

- **决定**：数据来源未知时 live 自动补「数据来源：—（待补充）」占位。
- **理由**：§0.5 默认决策明令「禁止省略署名」；占位文本显式标注待补充，
  不虚构来源。既有测试零改动通过（无测试锁定「attribution 缺席」态）。

## D7. 折叠溢出面板的求解语义

- **决定**：V4 L3 = 落到专属溢出槽 bottom-center（宽度收敛 1 + collapsed
  标记，允许容量覆写）；requested 即 bottom-center 时镜像 top-center。
- **理由**：「折叠为溢出面板」的本义是指定倾泻目标而非重试容量检查；
  折叠态像素足迹极小，容量覆写如实披露（warnings + RepairStep）。

## D8. 密度自适应为双维联合约束 + 全球退化出超

- **决定**：候选间隔须同时使经/纬向线数 ≤10；域内取首个双维 ≥3 的档；
  无档时取 min(lineCount) 最大者；全球级跨度取最粗档（30°）如实出超。
- **理由**：单维约束会让另一维度跌破 3（实测 2×1.5° 跨度）；全球视图
  任何优选间隔都超 10 —— 出超 + 披露优于虚构或省略。

## D9. 断环权重 = 端点 priority 和，平局 (dst,src) 字典序

- **理由**：priority 是组件既有语义（贴边序/重要性代理）；断开端点
  priority 和最小的边对渲染序影响最小；字典序平局保证确定性。

## D10. export-chrome 只消费 chrome 段（整饰描述边界）

- **决定**：08 线边界内，export 侧对中间层的消费限定为 descriptor.chrome
  （numericScale/declination/graticule 配置）与 provenance 读取；不改
  exporter 画布逻辑（exporter.ts / frame-composer.ts 未触碰）。

## D11. 磁偏角用偶极子近似并恒标 approximate

- **理由**：WMM 系数表不适合内嵌前端（体积/更新频率）；偶极子模型
  （DGRF2020 地磁极 80.65°N/72.68°W）提供数度级精度的确定性近似，
  配合「≈ + (approximate)」标注满足 §0.5「真北偏角数据不可得」分支。

## D12. 版式维度对三检查的敏感性（基线设计结论）

- **事实**：三检查是 zone 计数/图语义模型，canvas 硬编码 1280×720；
  版式只能经 solve（重排 position）与 layout.margins 间接影响。
- **决定**：4 版式基线按「同语料 × 4 profile 求解折算 + 配对 margins」
  构造；归零验证在每版式下独立执行。不做 semantic_checks 签名扩展
  （跨线影响不可控）—— 版式敏感版面 QA 留给中间层消费方。

## D13–D15（S2 独立复审后的修复裁决，2026-09-13）

## D13. 可折叠词表单源化 + 真实 parity 测试

- **发现**：COLLAPSIBLE_TYPES 在 composer/solver/前端三处复制，且注释声称
  的「parity 测试锁定」并不存在（不实注释）。
- **修复**：后端单源 = component_composer（solver 导入之，无环）；新增
  `test_collapsible_vocabulary_parity`（is 断言）；前端集注明为渲染域
  镜像。注释不再引用不存在的测试。

## D14. 语料 harness 入库 + 归零门禁全量化

- **发现**：scripts/ 被 `/scripts/*` gitignore 挡住，20×4 全量证据不可由
  提交物复现。
- **修复**：harness 移入 `tests/cartography/ac07_corpus_harness.py`；
  `test_ac07_zero_regression.py` 升级为全量 20 案例 × 4 版式 × 2 口径。

## D15. 前端镜像规则表与后端对齐 + A3 档

- **发现**：镜像缺「screen+投影 → graticule 建议」分支；任务书 §2 P2 的
  A3 档在双侧词表均缺席；isPrint 判定基准不一致（后端原始 vs 前端解析后）。
- **修复**：后端补 A3（isPrint 统一以解析后 purpose 判定 —— 未知用途按
  §0.5 归屏幕）；前端补 screen 投影分支 + A3；双侧词表/分支由测试锁定。

## D16. 其余 findings 的处置

- **修复**：floating 重叠披露恢复面积证据；compose elements 的 slot 改由
  共享求解器真解析（去占位常量）；graticule-labels 死三元删除、
  bboxCenter 文档如实化；scale-bar aria 与显示模式一致；legend nodata
  消费 v2 color、label-only 呈现为虚线框；图例卡补快照测试；inset 静态
  扫描扩展到 ctx.spec/sources。
- **反驳（记录不动）**：render-observation.ts 的 `__fallback_*` 预测 id 与
  autofill id 的差异 —— 该文件属 §8 禁改区（mapspec-runtime/**），其
  「spec 缺席 → chrome 挂载」的预测语义仍然为真（chrome 以 autofill 或
  fallback 之一挂载），无消费者按 id 字符串匹配；留待 06 线在其词表中
  收敛。snapshot 断言为新增快照的首次落盘（锁定后续回归）。
