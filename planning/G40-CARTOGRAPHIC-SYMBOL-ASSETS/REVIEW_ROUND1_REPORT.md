# G40 独立评审报告（可复用点符号与面纹理资产工作台）

基线 `origin/master=1276692737` 未移动；评审对象 HEAD=`9abcd60bb`（59 文件，+9801/−58）。所有复跑均通过：`tests/cartography/test_g40_symbol_assets.py` + `tests/unit/test_g40_cartographic_asset_catalog.py`（35 passed）、`test_g40_publication_readback.py`（5 passed）、Pi journey e2e（`PI_JOURNEY_E2E=1`，23s，1 passed）、前端 `contracts.test.ts`（13 passed）、共享文件回归 `test_mapspec_to_svg*.py / test_visual_variables_v1.py / test_g34_print_layout_authoring.py / test_agent_commit_undo.py / test_mixed_export_route_and_tool.py`（128 passed）、G40 文件 ruff（项目配置）与 `tsc --noEmit` 均干净。以下 finding 均有复现或代码证据。

## P1（阻塞级）

**F1. DB 资产在 live 地图完全不可渲染 —— 内容端点响应形状与前端消费双重不符**
- 证据：`app/api/routes/cartographic_assets.py:270-277` 返回 `{"success", "document", "meta"}` 信封；`frontend/lib/cartographic-assets/api.ts:140-151` 的 `fetchDocByRef` 把信封当文档用（`doc.content_digest` = undefined → 必抛 `content_digest mismatch`）。且 `app/services/cartographic_assets/catalog.py:192` `_put_document_blob` 用 `model_dump_json(exclude={"content_digest"})` 存 blob —— 即使解开信封，`document` 里也没有 `content_digest`（builtin 路径 `catalog.py:466` 却带）。
- 触发输入：任何绑定非 builtin 资产的会话在 live 打开。
- 影响：`resolveLayerDocs` 全部 missing → `runtime.ts` addLayerSafe 直接跳过整层（`symbol_asset_unavailable`）——不是缺纹理，是整层消失。测试盲区：前端测试与 Pi journey 都只用 builtin。
- 修复：路由返回 `{...meta, **document}`（或前端解包 `.document` 并以 `meta.content_digest` 对账），并为 DB 路径补 digest；补一个 DB 资产 live 解析的回归测试。

**F2. SVG 导入器把多坐标对 `M` 静默改写成连续 moveto（合法常用写法几何丢失）**
- 证据：`app/lib/cartography/svg_import.py:312-316`（`upper == "M"` 分支无隐式 L 处理，`cmd` 置回 `"M"/"m"`）。复现：`d="M 2 2 20 2 20 20"` → 规范形 `[["M",2,2],["M",20,2],["M",20,20]]`，渲染为空（SVG 语义应为 M+两条隐式 L）。
- 影响：用户导入含该常见缩写的图标 → 符号不可见且无任何 typed 拒绝，违反“不静默删”合同。
- 修复：M 后续坐标对按隐式 L（相对则为 l）展开，并补测试。

**F3. live 纹理栅格化与出版语义实质分歧（宣称“几何等价”不成立）**
- 证据：`frontend/lib/cartographic-assets/rasterize.ts:140-183` 用“tile 预旋转后按轴对齐网格平铺”；导出侧 `symbol_asset_svg.py:158` 用 `patternTransform="rotate(θ)"`（网格随转，垂直间距恒 = spacing）。数值验证（格点偏移集）：θ=45° 时 live 有效间距 = 0.707×spacing（默认角就密 41%）；θ=30°/60°（tan 无理）相位稠密 → 摩尔纹/糊死；仅 θ=0 一致。
- 影响：同一绑定 live 与 SVG/PNG/PDF 出版观感不同（S12/单位合同在交互面失真）。
- 修复：live 改为在 tile 内按“屏幕坐标画角度线族”（与 swatch_svg 同式）再采样，或以 spacing/cosθ 校正并限制可用角度；加跨端几何 oracle 测试。

## P2（重要）

**F4. fallback_asset 类别绑定写出 schema 禁止的字段，会话文档被判 invalid**
- 证据：`mapspec_schema.py` `MapSpecLayerSymbolAssetCategoryBinding`（extra="forbid"）只声明 `field/key_type/map/other_mode/other_asset`；`binding.py:333-335` 却写 `other_revision/other_content_digest`。复现：`parse_mapspec` → `valid=False`，`extra_forbidden @ layers[0].symbol_asset.category_binding.other_revision/other_content_digest`。schema 文档自称“权威形状”，却被自家写链打破；严格消费者（如 harness evaluator）会拒绝。`other_params` 同缺（live-project.ts 读它恒 undefined）。
- 修复：schema 补三个 optional 字段（`other_revision: int`、`other_content_digest`、`other_params`），加 fallback_asset 全链 apply→export 测试。

