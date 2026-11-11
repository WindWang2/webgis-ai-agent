# G24 — MVT 大图层的权威拾取、选择与编辑连接

## 功能增量

大图层（MVT）不只可看：点击/框选 → 服务端权威对象解析 → 属性/选择集 → 既有编辑（G14）、标注（G16）、字段计算（G19）入口贯通。

1. **身份链统一（P0）**：`properties._wfid` 补齐并统一三条后端 id 链为 wfid-first（前端自 G14 已如此）——修复「已编辑数据集在 MVT 索引路径按 wfid 回查 404」；MVT 整数属性超出 JS 安全整数界（±(2^53−1)）以字符串下行；重复自然键编辑 typed 拒绝（`ambiguous_target`，first-wins 会编辑到用户没点的对象）。
2. **权威拾取（P2）**：新 `mvt-pick`（候选 id 去重 ≤5 → `/feature/{id}` 权威回查 → 原始几何精确命中）+ `geometry-hit`（Polygon 洞排除、长线段中段、Multi* 真实部件）。编辑手势（delete/vertex/split）对 MVT 层从「恒空集」接通到权威链；inline 层本地路径不回退。
3. **服务端选择集句柄（P3）**：`/layers/data/{ref}/selection` 五端点（create/meta/features/materialize/release）：封闭 scope（bbox/where/ids≤200）、AM 拆分、计数诚实（count_exact）、TTL+LRU+session/ref/revision 三重绑定；数据刷新 → 410 stale。框选截断时 map-panel 异步建句柄（迟到守卫），选择 store 增 handle 字段与释放挂钩。
4. **属性表与消费者（P4）**：table-panel「选中集」分页视图（总数/完整性如实、410 解释）；materialize 端点为 field_compute 等只认 selection_ref 的消费者提供可靠物化（超预算 typed 拒绝）；split/merge 血缘更新选择集（退役移除+同层新生并入）。
5. **Pi 工具（P6）**：`webgis_pick_detail`（稳定 id 权威详情，几何摘要不含坐标）、`webgis_selection_inspect`（句柄全集计数/完整性/有界 id 分页）——真实 ToolRegistry 注册，tier 2 / cacheable_read。
6. **规模证据（P7）**：100k 点档实测 —— 14.6MB 数据集单要素回查 132B 响应、零线性扫描（索引 O(1) 计数器钉住）；1M 档 `G24_MILLION=1` 显式开启；带洞面/多部件跨瓦片 fixture。

## 排除项 / 边界

- 真实浏览器 E2E（A25）未在本 worktree 执行（宿主重资源被并行方向占用）——组件级 vitest + 服务端真实旅程覆盖同等断言面，浏览器级留待集成。
- fieldcompute/导出 UI 内的「选择集」入口按钮未新增（通道+测试已就绪，UI 深接线留给消费方向）。
- `tests/unit/api_contract/test_api_v2.py` 4 errors 与前端 4 例失败为 **master 预存**（干净 BASE 同条件复现；本方向未触碰对应文件，见 LOOP_LEDGER）。
- 不改 G27 表达 LOD / G14 事务语义 / Pi vendor（G22）。

## 证据

- 后端新增/回归：`test_selection_handles` 16、`test_selection_handle_routes` 10、`test_selection_tools` 5、`test_g24_identity_guards` 5、`test_g24_scale_evidence` 3+1(gated)、acceptance journey 2；既有 feature_edit 123 / MVT 46 / feature_pages 11 全绿。
- 前端：新增 25 测试 + 既有 selection/map-panel/map-components 全绿；`tsc --noEmit` 0；eslint 改动文件 0/0。
- 性能：见 TEST_EVIDENCE.md §3（100k 档 132B / 0 扫描 / index 4.4s 一次）。
- OpenAPI 快照 additive 刷新（0 breaking）。

## 兼容 / 回退

- 全部为加性变更：id 链统一只影响「物化后数据集」的索引键选择（原始 id 仍可线性回退寻址）；瓦片字节仅对 >2^53 整数属性数据集变化（ETag 内容寻址自然失效）；选择 store 新字段可选。
- 回退：revert 单分支即可；无 schema 迁移、无持久化状态（句柄进程内 LRU）。

## Review

独立 reviewer（未参与实现）完成 BASE→HEAD 全量审查 + 关键套件复跑：见 `planning/G24-MVT-AUTHORITATIVE-SELECTION/REVIEW.md`（findings → 修复 → 复验结论）。
