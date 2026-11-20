# G40 Round-2 独立复审报告（复判 Round-1 修复 commit 83009c320）

复跑全绿：后端 48 passed（三套件）+ G34 共享 34 passed + Pi journey 1 passed（29.7s，PI_JOURNEY_E2E=1）+ 前端 contracts.test.ts 15 passed + tsc 干净 + G40 文件 ruff 干净。但另有 **2 个仓库级测试在 HEAD 失败**（见 N7），且逐条复核发现若干 Round-1 未覆盖的真实缺陷（N1/N2 最重）。

## 逐条 verdict

**F1（P1）FIXED** — `app/api/routes/cartographic_assets.py:276-281` 扁平返回 `{success, **document, meta}` + `catalog.py:478-487` blob 读回注 `content_digest`。消费闭环独立追踪确认：`apiFetch`（transport.ts:331）原样返回 JSON body → `fetchDocByRef`（api.ts:140-151）顶层读 `doc.content_digest` 对账 → `putDoc` 入 digest 缓存 → runtime 消费。测试 `test_f1_read_version_blob_injects_digest` 验 blob 层（无 route 级 HTTP 测试，可接受）。

**F2（P1）FIXED** — 12 组边界探针全部正确：单对 M、相对 m（`m 2 2 18 0 0 18` → M(2,2),L(20,2),L(20,20) 精确）、M 后跟命令、奇数 token typed 拒绝、mid-Z 重启、Z 后跟数字拒绝、隐式 L 的 token 消费无错位；隐式 L 长度仍被 tokenizer 预检上界覆盖。

**F3（P1）FIXED（核心语义）** — 独立几何 oracle（Python 复刻 TS 算法）验证：**无缝性精确成立**（tile 平移相位推进 = p·dEff / q·dEff 恒整数倍，全参数域 seam=0）；**间距保真 ≤0.22px**；45° 有效间距不再 0.707×；dots/svg_tile 分支未破坏。残留见 N4（角度保真度在大间距下退化 + 兜底 size 超 256 预算 + docstring "≤0.3°" 在默认间距 15°/75° 实测 0.95° 不成立）。

**F4 FIXED（核心）** — schema 三字段（`other_revision` ge=1、`other_content_digest` max_length=96、`other_params`）+ `test_f4` parse_mapspec 全链有效 + `types.generated.ts` 同步。备注：写链（binding.py:333-335）仍不产出 `other_params`（声明未接线，TS 端 `?? params` 优雅降级，无害）。

**F5 FIXED** — `cartographic_asset_tools.py:335-339` 键含 layer_id。**无回归测试**。

**F6 PARTIAL** — 注册进 `repSublayersByLayer`（runtime.ts:1474-1476），`removeLayerSafe` 级联（1691-1699），普通纹理 fill 层场景已闭环；但**排序洞**：texcat 注册（append）先于 `addRepresentationSublayersSafe` 末尾的 `.set(layer.id, subIds)`（runtime.ts:1669，替换整个数组）——同层同时有纹理类别绑定和有效 representation 计划时 texcat id 被抹掉，幽灵层在该组合下复现。未测试。

**F7 FIXED** — 链路完整：route `Depends(get_current_user_optional)` → `actor_ids` → `asset_identity` → `submit_export(asset_identity=)` → `_resolve_symbol_assets_sync`（显式身份 > 工具上下文 > 匿名）。G34 共享 34 passed；export 路由 OpenAPI 条目与快照逐字节相同（未引入 schema 漂移）。无回归测试。

**F8 PARTIAL** — path（含未变换）/circle/ellipse/rect 新增强制（extent 检查含外接矩形），探针确认；**`line` 仍漏**：`<line x2="80000">` 被接受入库（svg_import.py:653-666 无 `_check_coord`）。Round-1 明确点名 line；测试只盖 path+circle。

**F9 PARTIAL** — 周期双端统一（TS 孪生 `g40PatternDef` periodW/H = tile_viewbox×scale×dpi；与 Python 同式）、fg 不覆涂（`_primitive_svg(p)` 无 override）、swatch 补 tile 分支（双端），测试验颜色+周期。但见 **N1（重大相邻缺陷）**、N5。

**F10 FIXED** — 嵌套 svg 拒绝（`SVG_ELEMENT_UNSUPPORTED`）；根 svg 不经 `_walk`（仅遍历子元素），根 xmlns/width/height 不受影响（探针验证）。测试真实。

**F11 FIXED** — 根属性校验；**无误伤**：`xmlns`、`width/height` 接受；`onload`→SVG_EVENT_ATTR；根 `fill/opacity/style`→SVG_ROOT_PAINT_ATTRS。`_URL_VALUE_RE = url\s*\(` 不匹配 xmlns 值。测试真实。

**F12 PARTIAL** — x/y 平移生效且精确（`<use x=10 y=20>` 探针 M(10,20)）；但与 use 自身 `transform` 组合时变换被**双重应用**（探针：`transform="translate(5,5)" x=10 y=20` → (20,30)，SVG 正确值 (15,25)）——根因 N2，pre-existing。另 `use x="abc"` 静默归零（SymbolAssetError 是 ValueError 子类被 except 吞）。

**F13 FIXED（代码）+ 引入漂移 N6** — Python 白名单/builtin 默认/TS 白名单三处移除；但 TS builtin 孪生未再生成（N6）。

