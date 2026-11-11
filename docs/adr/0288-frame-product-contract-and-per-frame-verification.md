# ADR-0288：多画幅产品契约（FrameProductContract.v1）与逐画幅终验

- 状态：Accepted（G15-MULTIFRAME-CARTOGRAPHY）
- 日期：2026-11-11
- 关联：ADR-0118（V5 导出 frames）、ADR-0211（纸型 profile）、
  TemporalViewContract.v1（ADR 前身 v1.8）、C14 PublicationIR、
  PR #1975（出版制图）、PR #2034（Map Studio 单源）

## Context

`small_multiple_map` 长期 `runtime_status="planned"`：现有能力是「同一
committed MapSpec 的请求级多帧导出」（ExportRequest.frames → canvas
grid / pdf pages）与 atlas 逐帧分页 —— 二者都没有**产品内多画幅语义**：
画幅无稳定身份、无数据绑定、无共享表达契约、无逐画幅编辑/联动/终验。
用户说「三年并排、同一分级、联动查看、改第二幅、导出一张图版」时，
系统只能给临时拼图。

## Decision

### D1 一份权威 MapSpec + `frame_product` v2.0 additive 顶层块

- 画幅**身份**单一事实 = `layout.frames[].id`（既有 MapSpecFrame）；
  产品块只持产品语义（`ProductFrameSlot`：数据绑定/同步组/图例策略），
  绝不复刻几何 —— 引用悬空在 handler 锁内拒绝。
- 写入面：`SetFrameProductIntent`（config/patch/remove，镜像
  SetTemporalViewIntent）+ `SetLayoutIntent.frames`（layout.frames 的
  首个意图写面；MapSpecFrame 复验 + id 唯一 + 整表替换不删产品引用帧）。
- 版本阶梯 1.9 → 2.0（identity upgrader；TS projection/manifest/corpus
  goldens 同步再生）。

### D2 画幅有效文档 = 纯函数解析（WP04）

`frame_products/resolver.resolve_frame_document`：layerOverrides（逐键
merge）→ where（feature_pages AST）→ 显式时间窗（temporal [start,end)；
全局 cursor 不参与帧解析）→ 共享分级盖章（breaks/labels/palette_colors
同步重算）。帧间零共享可变结构；重复解析指纹逐位一致；悬空绑定 typed
拒绝。

### D3 共享表达是科学契约（WP05）

`shared_expression.compute`：breaks 在跨画幅**真实数据并集**上只算一次；
资格 = 字段一致 + 测量语义一致（ratio vs rate 拒绝共享）+ rate 必须有
分母声明；domain_status complete/partial/unknown 如实；拒绝时**回落独立
图例 + 披露**，绝不「各自分级但标称共享」。

### D4 同页并置出版（WP10）

- PublicationIR `PageLayoutIR.frame_slots`（additive；None = 既有单帧
  语义）+ `AtlasPolicy.driver="product"`（显式选择；旧 frames 驱动语义
  零变化，IR goldens 仅增 `frame_slots: null`）。
- 页编译：逐槽 resolver → 单帧编译器槽尺寸编译（shared 槽无 chrome——
  图例由页面共享带承载）→ id/clipPath 按槽命名空间化组合；任一槽失败
  → 整页 typed 失败（`product_frame_unresolvable/render_failed`）。

### D5 工具面与情境投影（WP09）

`frame_product_plan/bind/update/status/unbind` 五工具（Pi 与 legacy 同
面）；refs-only 摘要携带 active_frame/同步组/共享分级 —— 「只改第二幅
标题」经键级 patch 精确落位。registry 最小 additive：update 的 patch 载
荷（嵌套 data.ref/where/time_window 是声明不是数据请求）加入解引用跳过
表（webgis_map_product 同门）。

### D6 逐画幅终验（WP11）

`verification.verify_frame_product`：frame_id+revision 对账；观察分级
live_observed > export_verified > snapshot（快照不冒充实时）>
not_observed；空帧显式 `frame_no_data`；required 集合按目标收窄；
单帧成功不汇总为产品完成。消费面：`frame_product_status.verification`
+ goal_satisfaction `_frame_product_evidence`（bounded ≤12 行）。

### D7 资源边界

- 产品画幅 ≤ 12（MAX_PRODUCT_FRAMES；常见 2/3/4/6）；同步组 ≤ 4。
- 相机同步组：**契约 + 纯函数已交付**（`frame-sync.ts`：resolveSyncPair
  N 方推广，幂等收敛断回声，vitest 锁定）；多画幅交互视窗运行时（快照
  LRU + 按需激活，活跃 MapLibre ≤ 2）**未接线** —— 本 PR 面板可声明
  sync_group，联动传播待交互视窗落地（诚实边界，不冒充已交付）。
- 导出：槽 SVG 各 ≤8MB、页 ≤ 8MB、总 32MB（沿用出版预算）。

### D8 诚实可用性

- `small_multiple_map` → native（实证组合 = polygon/point ×
  admin_aggregate_table/grid_aggregate × publication 产品页导出）；
  raster 未实证 —— 诚实留在 pitfalls（V5 导出 frames 承接降级）。
- 前端 canvas PNG/SVG 网格不承载 layerOverrides/纸型/共享图例带 ——
  `productFramesToExportFrames` 返回 limitations 披露；精确语义走后端
  `atlas.driver=product`（PDF/vector）。近似图不冒充原样导出。

## Consequences

- MapSpec 文档版本推进 2.0；存量 1.x spec 行为与指纹逐位不变（纯加法）。
- atlas 逐帧分页、ExportRequest.frames、temporal 契约全部保持 ——
  产品页是显式选择的第四条出口，不覆盖旧路。
- 后续（非本线）：栅格多画幅、六幅以上交互布局、frame 级标注覆写
  （G16 交界）。
