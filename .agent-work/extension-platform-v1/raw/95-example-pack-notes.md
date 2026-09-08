# Wave 14 — Example Extension Pack (extdemo-pack)

日期：2026-09-08　分支：feat/gis-extension-platform-v1（worktree webgis-ai-agent-extension-platform-v1）

## 交付物

### 1. `extensions/examples/extdemo-pack/`（全部新增，活文档 + 测试夹具二合一）

| 文件 | 要点 |
| --- | --- |
| `manifest.json` | id `extdemo.pack`，namespace `extdemo`，version/api_version 1.0.0，entry_point `main`，diagnostics_entry `health:check`，trust `trusted_builtin`，permissions `["network"]`（provider `requires_network=True` 的联动要求）。声明 2 tools + 1 algorithm + 1 data_provider + 1 cartography item + 1 workflow pack，与 `activate()` 注册逐一对应（声明↔注册 fail closed）。 |
| `main.py` | 两个纯计算工具：`extdemo_bbox_area`（side_effect=pure、deterministic、4 参数全带 param_descriptions）、`extdemo_polygon_compactness`（GeoJSON Polygon 首环的 Polsby-Popper 4πA/P²，鞋厂公式+周长，退化输入显式 ValueError）。算法 `compactness`（EXPERIMENTAL、category=morphometry、capabilities=[]（冻结 seam 不虚构 id）、tool_candidates 写投影名 `extdemo_polygon_compactness`、3 个 NumericalSmokeCase：单位正方形→π/4、2:1 矩形→8π/36、4:1 矩形→16π/100，tolerance 1e-3）。Provider `demo_tile_catalog`（is_raster_tile、requires_network=True）。组件 `note_scale_bar`（kind=component、runtime_status=planned、type `extdemo_note_scale_bar`、category `navigation.scale_bar`、export_behavior=degraded、degradation_policy=omit_with_disclosure、renderer/exporter support 诚实留空）。WorkflowPack `demo_overview`：单 recipe `extdemo_demo_overview`（intent_tasks=["simple_view"]、preferred_analysis=["poi_query","point_profile"] 全为既有 capability id、primary_cartography=simple_point_map）。 |
| `tile_catalog.py` | `DemoTileCatalogAdapter(GeospatialDataSourceAdapter)`：镜像核心 `WMSWMTSAdapter` 栅格语义——`query` 诚实返回空 features + `reason=raster_tile_source_has_no_vector_features`；`describe` 读捆绑 `catalog.json`、feature_count=None（诚实未知）、未知 id 抛 ValueError；`probe`/`health` 零网络恒 healthy。 |
| `catalog.json` | 2 个占位瓦片图层条目（随包指纹一起哈希）。 |
| `health.py` | `check() → {"status": "healthy", "messages": []}`。 |
| `README.md` | 每个文件的作用 + 扩展作者四大坑（声明↔注册一致、目录不在 sys.path 需 `_load_sibling` 按路径加载兄弟模块、命名空间前缀/算法 tool_candidates 用投影名、诚实性红线）。 |

### 2. `tests/unit/extensions_platform/test_example_pack.py`（7 个用例）

fixture：copytree 包 → tmp_path，`HostPolicy(roots=(tmp,), builtin_ids={"extdemo.pack"}, grants={"extdemo.pack": {"network"}})`，ToolRegistry 自建实例 + 真实单例 registry（algorithm/adapter/component/recipe）。

- `test_activation_state_and_projections`：ACTIVE（diagnostics==[]，无警告非 DEGRADED）；五类投影名 `extdemo_*` 全部可 has/get。
- `test_tool_dispatch_math`：`await registry.dispatch` 面积=6.0；退化 bbox → VALIDATION_ERROR/ValueError（不伪造结果）。
- `test_algorithm_authoring_smoke`：`run_authoring_checks` 零诊断；正方形→π/4≈0.7854（abs 1e-3）；descriptor 落 registry 且 scientific_status=EXPERIMENTAL。
- `test_provider_resolves_offline`：`get_registry().resolve` 命中、is_raster_tile=True；build_adapter 后 describe/query/health 全离线可跑。
- `test_recipe_passes_compile_time_validation`：精确镜像 `registry_validation.validate_gis_library` 的 recipe 块（TaskType 词表、capability 存在性、cartography 经 MapModelRegistry 解析）。注：激活期不能跑完整 validate_gis_library——扩展算法刻意无 capability、扩展组件不在渲染器真值矩阵，两者是设计内 issue。
- `test_health_gate`：host.health → healthy/state=active。
- `test_deactivate_leaves_no_zombies`：deactivate 后工具/算法/provider（resolve 抛 UnsupportedSourceError）/组件/recipe 五类零残留。

顺序鲁棒性：`_cleanup_leftovers()` 在激活前与 fixture finally 里幂等清理 `extdemo_*`（单例跨测试持久，防止失败运行残留引发碰撞）；只做 has/get 断言不做计数断言。

## 验证结果

- `python -m pytest tests/unit/extensions_platform/ -q --no-cov` → **2129 passed, 0 failed**（原 59 个基线用例全绿；本 wave +7）。
- `ruff check extensions/ tests/unit/extensions_platform/test_example_pack.py` → **All checks passed**（test_host_lifecycle.py / test_sdk_projections.py 的 5 个 F401 属并行 wave 的在制文件，未触碰）。
- 未 commit；未修改 app/ 任何文件。

## 过程备注（给后续 wave）

- 本 worktree 期间有并行 wave 在改 `app/extensions_platform/{context,host,diagnostics,api_version}.py`、`sdk/tool.py`、`wms_wmts_adapter.py` 并新增 test_cli/test_sdk_projections/test_conformance_corpus/test_ogc_stac_hardening；期间出现过 3 个瞬时失败（文件写一半时跑全量）与 2 个他 wave 失败（test_cli scaffold、test_sdk_projections），均在隔离运行复现、与 extdemo 无关，最终全量转绿。
- 关键契约确认：算法 `tool_candidates` 必须写**投影后**工具名且工具先于算法注册（context.register_algorithm 对照 host 注入的 ToolRegistry 校验）；dispatch 对工具内 ValueError 归类为 `code=VALIDATION_ERROR, error_type=ValueError`（不是 TOOL_ERROR）；健康门 unhealthy 才回滚，healthy/degraded 继续。