**F17 FIXED**（docstring 修正，纯文档）。**F19 FIXED**（`AssetSwatch` 传 `c.key`；`resolveBindingSwatch(binding, categoryKey)` 按 rule/fallback 解析；无测试）。

## 新发现 findings

- **N1（P1 级，pre-existing 9abcd60bb，双评审均漏）svg_tile 导入内容域不匹配——导入纹理在所有表面渲染为角落微点**。导入器把 tile 图元归一到**单位方**（`svg_import.py` root_matrix `1/vb_w,1/vb_h`；实测 `tile_viewbox=(24,24)` 而图元 `M 0.0 0.25 L 1.0 0.25`），但全渲染链按 **tile_viewbox 域**缩放：Python `symbol_asset_svg.py:196-199`（`sx=tw/tile_w`→1.0，24 单位 pattern 里画 1 单位线）、TS 孪生 `mapspec-to-svg.ts`（同式）、live `rasterize.ts:149`（`scale(w/tw)`=2，48px 画布画 0..2px）、双端 swatch（`s=min(box_w/tile_w)`）。用户导入的 tile 作品缩到 ~1/tile_viewbox。复现脚本输出在案。修复方向：渲染侧 `scale=period`（或导入器存 tile_viewbox 域坐标）。
- **N2（P1 级，pre-existing）图元自带 `transform` 被双重应用**。`_walk`（svg_import.py:784-787）已把元素自身 transform 合入 `own_matrix`，`_emit.transform_of()`（532-538）再次解析同属性叠加。实测 `<path d="M 10 0" transform="translate(5,0)">` → M 20,0（正确 15,0）；circle、use 同病；嵌套 g×transform 误差累积（实测 21 vs 18）。带 transform 的 path/circle/… 是图标导出的常见形态——静默几何错误，与 F2 同类合同违反。F12 的 use-shift 经由同一个 m，故 use+transform+x/y 组合错误。
- **N3（P3）** F8 残留：`line` 坐标帽未强制（svg_import.py:653-666）。
- **N4（P3）** F3 残留：a) docstring “误差 ≤0.3°” 不实——默认 d=24 时 15°/75° 实测 0.95°，d=48 时 30° 误差 3.4°，d=128 时 30°→45° 格点（15° 误差），d≥256 兜底可偏 45°；b) 兜底 `size=Math.round(d)` 无 256 上限（参数域最大 d=512，违反 S25 pattern tile 预算；rasterize.ts:236-237）。
- **N5（P3）** svg_tile 细节分歧：a) Python `bg_rect` 以 spacing×spacing 尺寸放进 tile（tile_viewbox>spacing 时背景只盖局部；live 全盖）；b) 默认参数 normalize 出 `angle_deg=45` → 导出给 tile 加 `patternTransform="rotate(45)"`（实测）而 live svg_tile 分支不旋转。建议 tile 资产冻结 angle 或双端对齐。
- **N6（P3）** TS builtin 孪生漂移：`builtin-documents.ts:448-462` cross_hatch 仍含 `angle2_deg` + 陈旧 digest `30299713…`（Python 现为 `fbeb2993…`，其余 8 个 builtin 一致）——F13 改了 Python 单源没再生成孪生，F21 预警的漂移在修复 commit 内成真。功能惰性（live 种子来自服务端列表；孪生仅测试消费），但“生成物勿手改”合同破坏且无 drift-lock 测试。
- **N7（P2，合并卫生）HEAD 上 2 个仓库级测试失败**：`tests/quality/test_api_compatibility.py::test_snapshot_matches_and_no_breaking_changes` 与 `tests/test_api_docs_drift.py` —— OpenAPI 快照未随 G40 新增路径刷新（纯 additive、0 breaking；我核对漂移路径全部是 `/api/v1/cartographic-assets*`，print-layout export 条目逐字节相同，故非 83009c320 引入，是 9abcd60bb 遗留且两轮都未跑此测试）。需 `API_SNAPSHOT_UPDATE=1` 刷新 + docs 再生成。
- **N8（P3）** F6 排序洞（见上，runtime.ts:1669 `.set` 覆盖 1474 append）。
- 微备注（不计级）：commit message “新增回归：F1-F13 逐项测试”言过其实——F5/F6/F7/F13/F17/F19 无逐项测试（仅 F1/F2/F4/F8/F9/F10/F11/F12 后端 + F3 前端）；`use x="abc"` 静默归零；`_shift_primitive` line 的 w/h 不随 scale（docstring “宽高随 |scale| 近似”过誉）；catalog blob 重序列化用默认分隔符（与存储字节不同构，仅观感）。

## 总结论：GO-WITH-FIXES

Round-1 的三个 P1（F1/F2/F3）修复真实且核心语义成立，F4/F5/F7/F10/F11/F13/F17/F19 干净落地，回归面（后端/前端/共享/Pi journey/tsc/ruff）全部通过。但本轮独立复核发现的 **N1（svg_tile 导入渲染为微点——导入纹理功能视觉上整体失效）与 N2（transform 双重应用——常见 SVG 写法静默几何错误）** 与原 P1 同级同类（导入路径静默几何损坏），且为两轮实现+评审共同盲区；另 HEAD 携带 2 个失败仓库测试（N7，机械修复）。建议：**N1、N2、N7 修复合入前必须完成**（N1/N2 均为小而确定的修复 + 补跨端 oracle 测试）；F6 排序洞（N8）与 F8 line（N3）宜同批；N4/N5/N6 可跟单。