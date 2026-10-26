# G3D04 · RISKS 与残余限制

## 已知边界（诚实披露，非缺陷隐瞒）

1. **scene 面 RenderWork/ACK 不在信封**：`render_work_envelope.required_capabilities` 只收 layer/source/component（基线设计），scene 应用证据消费既有 `is_compiled/mutation_revision/scene_applied` 面。scene 面 ACK 扩展属 G3D02（其分支正在改 render_work_envelope.py），本方向 verdict 在无渲染证据时 fail-closed（blocked），不冒充确认。
2. **raster-dem 源注册无工具通道（master 基线）**：G3D01（PR #1771）提供 DEM 管线、G3D02 提供 scene 资产面；合入前 acceptance 的 terrain/viewshed 正例不可达（能力门 typed unavailable + alternatives），正例链以挤出证据（fill-extrusion layer stats）达成 3d。单元级 viewshed 真实 dispatch 用合成 DEM GeoTIFF 验证。
3. **viewshed 结果上屏**：viewshed_analysis 返回 stats（visible_fraction/area）+证据，不产结果图层；attach 步骤 typed 占位（`SCENE_RESULT_ATTACH_PENDING_G3D03`）。
4. **程序性相机零回流**（ADR-0227 P1-4 既有边界）：agent 相机动作不产生 Interaction IR 事件；user-wins 靠"用户 viewport 与 spec 偏离"检测（被动可见面），不靠程序性回流。
5. **NL 理解不在离线 lane**：cue 解析是确定性词面投影（双语保守词表），非 LLM 理解；与 G05 的 `nl_understanding not_evaluated` 边界一致。语义歧义（"立体"指挤出还是地形）按词面保守双通道记录。

## 风险

| 风险 | 缓解 |
|---|---|
| cue 正则误命中（如"3d打印"） | 词表保守 + 32 场景回归钉住；误命中只产生 IR 偏好（决策核证据门仍 fail-closed），不会直接触发 mutation |
| 与 G3D02 合并冲突（scene 面） | 本方向零触碰 G3D02 分支文件（见 PR_OVERLAP_MATRIX）；语义接口（SetSceneIntent/SetViewIntent）稳定 |
| SetSceneIntent 回投影改变既有行为 | 仅 user origin 投影（agent origin 不变）；旧断言已按新语义更新并加双向测试 |
| G05 pin 再生 | 官方 CLI `g05_task_acceptance.py pin`；byte 级一致闸 |
| 平台绑定候选漂移 | `test_platform_capabilities` 双向冗余清单 + registry AST 节点校验 |
| 负例恒绿 | expect_error 语义（decline 必须真失败）+ corpus 行为反转抽样（既有测试纪律） |

## 回滚方案

- 全部改动收敛在：新包 `scene3d/`、新工具文件、requirement_ir/taxonomy/projection/plan_graph/artifacts/platform/categories 的 additive 扩展、测试与 pin。
- 回滚 = revert 本 PR（无 schema 迁移：SceneSpec 全默认值、digest 条件包含对存量零漂移、MutationIntent union 未扩大、Interaction IR 词表未动）。
- 生成物（catalog manifest/账本）随 revert 由既有 CLI 再生即可。
