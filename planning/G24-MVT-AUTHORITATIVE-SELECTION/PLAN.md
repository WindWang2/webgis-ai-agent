# G24 PLAN — MVT 大图层的权威拾取、选择与编辑连接

BASE_SHA `b8f2a6ecc`（origin/master 2026-11-11）。分支 `zcode/g24-mvt-authoritative-selection-20261111T151706Z`。
规划目录：`planning/G24-MVT-AUTHORITATIVE-SELECTION/`。

## 目标拆解（工作包 → 实际增量）

基线核实结论（详见 RECON.md / INTERFACES.md）：单击权威回填、分页属性表、
feature_edit 事务链在 master 已存在；G24 的真实缺口是编辑手势拾取断链、
选择全集无权威承载、id 身份链三侧漂移、大整数精度、Agent 侧选择核实缺失。

| WP | 计划 | 实际落地 | 状态 |
|---|---|---|---|
| P0 数据到瓦片身份审计 | 建立映射表、明确边界 | ① `properties._wfid` 补齐后端三条 id 链并统一为 wfid-first（`feature_pages.py`、`mvt.py::_chain_stable_id`、`layer.py::_find_feature_by_id`、`selection_handles._chain_id_of`）——修「已编辑数据集按 wfid 回查 404」；② MVT int 属性 \|\|v\|\|>2^53-1 字符串下行（JS Number 精度防线）；③ `ambiguous_target` typed finding：重复自然键编辑拒绝（first-wins 会编辑到用户没点的对象）；④ 审计表见 INTERFACES.md §1 | verified |
| P1 服务端权威 lookup | 一小组 id 的完整 geometry/properties + 预算 | 复用 `GET /layers/data/{ref}/feature/{fid}`（index O(1) + positional v-guard，已在 master）+ 新增 selection handles 的 `ids` scope（≤200 有界、顶点/扫描预算承袭 page_features、缺失明示）；预算披露走 reason_codes | verified |
| P2 客户端候选拾取与精确命中 | queryRenderedFeatures 只出候选 id；权威几何精确命中 | 新模块 `frontend/lib/edit/mvt-pick.ts`（候选去重 ≤5 → 权威回查 → `geometry-hit.ts` 精确命中：A08 洞/A09 线段中段/A10 Multi*）+ `feature-layer-editor` 接线（inline 层本地路径不回退 A01）；AbortSignal 全程；位置 id 回退带 v guard | verified |
| P3 权威选择集 | 有界 ids / 服务端不透明 handle | `app/services/selection_handles.py` + 路由 5 端点（create/meta/features/materialize/release）：封闭 scope（bbox/where/ids）、AM 拆分（A14）、计数诚实（count_exact）、TTL+LRU+三重绑定、revision 失效 410（A22）；客户端 selection-store 增 handle 字段 + 释放挂钩 + map-panel 截断框选异步创建句柄（epoch+revision 迟到守卫） | verified |
| P4 属性表与消费者贯通 | 分页/排序/字段一致；编辑加载完整几何；消费者同一 scope | table-panel「选中集」视图（handle 全集分页 + 计数如实 + 410 解释 + A13）；materialize 端点给 field_compute 等只认 selection_ref 的消费者；编辑路径经 P2 权威几何调用既有 preview/approve；lineage 更新 selection（A19） | verified（field_compute 物化通道有测试；UI 内深接线见 REVIEW 边界） |
| P5 并发与用户体验 | 迟到不覆盖、失效解释、血缘、反馈 | 单选回填 seq 守卫（既有）；brush 句柄创建迟到守卫；句柄清空/替换/会话切换释放；`applyLineageToSelection`（退役移除+同层新生并入、纯 create 不并入）；matchedAuthoritative/selectionStale i18n 反馈 | verified |
| P6 Pi 接入 | 小范围工具、refs-only、真实调用 | `app/tools/selection_tools.py`：`webgis_pick_detail`（几何摘要不含坐标）、`webgis_selection_inspect`（count/完整性/revision/有界 id 分页；跨会话 not_found、stale typed）；真实 ToolRegistry 注册 + 旅程测试（选中→核实→编辑→旧句柄失效→血缘） | verified |
| P7 大图层验证 | 10万/百万级 + holes/多部件/跨tile fixture | `test_g24_scale_evidence.py`：100k 档（14.6MB）单要素回查 0 线性扫描、132B 响应；1M 档 G24_MILLION=1 显式开启（分档披露）；带洞面/多部件跨瓦片编码 fixture；前端侧断言 pick 不整包水合（mvt-pick.test） | verified（100k 档） |

## 明确不做（边界）

- 不重写 G14 编辑事务/undo/拓扑/标注布局器（复用）；
- 不改 G27 的分类/抽稀/LOD 政策（`MAX_TILE_FEATURES`、简化阈值不动）；
- 不接管 #2064/#2004 类通用 frontend/lib 修复（#2004 已修于 master，登记即可）；
- 不给 fabric catalog（Data Fabric 数据集版本面）做选择句柄 —— 本方向 scope 是会话 ref 数据面（MVT 编辑/显示同面）；
- Pi vendor 升级归 G22。

## 关键取舍记录

1. **wfid-first 链序**：物化后 WFID 是唯一跨 revision 寻址键 → 三侧统一
   `properties._wfid → 顶层 id → 链内键`。副作用：`feature_id_index` 键由
   原始 id 变为 wfid（已编辑数据集），原始 id 仍可经线性回退寻址 —— 行为
   更正确而非破坏。`feature_edit.identity.resolve_feature_id`（物化前映射）
   保持顶层优先 + wfid 显式分支，回归测试锁定。
2. **>2^53 int 下行为字符串**：改动瓦片字节（ETag 内容寻址自然失效）；
   id 链两侧都是 str() 比较，字符串往返精确。
3. **bbox scope 语义 = 粗滤相交**（保守超集，`bbox_coarse` 披露）——与
   /features 既有语义一致，不发明精确相交；within 属后续增强。
4. **handle 进程内 LRU + TTL**：重启失效（诚实 410）是设计；不持久化，
   避免变成长期 capability。