**F5. Pi apply 的幂等键漏 layer_id → 跨层静默 no-op**
- 证据：`app/tools/cartographic_asset_tools.py:333` `mutation_id = f"symbolasset:{plan_digest}:r{expected_revision}"`；引擎 dedup 在 CAS 之前（`lifecycle_engine.py:402`）命中即返回旧结果不执行。同一 turn 用同 plan+同 revision 绑第二个层 → 第二层未写，回执 `success+deduplicated`。修复：键加 `layer_id`。

**F6. 纹理类别子层成幽灵层（移除/解绑不级联）**
- 证据：`runtime.ts:1453-1481` 添加 `${layer.id}__texcat_*` 子层但未注册 `repSublayersByLayer`；`removeLayerSafe`（runtime.ts:1669+）只级联 `-label`、label-authoring、rep 注册表，**没有**注释声称的 `${id}__` 前缀扫描。解绑/删层/重编译后幽灵纹理面残留至 style 重建。
- 修复：把 texcat 子层 id 注册进级联表（同 rep 纪律），并修注释。

**F7. G34 print-layout 导出未传用户身份 → 属主 DB 资产在版式导出中丢弃**
- 证据：`app/api/routes/print_layout.py:167-169` 只传 session_id；`print_layout_authoring/service.py:_resolve_symbol_assets_sync` 回落 `tool_caller_identity()`，HTTP 上下文下 = (None,None) → 匿名 → DB 资产 `symbol_asset_unavailable`（有披露/manifest，但属主导出丢纹理）。mixed export 已做（`map.py:783` asset_identity），此处漏。修复：路由透传身份。

**F8. 坐标硬帽 MAX_COORD_ABS 未对未变换 path/circle/line/ellipse/rect(w,h) 强制**
- 证据：`svg_import.py` `_check_coord` 仅用于 rect x/y、polygon points、带 transform 的 path；复现 `d="M 99999 0 L 0 0"` 被接受入库。违反“运行时超限同样停止”合同（实际渲染有裁剪，影响有限但帽子是可测承诺）。修复：`_flatten_path` 末段与各图元分支统一 `_check_coord`。

**F9. svg_tile 纹理周期跨端不一致（导出 vs live/TS 孪生）**
- 证据：Python `symbol_asset_svg.py:186-197` 周期 = tile_viewbox×scale×dpi；TS `mapspec-to-svg.ts g40PatternDef` 与 `rasterize.ts:159-167` 周期 = spacing_px×scale。复现：24 单位 tile + spacing 12 → 导出 24、live/孪生 12（差 2 倍）。另外 `swatch_svg`（symbol_asset_svg.py:202-266）无 svg_tile 分支 → 导入 tile 的出版图例 swatch 画成通用晕线（复现确认，S12 失真）；且 tile 自身颜色被实例 `fg_color`（默认 #334155）整体覆涂（symbol_asset_svg.py:191-194），多色 tile 默认变单色灰，未披露。TS `swatchSvgString` 同样。

**F10. 嵌套 `<svg>` 被接受而非文档承诺的拒绝**
- 证据：`svg_import.py:_walk` 对 tag="svg" 仅按组递归；复现：嵌套 svg（不同 viewBox/x/y）被接受且其 viewBox/x/y 被静默忽略 → 几何错位。docstring/ADR-0292 明列“嵌套 `<svg>` 拒绝”。修复：非根 svg → `SVG_ELEMENT_UNSUPPORTED`。

## P3（改进/备注）

