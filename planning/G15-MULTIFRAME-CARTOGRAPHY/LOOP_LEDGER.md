# G15 LOOP_LEDGER — 执行账本

## 轮次记录

- **R1（WP01-02）**：基线复核（BASE_SHA=816705218 = origin/master）、PR 排重、worktree 建立；
  frame_product.v1 schema + SetFrameProductIntent/SetLayoutIntent.frames 写面 + parity corpus。
  验证：schema 套件 + TS projection + parity corpus 70 通过。commit 4c639c938
- **R2（WP03-05）**：resolver（帧隔离/确定性/共享分级盖章）+ shared_expression（并集分级一次/
  资格拒绝）+ bind/plan/update/unbind/status 服务。验证：31 测试。commit e7000f9c9
- **R3（WP10）**：PublicationIR frame_slots + driver=product + 页编译组合 + 路由/请求面；
  真实 WeasyPrint PDF 旅程。IR goldens 再生（additive only）。commit cf417dff7
- **R4（WP09）**：五工具 + bootstrap + session 注入 + deref skip。commit f4adb19ea
- **R5（WP11）**：verification + status/证据消费。commit 2417f2be9
- **R6（WP06-08 前端）**：frame-product lib + panel（Studio frames 子页签）+ N 方同步组 +
  spec-frames 诚实适配器 + i18n；j14 浏览器旅程（mock 档真实跑通）。commit 1f0225090
- **R7（WP12）**：真实导出工件（PDF 文本层 + pdftoppm 像素核验 + SVG 结构）、基准 2/4/6、
  native flip + catalog 再生 + 能力包注册 + ADR-0288。
  视觉验收两轮：首轮 FAIL（空白帧/标题重叠/图例失配）→ 根因（WeasyPrint SVG 渲染器
  不支持 g transform/多嵌套 svg —— 实证探针）+ viewBox 保留 + paint 重投影 + 图例透明度
  对齐 → 再审（结构全过；残余 2 项已修：fixture 分级跨类 + extent 覆盖几何）。
  commit f9eadc89a
- **R8**：全量回归 6946 通过 / 0 失败；ADR 0286/0287 撞号为基线既有（BASE_SHA 复现证据）。

## 关键决定

- 画幅身份 = layout.frames[].id（产品块只持产品语义）—— 避免 G16/前端双写漂移。
- 产品页 PDF 走 HTML 组合（WeasyPrint SVG 限制实证）；SVG 单文件面保留组合 SVG。
- 小倍数 native 仅 polygon/point（raster 未实证，诚实留 pitfalls）。

## 下一步

- 独立 review → 修复 → 复审 → push + PR。
