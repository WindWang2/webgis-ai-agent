# G40 PLAN — 可复用点符号与面纹理资产工作台

状态：实现中。基线与排重见 BASELINE.md / DEDUP_MATRIX.md。

## 一、领域设计（WP01–WP02 定稿）

### 1. 资产身份与版本（照 composition_assets 纪律，不复制代码）

- 新表 `cartographic_symbol_assets`（主表）+ `cartographic_symbol_asset_versions`
  （不可变版本行，String(36) PK；additive migration，CHECK 词表与迁移一字不差）。
- id 命名空间：`cartoasset.<uuidhex>`；内置资产 `cartoasset.builtin.*`（代码定义，
  revision=1，不落库 —— builtin 走同一解析/规范化路径）。
- 生命周期：draft → published → retired（retire=软退役：新绑定拒绝、钉版本重放
  只读放行）；新版本 = max+1 新行，不回写旧行。
- scope/visibility：private（creator）/ org / project；不可见 = 404 不泄露存在性；
  DB 资产永不进任何进程级全局注册表；内容去重（sha256 blob 键天然去重）不合并
  owner 权限（元数据行按 owner 独立）。
- 内容后端：BlobStore（sha256 内容寻址）。版本行持有 blob 键 + content_digest
  （promotion GC 引用保护）；删除/retire 版本前查引用（见 6）。

### 2. 规范化资产文档 `symbol_asset.v1`（单一清洗口径）

JSON 规范形（不是原始 SVG —— 导入时一次解析成有界图元，渲染双端不重复解析）：

```
{
  "schema_version": 1,
  "kind": "point_symbol" | "area_texture",
  "viewBox": {"width": W, "height": H},        // 有界（≤256 用户单位，有限正值）
  "primitives": [ {"op":"path","d":["M",x,y,...]} | {"op":"circle",...} | ... ]  // ≤200 个，坐标有界
  "default_params": {...},                     // 纹理：angle/spacing/stroke_width/fg/bg 等
  "source": {"origin":"builtin"|"svg_import", "license": "...", "note": "..."},
  "content_digest": "sha256:<64hex>"           // 规范形 canonical 序列化摘要
}
```

- 内置点形状（builtin）：triangle / square / diamond / star5 / cross / pin 等
  （首批 6 个）；内置面纹理：hatch / cross_hatch / dots（首批 3 个）。
- 受控 SVG 子集导入（point_symbol 与 area_texture tile 均可）：仅允许
  `<svg><g><path><rect><circle><ellipse><line><polygon><polyline><defs><use>`，
  path 命令 M/L/H/V/C/S/Q/T/A/Z（拒绝 a 命令大写 A 允许？——统一拒绝隐式重复、
  拒绝相对命令小写？定稿：允许大小写 M/L/C/Q/A/Z 等标准集，坐标必须有限有界）；
  拒绝：script/事件属性/foreignObject/外部 href/CSS url()/style 元素/实体/
  text 字体字符图标（无受控 text→path 实现）/clip/filter/mask/渐变（首批）/
  嵌套 use 循环 / viewBox 非有限或零尺寸 / 节点/坐标超预算。unsupported 元素
  显式报错（typed code + 元素名），绝不静默删除后宣称成功。
- 规范化器输出 primitives + digest；预览/live/SVG 出版全部消费同一规范形
  （禁止两套清洗口径）。

### 3. MapSpec 图层绑定块 `symbol_asset`（additive typed）

挂在 MapSpecLayer 上（mapspec_schema.py additive，extra=allow 既有）：

```
"symbol_asset": {
  "schema_version": 1,
  "asset_id": "cartoasset.xxx" | "cartoasset.builtin.triangle",
  "revision": 3,                       // 钉住的版本（published）
  "content_digest": "sha256:...",      // 不可变内容 pin（与版本行一致才可解析）
  "kind": "point_symbol" | "area_texture",
  "params": { ...typed 有界实例参数（见 4） },
  "category_binding": {                // 可选：类别 → 资产映射（typed key_type）
    "field": "type", "key_type": "string|number|bool",
    "map": [{"key": "school", "asset_id": "...", "revision": 1, "content_digest": "...", "params": {...}}, ...],
    "other": {"mode": "fallback_asset"|"keep_current", ...}   // 缺失/未知类别显式规则
  },
  "applied_by": {"origin": "user|agent", "actor": "...", "applied_at": "..."},
}
```

