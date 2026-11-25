# Acceptance — WP 验收表（交付时逐项核对）

| WP | 验收条款 | 证据 | 状态 |
| --- | --- | --- | --- |
| WP1 | 模型作者契约（nodes/ports/params/edges/输出义务/来源），从既有工作流/plan 导入草案 | `app/schemas/model_authoring_schema.py` + `contracts.py`；`imports.py`（from_project_workflow/from_session_plan_steps 复用 promotion）；test_imports | ✅ 代码+测试 |
| WP1 | 禁止任意代码/SQL/网络/凭证；结构/AST 严格上限 | schema closed-vocabulary + extra=forbid + 上限（32 节点/64 边/16 参数/256KB）；无任何执行语言 | ✅ |
| WP2 | editor graph → 既有 compiler/plan 映射 + roundtrip | compile.py（TypedWorkflowGraph→WorkflowPackage，指纹同源）；roundtrip_projection | ✅ |
| WP2 | 静态验证（端口/CRS 类/角色/参数/能力可用性），typed reason + 最小修复建议 | validate.py 20 finding 码（双语 detail；CRS 类由运行时绑定门裁决）；发布/实例化强制 error=0 | ✅ |
| WP2 | 模型与绑定实例分离；发布不带私有 ref/credential | 发布只产包（声明）；绑定只在实例化（session ref）；validate 拒未声明引用 | ✅ |
| WP2 | 不可行时不能启动；参数缺失引导填写 | MODEL_INVALID 阻断发布；REQUIRED_PARAM_MISSING 阻断实例化 | ✅ |
| WP3 | 可增删节点/连线/参数面板/拖动/合法性即时提示/选中预览/运行 overlay/撤销恢复 | B 交付：自绘 SVG 画布/palette/props/findings/run-overlay/undo 接线；64 组件测试绿 | ✅ |
| WP3 | Pi 经标准工具注册生成/inspect/validate/patch/bind/submit；感知 typed findings | 8 个 webgis_model_builder_* 工具（同 service 链）；journey 九步真实 dispatch 绿 | ✅ |
| WP3 | 不可执行代码节点不能注入 | patch 白名单 op（PATCH_OP_UNKNOWN 拒绝）；无代码类节点词表 | ✅ |
| WP4 | 草稿→校验→版本化发布；immutable graph/compiled fingerprints；撤回/复制 | publish（semver/指纹）+ PackageRegistry deprecated；导出/导入复制 | ✅ |
| WP4 | 用户模型为既有 registry 中可调用条目；不伪造 capability ID | umodel_ 包经既有 PackageRegistry；capability 引用经注册表校验 | ✅ |
| WP4 | 无权限不默认全局可见 | owner/org 域隔离（他人 MODEL_NOT_PUBLISHED，测试锁定） | ✅ |
| WP4 | 声明式导入导出小文档；schema 校验；不自动执行 | export/import（format webgis-model v1）；parse 拒绝未知/坏文档 | ✅ |
| WP5 | 绑定模型 revision+输入快照；节点 attempt/证据/解释 | 包指纹+input fingerprints（既有 instance 行）；projection/explain 直通 | ✅ |
| WP5 | 失败定位/合法 retry/cancel/resume；参数修改生成新实例并失效下游 | 既有 retry/cancel/changes+recompute REST；node_params 注入 | ✅（复用） |
| WP5 | 节点真实中间结果/地图/统计；输出状态来自 runtime receipts | projection+events；e2e 断言产物（多边形/统计/blob） | ✅ |
| WP5 | 禁止只看 graph 全绿而结果缺失 | 节点 output_ref/evidence 为真值源；e2e 校验产物实体 | ✅ |
| WP6 | 四个真正可跑模型+fixture+正负例 | templates.py 四模板（validate 零 error）+ A 模板真实 driver + 18 安全负例 | ✅ |
| WP6 | 中文旅程：生成→调参→验证→执行→复用→发布→换城重绑→恶意补丁拒绝 | journey 九步真实 dispatch 全链绿 | ✅ |
| WP6 | 20/100 节点性能上限 | 20 节点 validate+compile 4.2ms / 100 节点 wire+指纹 0.6ms（阈值 2s） | ✅ |

## 全局验收
- [x] 本域 pytest 全绿（143+journey 含 heavy e2e，2026-11-26 实测）
- [x] 前端 typecheck 零新增（基线 diff 空）/vitest 定向 94/95（1 预存=master）/eslint 3 预存与 master 一致
- [x] 前端生产 build 通过（2026-11-26）
- [x] 迁移 upgrade/downgrade 循环 + head 唯一（m0111）
- [x] R1 冷审完成（0P0/1P1/4P2/8P3 全部修复 42fb6161a）→ R2 复审进行中
- [ ] PR 提交（base master），正文含基线/WP 表/复现/评审/避让矩阵
