# ADR-0275: 三维制图产品组合（3D Cartographic Product Composition）

- 状态：Accepted
- 日期：2026-10-27
- 方向：G3D05（三维智能制图产品、场景化 UI、StoryMap 与导出质量）
- 关联：ADR-0199（场景协议）、ADR-0238（模板库 V3）、ADR-0214（组合契约/ABI）、
  ADR-0228（产品 manifest）、ADR-0196（StoryMap）、ADR-0211（导出 parity）、ADR-0155（SVG 表达力）

## 背景

三维场景协议（ADR-0199）解决了"场景正确性"：SceneIntent→SceneDecision fail-closed、
terrain 源校验、挤出证据门。但从**正确的三维场景**到**可交付的制图产品**之间存在缺口：

1. 组合模板库 10 个 pack 全部 2D —— 无三维产品模板、无场景披露组件；
2. 用户与 Agent 的场景操作分叉：`set_map_scene` 写 spec，但 `is3D` 闸门只由
   用户开关驱动（Agent 的 3D 场景从不激活）；无 exaggeration/pitch/bearing 用户面；
3. 透视视角下屏幕比例尺失真但导出件不披露；矢量导出天然压平 3D 但无 typed 收据；
4. StoryMap 章节无场景状态（keyframe 只有相机无场景档）、无会话持久化
  （compile/export 全无状态）、播放中用户接管相机与自动播放争抢。

## 决策

### D1 三维组合模板 pack（`composition_packs/scene_3d.py`）

三个模板：`terrain_3d_scene`（地形+晕渲+等高线）、`urban_3d_skyline`（建筑挤出+
行政区统计）、`viewshed_scene_3d`（视域+地形）。诚实边界：

- 输出目标仅 `interactive`+`png` —— 矢量目标（pdf/svg/print）由导出管线**显式降级**
  并附收据，模板不得声明矢量支持 3D（静态媒介不出 3D，scene_planning 同源）；
- `scene_disclosure` 槽**必填**：三维产品不带场景披露 = 误导性产品，conformance
  按 `required_slot_missing` 拦截。

### D2 新组件类型（防第二真相纪律）

- `scene_disclosure`（disclosure 族）：内容**绑定 spec.scene 单一真相**
  （mode/exaggeration/垂直基准/比例尺可信度随 pitch 派生），options 只承载
  样式/附注 —— payload 校验器**禁止** options 复制场景字段；
- `elevation_profile`（analysis 族）：`{distance_m, elevation_m}` 单调有界序列或
  `ref:profile-*` 工件（与 G3D03 量测契约的接口约定）；导出复用图表渲染链
  （剖面本质是折线图），`data-kind="profile"` 独立标记入语义语料。
- 全链路接线：contracts ComponentType+payload 校验器 → mapspec_schema →
  registry descriptor → taxonomy → ABI META（props 接地）→ renderers 真值矩阵
  （live/canvas/矢量孪生三链 publication）→ seed 组件模板 → catalog 再生成。

### D3 场景质量门扩展（scene_quality）

三码：`SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE`（有效 pitch=view 优先回退
camera；>10° 且比例尺在场：缺披露组件 warning / 已披露 info）、
`SCENE_LABEL_OCCLUSION_RISK`（>45°+标注层 info）、`SCENE_EXPORT_VECTOR_DEGRADE`
（导出目标含矢量且 mode≠2d：info —— 降级合法但未披露的降级是误导）。
`set_map_scene` 工具证据回传 `scene_quality`（never-raise）。

### D4 人机同真相的场景 mutation（SetSceneBody）

`SetSceneBody(intent="set_scene")` 入 `MapSpecMutationUnion` → 复用
`SetSceneIntent` 事务管线（origin=user、client_mutation_id 幂等、409/503 语义
与既有一致）—— **零第二状态机**。前端 `scene-mutation.ts` 走既有
`enqueueUserMutation` 串行链，响应整本 mapspec 经 `commitMapSpecDocument` 进
committed 真相。map-panel 补 Agent→is3D **单向桥**（scene.mode∈{3d,2.5d} 拉起
is3D；缺席不强制关闭 —— 用户本地开关不被服务端状态回拽）；
`sceneCameraIdentity` 并入 terrain 身份（exaggeration-only 变更也重放地形）。

### D5 导出诚实语义

- 诊断词表：`scale_unreliable_perspective`（warning）/`svg_3d_perspective_degrade`
  （info，后端孪生编译期必发 + 前端矢量路径同语义）；
- 前端判定单源 `scene-export-disclosure.ts`（`SCALE_TRUST_PITCH_MAX=10` 与后端
  同值、契约测试互锁）；导出比例尺**披露而非移除**：透视时比例尺下加注
  「仅供参考/不可信」小字，`scene_disclosure` 在场时用较软文案；
- `product_manifest` 场景溯源块：mode/terrain(source/夸张/垂直单位/基准)/
  camera(pitch/bearing)/scale_trust —— 交互 3D 导出的可复现锚点。

### D6 StoryMap v1.1 场景章节与持久化

- spec v1.1（additive）：`CameraKeyframe.scene{mode,exaggeration}`（只覆盖叙事
  镜头两旋钮，完整场景配置仍由会话 MapSpec.scene 持有 —— 防第二真相）、
  `StoryChapter.visible_layer_ids`（≤32，空=不改可见性）；
- 编译器 `scene_enabled` 证据门：会话壳检查 mapspec 有 raster-dem 源才投影
  弧角色场景档（解剖/推演 2.5d+1.5 温和夸张），无证据不虚构立体叙事；
- `story_map_assets` 表（migration 0099，会话唯一行+revision 单调）+
  `POST/GET /storymap/sessions/{id}/spec`（所有权守卫；损坏资产 typed 422
  不伪造默认）；播放端装载优先恢复资产，无资产才现场编译；
- 播放器：章节场景经 `commitUserScene` 应用（与场景控制器/Agent 同管线）；
  **用户接管相机（dragstart/pitchstart/rotatestart）→ 自动播放暂停** +
  恢复按钮（用户控制优先）。

### D7 场景控制器（scene-controller）

2D/2.5D/3D 分段控制、exaggeration 滑杆（无 raster-dem 证据禁用+解释）、
pitch(0–85)/bearing(±180) 步进、垂直基准信息行（缺省"未声明"）、
reduced-motion→transition_ms=0。显示态读 committed spec（本地无场景状态），
操作走 D4 通道；i18n 双语、键盘可达（radiogroup/slider/stepper 语义）。

## 后果

- 三维产品的"可交付性"有了组合模板层的表达：披露必填、矢量降级显式、
  比例尺诚实 —— 用户端不出现"透视截图冒充可量测产品"；
- 人机场景操作汇合于 SetSceneIntent 单管线，SSE/HTTP 双信道语义不变；
- 代价：组件注册链新增 2 类型（catalog/ABI/taxonomy 全链同步维护）；
  StoryMap 新表与迁移（0099，additive 可重入）。
- 已知边界：前端披露组件 zh 文案暂以模块级常量承载（过双 CJK 门禁），
  i18n 键化留待下一批；`elevation_profile` 的 ref 工件生产端依赖 G3D03
  量测契约（未合并），当前由 inline 数据/fixture 驱动，不虚构来源。