- 资产名字只是显示名；身份 = asset_id + revision + content_digest。
- 二进制/任意 URL 绝不进 MapSpec。
- 绑定更新 = 新版本显式迁移绑定（新 revision 写入新绑定），旧地图保持旧版本
  （S04/S27）。undo 恢复先前完整绑定（既有撤销栈，mutation 单笔即天然支持）。
- 层型合同：point_symbol 绑定要求层源为 Point/MultiPoint 主面（circle 语义层）；
  area_texture 要求 Polygon/MultiPolygon（fill 语义层）。MVT/ref 源放行——数据
  面由源 profile/运行时披露，不强制 inline。

### 4. 实例参数（typed 上下限；WP05）

point_symbol params：
- `size_px`（CSS px，1–128，默认 16；= icon 外接框目标尺寸）
- `anchor`：center|left|right|top|bottom|top-left|top-right|bottom-left|bottom-right
- `rotation_deg`（−360–360，默认 0；viewport 对齐 —— 唯一已实现合同；
  map 对齐/pitch 显式拒绝 unsupported）
- `color`（可选 hex 覆写，作用于资产默认着色槽）、`opacity`（0–1）

area_texture params：
- `spacing_px`（4–64，默认 12）、`angle_deg`（−90–90，默认 45）、
  `stroke_width_px`（0.25–8，默认 1.5）、`fg_color`（默认资产色）、
  `bg_color`（默认 transparent —— 合同：透明背景不混入未知不透明底，S13）
- `pattern_scale`（0.25–4，纹理单元整体缩放）

单位合同：live CSS px / SVG 用户单位（mm*4*dpi/72，ADR-0290 corpus）/ 出版物理
mm 恒定 —— 同一 size_px 在 SVG 导出按 dpi_scale 缩放（与既有 circle radius
*dpi_scale 同式）。屏幕 px 是尺寸单位，不推断朝向固定于屏幕：rotation 显式
viewport 对齐并在 bearing 变化时保持视觉角度（MapLibre icon-rotate 默认行为）；
纹理相位锚 = 图面像素原点（viewport 对齐），zoom 不改变 spacing_px（恒定屏幕
间距合同，写入文档与披露）。

### 5. 图标碰撞合同（WP05/S31）

- 默认：`icon-allow-overlap: true` + `icon-ignore-placement: true`（不因图标
  碰撞/相邻文字静默省略数据图标；如实显示重叠）。与既有文字策略的关系：
  不改既有 text 层；图标不参与文字 collision（ignore-placement）。
- 可选 declutter：`declutter: true` 显式开启 → icon-allow-overlap false；
  必须同视窗/尺度在 live 与出版同策略（出版侧同步省略逻辑：按 MapLibre
  近似贪心省略并披露省略数量 —— 若无法保证跨端一致则不支持，首版只开放
  默认无省略 + 显式拒绝 declutter 组合）。**定稿：首版只实现默认无省略，
  declutter 参数显式拒绝（UNSUPPORTED_DECLUTTER）**——诚实边界，不为拼凑
  开放未验证组合。

### 6. 引用/GC/删除（WP03/S18/S19）

- 资产版本被引用 = MapSpec 图层绑定块 / 导出任务 manifest 持有
  (asset_id, revision)。删除版本 → 查全部会话 MapSpec 引用（会话 store 遍历
  有界）+ 导出 manifest → 被引用则拒绝（409 ASSET_IN_USE，提示 retire）；
  retire 保留内容可读（钉版本重放只读放行）。
- 跨 owner 猜 ID：列表/读取/预览/绑定/导出全走 scope 检查（不可见=404）。

## 二、写入路径（WP07）

服务 `app/services/cartographic_assets/`：
- `catalog.py`：资产 CRUD/版本/publish/retire/列表（分页 ≤50）/复制/删除检查。
- `binding.py`：候选构建 + 资格检查（层型/源/类别字段 typed 检查）+ 预览
  （零 mutation，返回 swatch refs 与诊断）+ 应用（单笔
  `apply_gis_mutation(UpsertLayerIntent(layer, source_data=None), expected_revision)`
  ，同 G33 模式；只改 symbol_asset 块与本域 paint/layout 键，其余逐键保留）。
- 应用时写入：symbol_asset 块 +（若层原为 circle）保留原 paint 键不动 ——
  渲染投影层负责实际表达（见三）。解除绑定（unbind）恢复原 circle/fill paint
  表达（应用时把绑定前的 paint 快照存入块内 prev_paint_snapshot，有界）。
