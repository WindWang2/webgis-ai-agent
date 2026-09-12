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
