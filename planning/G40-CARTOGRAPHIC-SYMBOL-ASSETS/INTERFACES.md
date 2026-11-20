# G40 INTERFACES — 接口与契约清单

## 后端新模块

| 模块 | 接口 | 语义 |
|---|---|---|
| `app/lib/cartography/symbol_asset.py` | `SymbolAssetDocument`（symbol_asset.v1）；`normalize_point_params/normalize_texture_params`；`compute_digest/validate_digest`；`anchor_offset`；`conservative_bounds`；`get_builtin_asset/builtin_asset_index`（9 内置） | 规范形单一契约；typed 参数上下限；digest 身份 |
| `app/lib/cartography/svg_import.py` | `import_svg_asset(svg_bytes, kind, name, license_note)` | 受控 SVG 子集 → 规范形（唯一清洗口径）；typed 拒绝码 SVG_* |
| `app/lib/cartography/symbol_asset_svg.py` | `point_symbol_def/use/transform/obstacle_box`；`texture_pattern_def`；`swatch_svg`；`prepare_svg_for_rasterize`（librsvg 回归 workaround） | SVG 投影纯函数（出版/图例共用） |
| `app/models/cartographic_symbol_asset.py` | `CartographicSymbolAsset` + `Version` | 0098 先例的资产/版本表对 |
| `app/services/cartographic_assets/catalog.py` | `create_asset/new_version/publish_version/retire_asset/restore_asset/resolve_version_document/read_version_blob/list_assets/list_versions/delete_asset_version/find_asset_references/collect_symbol_asset_documents` | 生命周期 + scope（404/403）+ digest 对账 + S18 引用检查 + S27 出口冻结 |
| `app/services/cartographic_assets/binding.py` | `inspect_layer_binding/preview_binding/apply_binding/unbind_layer`；`plan_digest_of` | UI/Pi 同一写入面；单笔 UpsertLayerIntent CAS |
| `app/api/routes/cartographic_assets.py` | 目录 CRUD + 会话绑定 4 端点（require_owned_session） | REST 面（main.py 注册 /api/v1） |
| `app/tools/cartographic_asset_tools.py` | `webgis_symbol_asset_list/create`、`webgis_symbol_layer_inspect/preview/apply/unbind` | Pi 工具（_TOOL_MODULES 注册；map_mutations=("symbol_asset",) 新词表） |

## 契约变更（additive）

- `MapSpecLayerSymbolAsset`（mapspec_schema.py）：schema_version=1 / asset_id /
  revision / content_digest / kind / params / category_binding（typed
  key_type）/ applied_by / prev_paint_snapshot；`SCHEMA_EXPORT_MODELS` 增 3
  类型（types.generated.ts 再生成）。
- `compile_mapspec_to_svg_detailed(symbol_assets=...)`：规范形注入面（缺席
  → symbol_asset_unavailable 诊断，不画不伪装）。
- `export_mixed_map(asset_identity=...)`、`submit_export`：出口冻结 + manifest
  `symbol_assets.{resolved,unresolved}`。
- 诊断词表 + EMITTER_REGISTRY：`symbol_asset_unavailable`、
  `symbol_asset_def_budget`（Python + TS 双发射点）。
- `MAP_MUTATION_KINDS` + `"symbol_asset"`。
- `CHANNEL_RUNTIME_STATUS`：texture unsupported→partial（证据注释）。
- G34 `read_legend_content(symbol_assets=)` / `build_draw_plan(symbol_assets=)`；
  `render_legend_box_authored` 消费 `entry.swatch.swatch_svg`。
- librsvg PNG workaround：`prepare_svg_for_rasterize`（仅含 `url(#cartoap-` 时
  改写根尺寸；既有路径字节不变）。

## 前端新模块

| 模块 | 接口 |
|---|---|
| `frontend/lib/cartographic-assets/contracts.ts` | 类型 + 参数归一镜像 + `pointImageId/patternImageId/stableKey/anchorOffset` |
| `frontend/lib/cartographic-assets/rasterize.ts` | `rasterizePointSymbol/rasterizeAreaTexture`（ImageData）；`swatchSvgString` |
| `frontend/lib/cartographic-assets/register.ts` | `ensureRegistered/releaseMap/reinstallAll/registrationStats`（LRU ≤32MB；引用计数跨实例） |
| `frontend/lib/cartographic-assets/live-project.ts` | `projectSymbolAssetLayer`（icon-*/fill-pattern 投影 + 类别 match/子层）；`resolveBindingForFeature`（S02 typed） |
| `frontend/lib/cartographic-assets/api.ts` | 目录/内容/绑定 API + `resolveLayerDocs`（doc 预热缓存） |
| `frontend/lib/cartographic-assets/legend-swatch.ts` / `builtin-documents.ts`（生成物） | 图例 swatch / 内置规范形 fixture |
| `frontend/components/hud/symbol-asset-panel.tsx` | 面板（挂载 layer-style-panel：circle/fill 层） |

- `MapSpecRuntime`：`warmSymbolAssets`（processOne 预热，epoch 防迟到）+
  `addLayerSafe` 投影/类别子层 + `dispose→releaseMap`。
- `compiler.ts`：`projectBindingStatic`（导出）+ compile 结果 `symbolAssets` 清单。
- `mapspec-to-svg.ts`：`symbolAssets` 选项 + defs/use/pattern/障碍（Python 同语义）。
- 图例链：`LegendStackEntry.symbolAsset` → `ThematicLegend` → `CategoricalLegend`
  （`cat-swatch-asset`）。
- i18n：`hud.symbolAssets.*`（zh/en）。
