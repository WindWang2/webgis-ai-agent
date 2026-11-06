# PR #1965 场景与地形审查记录

- 日期：2026-11-06。
- 原始 PR head：`6ef617df783264cf1b83e45512a6a2b7fcfb1c98`。
- 审查基点：`7b4e4166798000d1dd4af7651a6583a85cb75d7e`，即本次审查时 `origin/master` 与 PR head 的 merge-base。
- 范围：原 PR 的 32 个文件及本次补充修复；重点复核 3D Tiles 调度、glTF 资源上界、模型生命周期、控制器交互、地形摄入/TileJSON、上传去重和观察归属。最终验证在隔离的 `codex/pr-1965-reviewed` 工作树执行，未修改其他 PR 源码。
- 状态：以下五项已修复，最终有界测试及生产/测试 TypeScript 检查通过。记录对应原 PR head 加当前审查修复，不表示已合并或部署。

## 五项修复

### 1. 可见瓦片预算耗尽时保留已就绪祖先

`frontend/lib/map-kit/scene3d/tiles3d/scheduler.ts`。

触发条件是 `REPLACE` 树、`maxVisibleTiles=1`，且子节点已占用预算或等待加载。原逻辑可能把未成功选入渲染集的已加载子节点算作就绪，或让待加载占位节点占住祖先需要的槽位；已就绪祖先随后被卸载，产生可见空洞。

选择函数现在返回是否成功选入；就绪判定包含选择结果。祖先兜底时清除全部后代渲染占位，保留独立加载队列。回归覆盖子节点未加载、子节点已加载两种预算耗尽情况，断言已就绪根节点仍在渲染集且不会被卸载。

### 2. 限制空 glTF 节点图的展开

`frontend/lib/map-kit/scene3d/gltf/gltf-parser.ts`。

仅有深度、环和顶点预算时，共享空节点组成的小型 DAG 可以指数展开，而不消耗顶点预算。宽而浅的空节点场景同样缺少总节点上界。

解析器现在拒绝重复父节点/重复节点引用，并将单资产展开节点数限制为 `8192`；分别返回 `corrupt` 和 `budget_exceeded`。回归包含 14 层重复空节点 DAG 及 `8193` 个空根节点，验证在产生失控展开前拒绝输入。

### 3. 模型重挂后同步恢复当前会话的用户表象

`frontend/lib/map-kit/scene3d/asset-manager.ts`、`index.ts`，以及 `frontend/components/map/scene-controller.tsx`、`map-panel.tsx`。

原回放放在控制器子组件的 passive effect；父组件随后才执行场景同步。源 revision 与资产 placement 同次变化、底图清扫等重挂路径会在回放之后重新创建资产，导致界面仍显示隐藏或半透明，而实际模型恢复默认表象。

表象现在由当前地图 manager 实例保存，并在模型重挂和单模型构建完成后同步应用。场景同步显式接收会话标识；切换会话时清除旧覆盖并重建同名资产。控制器只清空资产显隐/透明度本地状态，保留既有光照和书签行为。仍在 spec 中的资产经过 2D/3D 往返保留覆盖；从 spec 移除时清理 manager 中的对应记录。

新增集成回归使用真实控制器、真实 manager 和父组件同步效应，检查实际 `visibleEffective` 和模型材质透明度；覆盖源/placement 更新、底图清扫、context lost/restored、2D 往返以及跨会话同名资产。fake map 不创建 WebGL context。

### 4. 场景提交在途时不丢弃夸张系数

`frontend/components/map/scene-controller.tsx`。

原滑条在场景提交忙碌期间仍可操作；释放时先清除 draft，再调用会因 `busyRef` 直接返回的提交函数，最终值被静默丢弃。

场景提交忙碌时禁用滑条。若已有拖动 draft 在另一控制开始提交之后才释放，保留该 draft，并在忙碌结束后补交。回归覆盖忙碌时禁用/恢复，以及已有 draft 延后提交，验证实际提交次数和最终 exaggeration 值。

### 5. 为跨反子午线地形发布 MapLibre 可用的 TileJSON envelope

`app/api/routes/layer.py`、`tests/unit/test_terrain_tilejson.py`。