- 锁定层/隐藏层检查：locked（G34 locks 或层锁语义）拒绝；hidden 层允许绑定
  但不改变 visibility（S22）。
- CAS 冲突 → SUPERSEDED 409，重预览；不盲重放。

## 三、渲染投影（WP06）

### live（frontend/lib/cartographic-assets/）

- `contracts.ts`：绑定块/资产文档 TS 类型 + 与 Python 同语义的参数校验/默认值。
- `resolve.ts`：API 拉取资产规范形（GET /cartographic-assets/{id}/versions/{rev}
  /content，scope 检查在服务端）；LRU 缓存键 = (content_digest, params_hash,
  pixelRatio)；迟到请求 epoch 防护（不进新会话，S17）。
- `rasterize.ts`：规范形 primitives → ImageData（确定性软件光栅化：路径用
  polygon 填充多边形近似 —— 依赖 `path2d`? 不 —— 用 canvas 2D
  Path2D+fill，浏览器原生，Node 测试用同代码 + node-canvas？定稿：浏览器用
  OffscreenCanvas/Path2D；单测用几何 oracle 而非像素全等）。
- `register.ts`：`map.addImage(imageId, imageData, {pixelRatio: 2, sdf: false})`；
  imageId = `cartoasset-<digest12>-<params12>`（内容+参数寻址，同资产多实例
  去重，S25）；`map.setMissingStyleImageResolver` 兜底 + 显式预注册；
  style reload（runtime invalidateStyle / style.load）后重装；frame 实例各自
  注册（S16）；实例销毁时 removeImage 仅当本实例是注册者（引用计数表在
  runtime 内存，进程内即可 —— 资产内容缓存跨实例共享有界）。
- runtime.ts 接线：MapSpecRuntime reconcile 时，层带 symbol_asset 绑定 →
  生成/更新 MapLibre 层：
  - point_symbol：层 type 投影为 `symbol`，layout:
    `icon-image: imageId, icon-size: size_px/intrinsic_px, icon-anchor: anchor,
    icon-rotate: rotation_deg, icon-allow-overlap: true, icon-ignore-placement:
    true`；icon-rotation-alignment 默认 viewport（MapLibre 缺省）——文档化。
  - area_texture：层 type 保持 `fill`，paint: `fill-pattern: imageId`
    （fill-color 不写 —— MapLibre fill-pattern 下 fill-color 失效；背景色
    烘焙进 pattern 图像）；fill-outline-color 保留既有。
  - 依赖先加载：图层在 image 就绪前不添加（reconcile 顺序保证），缺失/解码
    失败 → typed 观察（maplibre error event 捕获 + 状态面披露
    asset_unavailable，绝不默认圆伪装，S23）。
- headless 编译器 compiler.ts：同一投影规则输出 style.json 的 icon-image/
  fill-pattern（静态导出路径）；Runtime Validator fixture 用
  `__ORIGIN__` 占位 fetch 资产内容？—— 静态 gate 断言：编译输出含正确
  image 引用 + 结构断言（不渲染 raster 的 headless gate 只验证 style 面）。

### Python SVG 出版（mapspec_to_svg.py additive）

- 层带 symbol_asset 绑定 → 资产解析（调用方预解析注入 resolver 结果 dict，
  compile 保持纯函数）→ `<defs><g id="cartoasset-...">primitives</g></defs>`
  （+ 纹理 `<pattern>` or 显式图元 —— 见 spike）→ 要素处 `<use href="#id"
  transform="translate(x,y) rotate(r) scale(s) translate(-ax,-ay)"/>`。
- 纹理面：path fill="url(#cartoasset-pattern-...)"（带洞 evenodd 既有语义
  天然正确，S11）；背景色（非 transparent 时）先画 base path 再画 pattern path。
- 障碍：点符号 = 变换后外接盒（anchor 偏移 + 旋转 + size + stroke），带
  layer/feature 身份进既有 v1 障碍接口；披露近似（保守外接盒）。
- defs 去重：同 (digest, params) 只写一次（S25）；总 defs 数有硬帽。
- 出版（WP09）：submit_export 链路注入资产解析（frozen：解析时锁
  content_digest，manifest 记录资产版本清单，S27）；PNG=rsvg-convert（pattern
  原生支持）；PDF=WeasyPrint —— **spike 必须先验证 WeasyPrint SVG pattern/
  use/clipPath 支持**；不支持则 PDF 通道用显式图元投影（有界图元平铺）并如实
  披露差异，或受限组合 unsupported（S28 诚实披露）。