- **F11** 根元素属性不校验：`onload`、`style="fill:url(#x)"` 在根 `<svg>` 上被静默忽略（`_reject_attrs/_style_dict` 未作用于 root）——安全性无影响（规范形不带），但违反“绝不静默删”表述。复现确认。
- **F12** `<use x= y=>` 偏移被忽略（svg_import.py:649-681）→ 合法 use 摆放错位。
- **F13** `angle2_deg` 在白名单里但从不归一/输出（symbol_asset.py:402-437）——死参数静默忽略；cross_hatch 第二方向固定垂直。
- **F14** `register.ts reinstallAll` 是死代码（无调用方；S15 实际由 addLayerSafe 重投影覆盖）；且 reinstall 在缓存被 LRU 驱逐时报 `ASSET_RASTER_FAILED` 而不重栅格化。
- **F15** unbind 无条件恢复 prev_paint_snapshot 键：绑定期间用户的 paint 修改被静默回滚（复现：opacity 0.2 → 恢复 0.9）。docstring 有披露、UI 无提示。
- **F16** `content_digest` 含 `name`（compute_digest 只排除 digest 自身）→ “名字只是显示名”与内容身份含名矛盾；CAS blob 键同含名，同内容不同名不去重。当前无 rename API，属潜在。
- **F17** 路由 docstring 称创建支持“builtin 引用建立私有副本”，代码实际必须 `svg_base64`（routes:165-181）——文档腐化。
- **F18** 纹理类别子层不继承主层 `fill-opacity` 等 paint（runtime.ts subDef / compiler.ts g40SublayerDefs 仅 fill-pattern）→ 已映射类别与未映射要素透明度不一致。
- **F19** 屏上 CategoricalLegend 所有类别共用主绑定 swatch（categorical-legend.tsx:46-58 未传 `c.key`；`resolveBindingSwatch` 明明支持 categoryKey）——live 类别区分度丢失（出版图例是按 key 的）。
- **F20** `mapspec-to-svg.ts` 的 `g40Defs` 是模块级可变 Map（每次编译 clear）——当前同步无碍，重入隐患。
- **F21** `builtin-documents.ts` 标注“生成物”但仓库无生成脚本/漂移锁测试（本次手工核对：当前与 Python 完全同步）。

## 已查无问题

- **scope/IDOR**：跨 owner 猜 ID=404 不泄露、org 可见≠可写、admin 同 org 可写，谓词与 G04 同构，有矩阵测试。
- **匿名工具身份**：`tool_caller_identity` 缺席 → DB 不可见、失败如实进 failures/manifest（有测试）。
- **digest 对账**：写入 validate、读取 `_verify_document`、pin 不符 typed 拒绝；builtin↔TS 文档当前同步。
- **S02 typed 匹配双端一致**：Python 导出 / TS 孪生 / live `resolveBindingForFeature` / MapLibre `==`+`in` 过滤器语义一致（"1" 不并入 1，型不符走 other/keep_current）。
- **S18 删除守卫**：current_revision 拒删 + 磁盘 mapspec.json 扫描（有界+截断披露；Redis-only 会话不在扫描面已在 docstring 如实声明）。
- **S20 CAS**：SUPERSEDED 409 + correction_hint，不盲重放；真重试经 dedup（先于 CAS）正确幂等。
- **S22 锁层**：agent origin 被引擎 `guard_locked_partitions` 拒（有测试）；user 豁免；`visible` 不被绑定改写（有测试）。
- **S23 缺资产**：双端（Python/TS）都是诊断+不画，无默认圆伪装（有测试）。
- **S26 出版真实回读**：SVG 自包含（XML 可解析、无外链）、PNG 走真实 rsvg 像素分类 oracle、PDF 走 WeasyPrint+pdftoppm 回读；rsvg pattern workaround 严格限定 `url(#cartoap-` 前缀，旧路径字节不变有断言。
- **S27 冻结**：mixed export 在冻结点解析 digest pin，manifest 记录 resolved/unresolved。
- **S31**：`icon-allow-overlap/icon-ignore-placement` 显式 + viewport 对齐显式 + declutter 显式 `UNSUPPORTED_DECLUTTER`；导出 N 要素=N 图标。
- **资源帽**：512KB 输入/400 节点/12 深/512 path 命令/200 图元/512KB 规范形/defs 128（Python+TS）/类别子层 16+披露/工具列表 24——除 F8 外均真实强制。
- **生命周期/CAS 令牌**：`_cartographic_mutation_revision` 与引擎同源（session_data_manager）；版本 max+1 重试。
- **复用边界**：catalog 纪律照抄 composition_assets、BlobStore 复用、未造 G33/G34 第二套轮子；共享文件改动加性（128 项共享回归全绿，legacy 输出字节不变有断言）。
- **Pi journey 真实性**：真 uvicorn 生产 app + stub LLM SSE + `tool_search` deferred dispatch + 权威 MapSpec 裁决（digest pin/兄弟保留/origin=agent/解绑恢复/revision 推进），非 mock。
- **合并风险**：vs #2118(G36) merge-tree 干净；vs #2117(G37) 仅 `app/tools/__init__.py` 一处同类追加的平凡冲突（两条 `_TOOL_MODULES` 记录都保留即可）。

## 总结论：GO-WITH-FIXES

后端主干（目录/版本/绑定/CAS/锁/出版冻结/三格式回读）质量高、测试真实。但三个 P1 均无测试覆盖且都打在核心承诺上：DB 资产 live 不可渲染（F1）、导入器静默丢合法几何（F2）、live 纹理与出版语义分歧（F3）。F1/F2 修复小而确定；F3 需要一次几何决策。建议修完 P1 + F4/F6（写链自洽性）后合入，P2 其余可跟单，P3 记录备查。