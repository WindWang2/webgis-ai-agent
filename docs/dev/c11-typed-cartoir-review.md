# C11 Review — S3 独立审查与修复循环

- 审查对象：`77d2678d..HEAD`（origin/master → zcode/c11-typed-cartoir-mapspec-renderer-abi-*）
- 审查者：S3（独立只读 reviewer，未参与实现）；第一轮 `BLOCKED`，修复后提交 `0bc282d4` 复审。

## 第一轮发现与处置

### P1（阻断 → 已修，回归锁 review-fixes.test.ts）

| # | 发现 | 修复 |
|---|---|---|
| P1-1 | `compiler.ts` cluster-count 子层的 `...subVis` spread 位于显式 `layout:` 键之前 —— 被整体覆盖，注释声称的"子层同隐"从未生效 | visibility 移入 layout 字面量内；cluster 层默认可见恢复无 layout 键（byte parity） |
| P1-2 | `runtime.ts addLabelSublayerSafe` 只认 `layout.visibility ?? "visible"`，不接裁决面 —— authored `visible:false` 与 zoom 门在 live 标注子层失效（headless 已接）| 改用 `resolveLayerVisibility`（裁决值 + gate.minzoom/maxzoom 挂 def）|

### P2（应修 → 已修）

| # | 发现 | 修复 |
|---|---|---|
| P2-1 | 非 string version 两路径裁决不一致（runtime 拒绝 / compile 缺省 1.0）| 新增免克隆 `checkMapSpecVersion`，双路径同源判定；非字符串统一为后端 `_version_of` 同口径（缺省 1.0 放行）|
| P2-2 | `compileMapSpec` 门判定走 `migrateMapSpec` 全量 structuredClone（导出/CLI 每次深拷贝 MB 级 spec 只为读 ok）| 门改消费 `checkMapSpecVersion`（零分配）；migrateMapSpec 仅在需要迁移产物时使用 |
| P2-3 | forward_version 披露对更旧/垃圾版本失实（"is newer than"）| 措辞中性化（"not in the supported vocabulary"）|
| P2-4 | parity 声明偏差：(a) addLayerSafe 恒写 layout.visibility 键（渲染等价 + 修掉对象别名隐患）；(b) 词表外 layout.visibility 旧为 MapLibre 响亮失败、新为静默折算；(c) 无显隐 cluster 子层恒带 `"layout": {}` | (a)(c) 恢复 byte parity；(b) 新增 `invalidLayoutVisibility` 披露 + 双路径 `layout-visibility-invalid` evidence（fail-loud 纪律）|

### P3（可选）

- **已修**：P3-3 `fallbackColor` 类型失实（永不 null）→ `string`；P3-4 非数值 hints 进 `ignoredHints`（non_numeric_hint）；P3-6 fixture 死数据键移除；P3-8 `component_abi_version < 1` → abi_missing（词表下界）。
- **接受为已记录项**（PR body 注记）：P3-1 降级层图例仍按 match 色阶产出（有 warning，图例未标注降级）；P3-5 `interactionsDegraded` 标志暂无消费方（additive API，ABI v2 前接线）；P3-7 重复拒绝的 spec 每次重取证（环有界，仅噪音）；P3-9 `propOf` 对象型 id 回落语义变化（方向更正确，极窄边界）。

## 复核结论

（S3 复审结论见 PR body —— 复审通过后回填。）