### TS SVG 孪生（mapspec-to-svg.ts）：同步 Python 语义（图元 + 障碍 + defs 去重）。

## 四、图例（WP08）

- 后端：G34 legend.py 条目循环 additive —— 层带 symbol_asset 时条目附
  `swatch` 描述符（uniform：全体条目同资产；category：按 key 映射；未知类别
  → other 规则条目）；render_legend_box_authored 画 swatch 时消费规范形
  （缩放到 swatch_units 盒，保持纵横比），无 swatch 字段走旧 rect（字节不变）。
- 前端：categorical-legend 等组件 swatch 渲染 —— 有 swatch 描述符时渲染
  资产 SVG（同规范形，≤32px 内联 SVG），否则旧色块。
- 可辨性披露：深/浅底预览（swatch 组件接受背景参数）；灰度预览（filter
  grayscale 近似披露）—— 明示判据（对比度阈值），无自动评分时如实
  not_evaluated。

## 五、UI 与 Pi（WP10）

- 路由 `app/api/routes/cartographic_assets.py`：
  GET /cartographic-assets（列表，分页 scope 查询）
  POST /cartographic-assets（创建：builtin 引用 or SVG 导入 → 规范化）
  GET /cartographic-assets/{id}（元数据+版本列表）
  GET /cartographic-assets/{id}/versions/{rev}/content（规范形 JSON，scope）
  POST /cartographic-assets/{id}/versions（新版本）/publish /retire
  DELETE /cartographic-assets/{id}/versions/{rev}（引用检查）
  POST+GET /cartographic-assets/sessions/{sid}/layers/{lid}/(preview|apply)
  （绑定预览/应用，require_owned_session）
- 工具 `app/tools/cartographic_asset_tools.py`（注册进 _TOOL_MODULES）：
  `webgis_symbol_asset_list / webgis_symbol_asset_create(svg 导入受控) /
  webgis_symbol_layer_preview / webgis_symbol_layer_apply`（expected_revision；
  side_effect=state_mutation; map_mutations=("symbol_asset",)；回执有界：
  资产版本/候选 revision/诊断/预览 ref）。
- 前端面板 `frontend/components/hud/symbol-asset-panel.tsx` 挂载 layer-style-panel
  （点/面矢量层）；目录分页/搜索/预览（内联 SVG）/参数编辑/类别绑定/应用/
  取消/恢复；键盘可操作；错误落具体参数（typed code → 字段定位）。
- chat 工具卡 additive：cartography-result-card 或独立卡片显示资产 swatch。

## 六、测试与验收（WP11–WP12）

- 单元：规范化器（合法/非法 SVG 矩阵 S05–S08）、参数校验（S10）、绑定契约、
  scope（S19）、版本 pin（S04）、引用删除（S18）、CAS（S20）、undo+重启（S21）。
- 集成：真实 Pi 子进程 journey（S29/S30，LLM stub 驱动工具 dispatch 真链）、
  SVG/PNG/PDF 出版回读（S26：SVG 离线打开无外链 + PNG/PDF 独立回读像素/
  文本证据）、浏览器 journey（S01/S15/S16/S17/S24 真实 maplibre mock→
  Playwright 冒烟）。
- 独立几何 oracle：锚点/旋转/洞/DPI 换算可计算断言（不用同一构建函数生成
  expected）。
- 资源上限：资产字节 ≤512KB、SVG 节点 ≤400、坐标 |v| ≤1e4、primitives ≤200、
  每图元点数 ≤512、每 map image 数 ≤64、缓存 ≤32MB、出版 defs ≤128、
  pattern tile ≤256px。

## 七、明确不做

通用素材市场/网络抓取/图片生成/任意矢量设计/复杂线装饰/沿线箭头/新标签
求解器/全局 UI 重构/3D/地理朝向推断/declutter 首版/G41 双变量语义/G36
图像 envelope/G37 模型选择。G34 已知布局限制（frame bounds、续排、比例符号
needed_pt）不修，仅不踩。

## 八、执行顺序

WP-A（纵向最小链）：schema+migration+builtin 规范形+绑定契约+apply 链+
SVG 出版点符号（defs/use）+ 单测 → WP-B：纹理（pattern spike：rsvg/WeasyPrint
实證）+ live 注册（runtime+compiler）+ 图例 swatch → WP-C：SVG 导入器+目录
API/UI 面板 → WP-D：Pi 工具+journey+浏览器 journey → WP-E：出版三格式回读+
资源/失败面+账本 → WP-F：独立 review→修复→复审→PR。