连续经度 `170°→190°` 摄入后的科学 bbox 为 `[170, south, -170, north]`。当前安装的 MapLibre `TileBounds.contains` 要求 west 不大于 east；直接把该 wrapped bbox 用作 TileJSON `bounds` 会剔除整个图层，而不只是负经度一侧。

TileJSON 现在使用 `[-180, south, 180, north]` 作为保守瓦片请求 envelope，同时在 `terrain.coverage_bbox_epsg4326` 保留精确 wrapped bbox，并以 `terrain.tile_bounds_policy="antimeridian_envelope"` 标记该策略。摄入 receipt 和 `ANTIMERIDIAN_CROSSING` 理由保持原义。

回归实际生成 `170°→190°` GeoTIFF、注册地形源、读取 TileJSON，再获取正经度一侧的 `z=5,x=31,y=15` PNG，断言 alpha 通道存在非透明像素。

## 最终验证

本轮验证在上述全部修复落定后执行；未运行全量前端测试，未修改测试阈值。

| 检查 | 结果 |
| --- | --- |
| 受影响前端测试 | 20 个文件，219 项通过 |
| 后端原相关测试与 TileJSON 回归 | 126 项通过，2 条 Rasterio deprecation warning |
| 生产 TypeScript：`tsc --noEmit` | 通过，退出码 0 |
| 测试 TypeScript：`tsc -p tsconfig.test.json --noEmit` | 通过，退出码 0 |
| 补充控制器/manager 源码及测试 ESLint | 通过，零 warning |
| `git diff --check` | 通过 |

前端复现命令，在 `frontend` 目录执行：

```bash
./node_modules/.bin/vitest run \
  lib/map-kit/scene3d \
  components/map/scene-controller.test.tsx \
  components/map/scene-controller.integration.test.tsx \
  lib/mapspec-compiler/scene-compiler.test.ts \
  lib/mapspec/scene-merge.test.ts \
  lib/mapspec/scene-mutation.test.ts \
  lib/mapspec-runtime/runtime.scene3d.test.ts \
  lib/map-kit/terrain-governance.test.ts \
  lib/hooks/use-cartographic-observation.test.ts \
  lib/map-commands/viewCommands.test.ts \
  lib/map-kit/render-scene.test.ts \
  lib/map-kit/render-scene.parity.test.ts \
  lib/map-kit/scene-export-disclosure.test.ts \
  components/map/map-components/scene-disclosure.test.tsx \
  lib/api/storymap-scene.test.ts \
  --maxWorkers=2
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/tsc -p tsconfig.test.json --noEmit
```

后端复现命令，在仓库根目录执行：

```bash
.venv/bin/python -m pytest -q --no-cov \
  tests/cartography/test_cartographic_quality_review.py \
  tests/cartography/test_render_work_corpus_v1.py \
  tests/unit/test_terrain_ingest.py \
  tests/unit/test_upload_raster_lane.py \
  tests/unit/test_terrain_tilejson.py
```

前四个后端文件对应先前记录的 116 项；加入 TileJSON 文件后本轮共 126 项。两条 warning 来自 Rasterio `Window.round_shape` 的废弃提示，不是测试失败。Vitest 另提示当前 Vite 配置将来默认 native config loader 的兼容性变化，本轮无启动或执行失败。

## 限制与合并注意事项

- 未执行真实浏览器/WebGL 旅程，也未重新生成截图。矩阵、拾取、深度遮挡、GPU 驱动差异及真实 context 恢复视觉结果，不能由本次 jsdom/fake-map 测试作出视觉验收结论。
- TileJSON envelope 仅用于避免 MapLibre 错误剔除。它不证明整个经度范围都有数据；连续经度源的负经度半区仍可能因瓦片窗口与源网格不相交而透明。消费者应读取原 receipt、`coverage_bbox_epsg4326` 和原因码，不能把 envelope 当作全覆盖科学证据。本次 PNG 回归只证明正经度一侧可渲染。
- 本轮采用有界输入和定向回归，没有执行全量前后端、性能压力或部署后验证。
- 与较新的地形认证改动整合时，必须保留短时签名 URL 和 live owner-auth fallback；原 PR 的 query 编码改动不能覆盖这些认证边界。本报告的当前分支测试不能替代整合后的认证回归。
