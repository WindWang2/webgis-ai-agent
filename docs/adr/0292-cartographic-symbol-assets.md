# ADR-0292 — 可复用点符号与面纹理资产工作台（G40）

状态：Accepted（2026-11-15，G40 任务范围）
相关：ADR-0051（MapSpec mutation CAS）、ADR-0289（G16 障碍）、
ADR-0290（G34 纸面版式/units corpus）、ADR-0214（模板资产生命周期先例）、
ADR-0283（导出障碍披露）。编号查重：`docs/adr/` 现存最高 0290（0286-0288
历史重号已由编号契约测试记录）；本 ADR 使用唯一 0292（0291 已被 G36 PR
占用，见 open PR #2118 的 docs/adr/0291-pi-visual-context.md）。

## 背景与问题

MapSpec 图层此前只有 circle 半径/颜色、线型、fill 纯色、标签与比例符号：
**没有图形资产**——用户与 Pi 无法让点层用三角形/五角星/受控导入图形、
让面层用可解释晕线纹理，更没有可复用、可版本化、跨图层共享且钉版本的
资产目录。MapLibre 的 icon-image/fill-pattern/addImage 原生能力从未接线
（全仓 addImage 调用为 0）；grammar 的 texture 标注 unsupported。

## 决定

1. **单一内容契约 `symbol_asset.v1`**（`app/lib/cartography/symbol_asset.py`）：
   资产 = 有界规范形图元文档（viewBox/primitives/纹理 pattern 描述 +
   content_digest canonical 摘要），不是原始 SVG 字符串。SVG 导入只在
   入口解析一次（`svg_import.py` 受控子集，typed 拒绝 script/外链/url()/
   text/clip/filter/渐变/循环 use；defusedxml），预览/live/SVG/PNG/PDF/
   图例全部消费同一规范形 —— 禁止两套清洗口径。
2. **身份 = asset_id + revision + content_digest**（名字只是显示名）。
   MapSpec 图层 additive 绑定块 `symbol_asset`（extra=forbid）只携带
   三元组 + typed 实例参数 + 可选类别映射（key_type typed，S02 不并类）
   + prev_paint_snapshot；二进制/URL 绝不进 MapSpec。
3. **持久目录照 0098 先例**：`cartographic_symbol_assets` +
   不可变版本表（draft→publish→retire 软退役→restore 造新 revision）；
   内容走 durable BlobStore（sha256 CAS）；scope 纪律照抄
   composition_assets（不可见=404；DB 资产不进全局注册表；同内容去重
   不合并 owner）。删除版本先做会话 MapSpec 引用扫描（S18）。
4. **写入面复用 MapSpec 生命周期引擎**：UI/Pi 同一
   `apply_gis_mutation(UpsertLayerIntent, expected_revision)` 单笔 CAS +
   checkpoint + undo；只写本域键；agent 命中锁层由引擎统一拒绝（S22）；
   解绑恢复 prev_paint_snapshot。预览零 mutation。
5. **渲染投影双端同语义**：live = MapSpecRuntime 层投影（点符号→
   symbol/icon-*（icon-allow-overlap=true+ignore-placement 碰撞合同，
   declutter 显式 UNSUPPORTED_DECLUTTER 拒绝）；面纹理→fill-pattern，
   类别拆过滤子层 ≤16）；headless compiler 同一合同输出 image 引用 +
   symbolAssets 清单。图像经 `map.addImage(id, ImageData, {pixelRatio:2})`
   内容寻址注册（digest+params 去重；引用计数跨 frame 实例，S16；style
   reload 经 reconcile 重装，S15；迟到请求 epoch 丢弃，S17）。
6. **出版**：Python SVG 编译消费规范形（defs/use 变换链与 icon-anchor/
   rotate/size 同式；纹理 `<pattern patternTransform>`；evenodd 洞既有）；
   点符号障碍 = 变换后保守外接盒进 G16 v1 接口（披露近似）。资产在导出
   期冻结（manifest.symbol_assets 记录 resolved/unresolved，S27）。
   **实证 librsvg 2.63.4 回归**：CLI -w/-h 与内在尺寸不一致时 pattern
   内容丢失 → `prepare_svg_for_rasterize` 改写根尺寸规避（无纹理路径
   字节不变）；WeasyPrint 70 pattern/use/洞实证通过。
7. **图例同语义 swatch**：G34 legend.py 条目附 swatch 描述符（规范形 +
   实例参数预渲染 SVG），render_legend_box_authored 消费；无 legend_spec
   的绑定层补单条目（S12 两个同色不同向纹理可分）；屏上图例（categorical）
   消费同一规范形内联 SVG。

## 后果

- visual_variables：texture unsupported→partial、shape/orientation 注释
  更新（受控资产通道已验证；自由语义未成契约，不整体升 supported）。
- 不做：通用素材市场/网络抓取/图片生成/任意矢量设计/沿线箭头/新标签
  求解器/declutter 首版/map 对齐旋转/G41 双变量语义。
- MVT/ref 源绑定放行（数据面由源 profile 披露），但 Python 导出路径只
  画已水合要素（与既有层一致）；live 经 MapLibre 原生 source 消费。
