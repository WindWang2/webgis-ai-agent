# G40 DEDUP_MATRIX — 生产链排重（present / partial / missing / owned-by-other / blocked）

基线 SHA：1276692737（2026-11-14 master）。每项判断以生产调用链为准。

| # | 能力项 | 判定 | 证据（file:line / PR） | 本任务动作 |
|---|---|---|---|---|
| 1 | MapSpec layer paint/layout 开放面 + additive typed 块先例 | present | mapspec_schema.py:721-776（label/bivariate/data_binding/representation/scale_profile/raster_style） | 新增 `symbol_asset` additive 块，保留兄弟字段 |
| 2 | symbol 编译 icon-/text-/symbol- 原生键透传 | present | frontend/lib/mapspec-compiler/compiler.ts:784-820 | 不重做透传；绑定块驱动 icon-image/anchor/rotate/size 显式写入 |
| 3 | fill 编译（颜色/透明度/轮廓/偏移） | present | compiler.ts:764-782 | 加性接 fill-pattern（经注册 image id），不改既有色面 |
| 4 | 点图形资产目录（创建/列表/版本/授权/删除） | missing | 全仓无 symbol_asset/cartographic_assets（grep 2026-11-15） | 本任务主所有权：新表+服务+API |
| 5 | 版本化资产生命周期（draft→publish→retire→restore、钉版本重放） | present（可复用模式） | app/services/templates/composition_assets.py:1-80；app/models/composition_template_asset.py（migration 0098 先例） | 照该模式建本域表；不建第二套生命周期语义，不复制代码 |
| 6 | 内容寻址二进制后端（sha256、put-if-absent、原子发布） | present | app/services/durable_blob_store.py:128-166 | 资产内容走 BlobStore；版本行持有 blob 键即受 promotion GC 引用保护 |
| 7 | MapSpec 单笔 CAS 写入（UpsertLayerIntent + expected_revision + checkpoint + undo） | present | thematic_authoring/service.py:499-645（apply_gis_mutation 模式） | 复用同一写入面；本域候选/预览/应用走同构 service |
| 8 | 会话归属守卫（require_owned_session + owner_token） | present | app/api/routes/thematic_authoring.py:53-108 | 同款路由面 |
| 9 | scope/租户纪律（不可见=404；DB 资产不进全局注册表；builtin 代码定义） | present | composition_assets.py:22-38；template_assets.py:46,337-440 | 逐条照抄纪律到本域 |
| 10 | live map 图像注册（addImage/icon-image/fill-pattern 到 map 实例） | missing | runtime.ts:1182 仅 addImageSource（G20 raster）；无 addImage 符号注册 | 本任务：真实 addImage + 生命周期管理 |
| 11 | SVG 子集导入与规范化（拒绝 script/外链/无限引用） | missing | 无既有 SVG 解析器（mapspec_to_svg.py 只有生成方向） | 本任务：受控子集 + 单一规范化口径 |
| 12 | Python SVG 出版：circle/line/fill 图元 + G16 障碍 | present | mapspec_to_svg.py:1889-2012（fill 洞已 evenodd）；1909-1936 circle 障碍 | 加性接入资产图元（defs/use/pattern）+ 变换后障碍 adapter |
| 13 | TS SVG 出版孪生 + circle 障碍 | present | frontend/lib/mapspec-compiler/mapspec-to-svg.ts:968-987 | 同步接入资产图元（与 Python 同语义） |
| 14 | 图例 swatch 消费入口（authored legend box） | present | app/lib/cartography/svg_marginalia.py:219-230 render_legend_box_authored | 接入同语义 swatch 内容；G34 已知续排/比例符号限制不修 |
| 15 | px/mm/pt/DPI 单位 corpus | present | app/lib/cartography/print_layout_authoring/units.py（ADR-0290） | 复用换算，不自造常量 |
| 16 | Pi 工具注册/dispatch/journey 模式 | present | app/tools/thematic_authoring_tools.py；tests/integration/test_pi_g33*.py | 同构本域三件套工具 + journey |
| 17 | 类别语义（legend_spec/category/color 权威） | present（G33 拥有） | thematic_authoring；thematic_spec.py | 复用类别 key_type 语义；类别→资产映射为本域新增，不改断点/色板 |
| 18 | 双变量/可靠性 | owned-by-other (G41 open PR 范围) | 无已合入代码；G41 分支存在 | 不触碰 |
| 19 | Pi 视觉上下文图像 envelope | owned-by-other (G36 #2118) | PR #2118 files: app/services/visual_context/* | 不触碰；本任务只提供资产 refs/预览 |
| 20 | 会话模型选择/用量 | owned-by-other (G37 #2117) | PR #2117 files: app/services/chat/model_runtime/* | 不触碰 |
| 21 | MapLibre 版本 | present | package.json ^6.10.0；锁 6.10.0（待 explorer 亲核 node_modules） | 以实际安装版本 API 为准 |
| 22 | icon 碰撞合同（allow-overlap/optional） | partial（无合同） | style-spec 26.5.1 默认 allow-overlap=false 会静默省略图标 | 本任务显式合同：默认 allow-overlap=true+ignore-placement（如实重叠）；declutter 组合显式支持才开 |
| 23 | visual_variables shape/texture/orientation 状态 | partial | visual_variables.py:67-82（shape=partial/texture=unsupported/orientation=partial） | 真实验证后仅对本域资产绑定通道提升 status 并注明口径 |
| 24 | G16 障碍接口（带身份障碍盒 + 求解器） | present | mapspec_to_svg.py:1909-1936；label_collision.py ↔ label-solver.ts | 只加资产变换边界 adapter，不扩求解器 |
| 25 | 图标/纹理的图例渲染 | partial（仅统一色块/圆） | legend 组件按 legend_spec 色渲染 | 本任务：同资产 swatch（前端图例 + SVG marginalia） |

## 兄弟任务边界（D 节核对）

- G36 #2118：视觉上下文——共享 `app/tools/__init__.py`、`app/agent_pi_bridge.py`、`chat.py`、`chat-tab.tsx`、`lib/api/chat.ts`。本任务在这些文件只做工具注册最小行 + 不动其域。
- G37 #2117：模型运行时——同上共享接缝。
- G38/G39：worktree 存在、无 PR。不动其域（数据关联/栅格采样）。
- G41：worktree 存在、无 PR。不动 bivariate/opacity 语义（mapspec_schema.py:640-655 保持不动）。
