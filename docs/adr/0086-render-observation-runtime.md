# ADR-0086: Render Observation Runtime（渲染级完成度证据）

- 状态：Accepted（2026-08-30）
- 关联：ADR-0081（Map Product Completion Runtime）、ADR-0080（Runtime v3 / runtime-layer-registry）、ADR-0084（制图布局引擎 / cartographic observation → repair 回路）、ADR-0076（SessionPlan 单一计划真相）、ADR-0085（Goal → Product Graph）
- 分支：`feat/gis-render-observed-product-closure`

## 1. Context

ADR-0081 把"任务完成"从"DAG 完成"推进到"地图产品完成"，但其完成度校验
全部作用于 **desired state**（MapSpec / 章节行 / ref descriptor / 组件
enabled）。真实渲染只发生在用户浏览器的 MapLibre 实例上，服务端没有
"浏览器实际渲染了什么"的证据：

```text
MapSpec says layer exists  ≠  MapLibre actually rendered it
```

ADR-0084 已经建立了前端 → 后端的制图观测通道
（`POST /sessions/{sid}/cartographic-observation`，latest-wins 持久化到
map_state 键 `_cartographic_observation`，content fingerprint 拒绝门），
但其证据只服务 style 级质量环：**没有 revision 绑定、没有组件观察、没有
runtime error 通道、没有 settle 语义**，Map Product Finalizer 也完全不
消费它。

## 2. Problem

- Finalizer 对"渲染层缺席"零感知：MapSpec 正确 + MapLibre 层没挂载（ref
  解析挂起、reconcile 失败、style wipe 后未恢复）会被判 complete；
- 观察没有 revision 语义，服务端无法判断"这份观察描述的是不是当前代次的
  地图"；
- 直接建一套渲染验证子系统（第二 map model / 逐帧上报 / 读完整 GeoJSON）
  会违反单一真相与性能契约。

## 3. Decision

**扩展现有 observation 通道（增维不换通道），finalizer 新增渲染级
validator。** 命名沿用现实：`RenderObservation` = 既有
`_cartographic_observation` 证据的 P9 增维。

### 3.1 前端（`mapspec-runtime/render-observation.ts`）

- `collectRenderObservation` 包装既有 `collectCartographicRuntimeObservation`
  （层族收敛证据单源，不建第二采集器），补充：
  - `mapspec_revision`（session-cursor 游标值，诊断性）；
  - `map_idle`（bounded settle：`race(map 'idle', 2.5s)`，永不阻塞渲染）；
  - `components[]`（复用共享 `resolveMapComponents` + MapSpecChrome 同规则
    的 north/scale fallback 镜像 —— O(C)，无第二布局计算）；
  - `runtime_errors[]`（map 'error' 事件 → dedup 有界环 ≤8 条）；
- 触发点不变（reconcile 落定后）；不逐帧 / mousemove / zoom-tick 上报；
- error 监听生命周期 mount → register → cleanup（unmount / 会话切换清环）。

### 3.2 后端（`chat.py` DTO 增量 + 服务端 revision 盖章）

- DTO 增 optional：`components≤32 / runtime_errors≤8 / map_idle / observed_at`
  （旧客户端字段缺席 → 向后兼容）；
- fingerprint 拒绝门通过后，**服务端盖章** `mapspec_revision = 当前
  `_cartographic_mutation_revision``：fingerprint 相等 ⇒ 观察描述的 spec
  内容就是当前 revision 代表的内容；observation 与 mutation 共用 session
  lock，无竞态窗口。客户端 revision 值仅诊断、永不作为守卫（信任边界在
  服务端）；
- 接受后追加 `maybe_finalize_map_product(reason="render_observation")`；
- 观察仍是 session 级 ephemeral（latest-wins、seq 单调、map_state TTL 自然
  到期）—— 不持久化为业务数据。

### 3.3 Finalizer 集成（`render_observation.py` + `map_completion.py`）

- 新 finding codes（单一词表定义在 map_completion）：`render_unverified` /
  `render_revision_stale`（warning）、`render_layer_missing` /
  `render_source_missing` / `render_component_missing` / `render_error`；
- `MapCompletionResult.render_status: verified | issues | stale | unknown |
  not_applicable`；
- revision 防护：**finalizer 只消费 `observation.mapspec_revision == 当前
  revision` 的观察**；stale / absent → warning 披露（不 false verified 也
  不 false failed）；
- 状态语义（兼容优先 + 如实披露）：
  - 无观察能力（旧客户端）→ complete + `render_unverified` warning ——
    旧客户端必须仍能完成；
  - stale → complete + `render_revision_stale`（瞬态，re-observation 自愈）；
  - 匹配 + 校验过 → complete（verified）；
  - 匹配 + 结果层/源/必需组件缺席 → **needs_repair**（不进 failed：期望态
    正确、可经 re-render/re-observation 自愈；无自动修复动作 —— 修复只走
    desired-state 通道，绝不 RenderObservation → 独立改图）；
- 幂等门第三把钥匙：`map_product` 块记录 `render_observation_seq`，新观察
  到达（seq 前进）即打破门 → 重验把披露从 unverified/stale 升级为
  verified（或暴露 render 缺席）；锁内守卫同款：验证依据的 seq 在持久化
  前被覆盖 → 不落块。

## 4. Alternatives

- **新建独立 render-observation endpoint + store**：拒绝 —— 通道/守卫/
  latest-wins/TTL 全部已存在，双通道必然漂移；
- **客户端 revision 作为守卫**：拒绝 —— 信任边界必须在服务端；fingerprint
  门 + 服务端盖章组合已给出等价且更强的保证；
- **observation 即地图状态（render state → 驱动地图）**：拒绝 —— 第二
  map truth；repair 只能走既有 GISMutationBatch / mapspec_store 通道；
- **queryRenderedFeatures 全图证明"画出来了"**：拒绝 —— O(视口要素) 成本
  且与"有界观察"冲突；挂载/可见性证据已足够完成度判定。

## 5. Trade-offs

- observation 只保证"挂载 + 期望可见性"，不证明像素级绘制（瓦片错误是
  warning）—— 完成度判定与瞬时网络质量解耦（transient semantics）；
- 每次接受观察后跑一次幂等 finalizer（门拦截，毫秒级）。

## 6. Compatibility

- 旧前端（不 POST 新字段）→ 观察接受路径不变；无观察会话 → render_status
  unknown + warning，完成语义与 ADR-0081 完全一致；
- 旧后端 + 新前端 → Pydantic 忽略未知字段（观察增维被丢弃，无错误）。

## 7. Performance

- 采集 O(layers + components)；错误环 dedup 有界；settle 是 race 不阻塞；
- 后端盖章 O(1)（锁内同源读取）；finalizer 渲染校验 O(结果层 + slots +
  观察条目)；
- 大数据安全：观察复杂度 ≈ O(层/源/组件数)，与 feature 数无关（前后端
  双重有界，150k features 会话同样只上报 ID/布尔）。

## 8. Failure semantics

- 瞬时 runtime 错误（瓦片失败等）→ warning 披露，不判失败；
- 层/源缺席 → needs_repair（可自愈）；持久化锁/存储故障 → 记录跳过，
  下一触发点重验。

## 9. Migration

无 schema 迁移：`map_product` 块新增 `render_status` /
`render_observation_seq` 键（additive）；旧块缺键 → 门首次重验自愈。

## 10. Future work

- graticule live 渲染器已在本轮落地（见 ADR-0084 附录）；
- observation 驱动的确定性 runtime 修复（如 reassert_spec 重新提交通道）；
- finalizer 渲染证据接入导出 parity 的运行时判定。
