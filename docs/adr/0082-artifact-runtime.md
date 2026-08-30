# ADR-0082: GIS Artifact Runtime（产物记录 / 血缘 / 生命周期）

- Status: accepted
- Date: 2026-08-30
- Supersedes: —（延伸 ADR-0076 SessionPlan 单一计划真相、ADR-0080 统一运行时、ADR-0081 完成度终验）

## Context

ADR-0081 之前，工具产物在系统里只是一根字符串指针（`bound_ref`）：
SessionPlan 能力行持 `bound_ref`，MapSpec source 持 `ref`，layer
provenance 持 `result_ref`。三个绑定面各自为政，没有人回答：

- 这个 ref 是谁、用哪个工具、在哪个能力行上产出的？
- 哪些产物依赖产物 X（重跑 X 之前必须知道下游会失效）？
- 重试 / 重规划后，旧产物是活是死？被谁替换？
- ref 被 TTL/LRU 驱逐后，为什么 MapSpec 还指着它、完成度还判 complete？

`app/lib/gis/artifacts.py` 里有休眠的 `ArtifactTypeRegistry` /
`ArtifactDescriptor` schema（16 个种子类型、producer/lineage 字段），
但运行时零实例化。

## Problem

P1 需要一个产物事实层，同时不得破坏三条既有不变式：

1. SessionPlan 是唯一的持久计划真相（ADR-0076）；
2. MapSpec 是唯一的地图状态真相；
3. RuntimeLayerRegistry 是唯一的运行时图层账本。

直接把 artifact 记录塞进 SessionPlan 行会让计划真相背上数据管理职责；
为产物另起一个可驱动行为的 mutable truth 会制造第四真相。

## Decision

新增 `app/services/artifact_registry.py`：**会话作用域的产物记录层**。

1. **身份 = 既有 ref 字符串**。`artifact_id` 直接复用
   `ref:geojson-…` —— ref 已是会话内唯一且是三个绑定面的既有指针，
   `bound_ref`/`ref`/`result_ref` 天然成为本 registry 的兼容投影，
   零 schema 迁移、零回填。
2. **记录而非驱动**。`ArtifactRecord`（type/producer/inputs/revision/
   expires_at/bbox/feature_count/status/storage_ref/metadata）只是把
   散落的指针上下文记录下来；validator / planner 仍读既有真相。
   registry 绝不反向修改行状态或 spec。
3. **ArtifactGraph 是派生纯函数**。`build_artifact_graph(records)` 在内存
   派生 consumers/producers/lineage/dependents/replacement_chain/
   latest_for_capability —— 不落盘、不缓存为第二真相。
4. **生命周期有限状态**：`valid → superseded`（同 capability 换 ref，
   自动记录 replacement 链）`→ stale`（ref 存活但不在活引用集合）/
   `expired`（store 探测缺失）。session reset 由 session store 的会话
   清理连带清除（registry 本身就是 session 数据，无独立生命周期管理）。
5. **注册 seam**（全部 best-effort，失败降级日志，绝不阻断工具路径）：
   - dispatch seam：ref 铸造即登记（tool 名 + 前缀推断 type）；
   - plan-apply seam：`apply_tool_result` 成功路径补全 capability/
     tool/**实例级血缘**（依赖能力行的当前 bound_ref —— plan_graph 的
     depends_on 是类型级，这里落到具体产物）；锁内直通（lock 透传，
     避免非重入自锁）；
   - raster / chart seam：`ref:raster/*`、`ref:chart-*` 登记为
     raster_surface / chart_spec。
6. **GC 保守**：`collect_orphan_refs` 只删「GC 态 **且** 不在活引用集合
   （章节行 ∪ MapSpec sources ∪ 组件 chartRef）」的 ref，锁内复检活引用。
   活引用绝不删除（哪怕记录态滞后）。
7. **完成度终验接入（修 review C-2）**：`gather_completion_inputs` 把
   MapSpec source refs 一并取 descriptor（并发 gather），
   `validate_layers` 对 layer source 的 ref 做存活校验 —— 行 ref 存活
   而 source ref 被驱逐不再假 complete。磁盘态 `ref:raster/*` 不在
   session store，不参与 store 探测（归 artifact_lifecycle 巡检）。

## Alternatives

- **把 artifact 记录放进 SessionPlan gis_chapter**：拒绝 —— 计划真相
  背上数据生命周期管理，且 supersede/归档语义会把产物记录一起归档。
- **独立 DB 表**：拒绝 —— 产物是会话作用域的临时数据（随 TTL 过期），
  落库引入跨会话生命周期与清理问题。
- **只做 descriptor 增强（给 ref_descriptor 加字段）**：不足以表达
  producer/lineage/replacement（descriptor 是 store 的 O(1) 元数据，
  不应携带跨 ref 图结构）。

## Trade-offs

- 记录层与绑定面可能短暂漂移（注册失败/进程崩溃）—— 接受：registry
  是增值记录，validator 仍以 descriptor/store 为存活事实，漂移只损失
  血缘完整性，不产生错误判定。
- `sweep_statuses` 的 O(refs) descriptor 探测只在显式巡检时发生（并发
  gather），不进工具热路径。
- 同 ref 多 capability（一工具多产物行）→ 一条记录、producer 记最后
  注册的 capability —— 血缘按 inputs 保留，可接受。

## Compatibility

- 旧会话（无 registry 记录）：validator 退化为既有行为（descriptor
  驱动），无回归。
- SessionPlan / MapSpec schema 零变化。
- `app/lib/gis/artifacts.py`（休眠 schema 层）保持原样 —— registry 的
  `artifact_type` 词表与其类型注册表语义对齐，后续可统一。

## Performance

- 注册：每工具结果 O(1) 次账本 read-modify-write（≤128 条记录，
  先淘汰 GC 态再 LRU）；dispatch + apply 两个 seam 各一次 upsert。
- 血缘查询：内存派生图，O(V+E)。
- 完成度终验新增的 source-ref descriptor 取数并入既有并发 gather
  （无新增串行往返）。

## Failure semantics

- 注册失败（锁降级/存储异常）：日志 + 返回 None，工具结果路径不受
  影响（产物本身已在 store）。
- `lock.lost`：拒绝写共享状态。
- 坏记录（反序列化失败）：单条跳过，账本整体可用。
- GC 失败：下次重试；绝不删活引用。

## Migration

无迁移：新会话起自然产生记录；旧会话无记录时一切照旧。

## Future work

- 替换 #1068(E-10) 的 chartRef 手工删除为统一 GC；
- P4 product-graph 以 `artifact_dependency_report` 为血缘输入；
- registry 状态反哺 Pi 投影（"产物 X 过期，下游 Y 需重跑"）。
