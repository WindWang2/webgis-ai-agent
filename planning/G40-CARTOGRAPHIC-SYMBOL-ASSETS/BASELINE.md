# G40 BASELINE — 执行时点事实账本

任务：G40-CARTOGRAPHIC-SYMBOL-ASSETS（可复用点符号与面纹理资产工作台）

## 执行时点与基线

- 执行开始：2026-11-15（研究快照 2026-11-14）
- 目标仓库：WindWang2/webgis-ai-agent（`gh repo view` 确认，remote=origin）
- 默认分支：master；最新 base SHA = `1276692737a8cf58d0604fb40ae0e70fc61779a2`
  （== 研究快照 SHA；fetch 后与快照一致，无新提交）
- 主工作区 HEAD=816705218（旧 checkout，未动）；本任务 worktree：
  `/home/kevin/projects/webgis/webgis-worktrees/g40-cartographic-symbol-assets-20261114T175308Z`
  分支 `zcode/g40-cartographic-symbol-assets-20261114T175308Z`（基于 1276692737）

## PR / review 通道复核（B2）

- Open PR（2）：#2118 G36 Pi 视觉上下文、#2117 G37 会话模型运行配置。
  两者 review/comments 通道均为空。与本任务文件重叠面：`app/tools/__init__.py`、
  `app/agent_pi_bridge.py`、`app/services/chat/pi_rpc_client.py`、
  `app/api/routes/chat.py`、`frontend/components/sidebar/chat-tab.tsx`、
  `frontend/lib/api/chat.ts` —— 合流时需处理这些共享接缝的兼容。
- 已合并 #2094–#2116 共 23 PR 的 GitHub reviews/inline/conversation 通道抽检
  （2095/2096/2099/2100/2116/2117/2118）全为 0，与快照一致；独立评审记录在
  `planning/G29..G35/*/REVIEW.md`（已读 G33、G34）。
- Open issues：0。
- G09 参数化批量分析、G21 有向 OD 流图：曾分配未检索到 PR，不重新占用
  （与本任务无重叠）。

## 关键既有事实（已亲核）

1. **G33 边界**（planning/G33-.../PLAN.md L48-68）：明确排除「新符号资产/图案/
   SVG sprite」→ 本任务（G40）拥有该域。G33 服务模式（inspect/preview/commit +
   apply_gis_mutation(UpsertLayerIntent, expected_revision) 单笔 CAS）是本任务
   写入权威的复用模板。
2. **G33 REVIEW**：P1-2「换通道留幽灵 paint 键」教训 → 资产绑定切换时先清本域
   旧键；P1-3「bool/boolean 词表分歧」教训 → typed 类别映射词表单点。
3. **G34 REVIEW 已知开放限制**（不纳入本任务修复，但不得踩）：
   - 预览/导出用全局 mapspec.view.bounds，不消费 frame 级 bounds；
   - 图例 continue 续排盒几何错误、无跨页；
   - 比例符号未计入 needed_pt、画框外无标签；
   - 页级模板仍是全局组件替换；submit_export 不跑 validate_authoring；
   - UI align 本地计算。本任务新增 swatch 只走 render_legend_box_authored 消费面，
   不宣称修复这些布局组合（S28 场景对应：受限组合明确 unsupported）。
4. **visual_variables.py**（app/lib/cartography/visual_variables.py:60-82）：
   CHANNEL_RUNTIME_STATUS：shape=partial、texture=unsupported、orientation=partial；
   allocatable() 只认 supported。本任务真实运行验证后仅可对**本域资产绑定通道**
   提升为有证据的 status（带注释说明口径），不整体改 supported。
5. **mapspec_schema.py MapSpecLayer**（L721-776）：paint/layout 开放 dict +
   additive typed 块先例（label/bivariate/data_binding/representation/
   scale_profile/raster_style）。资产绑定采用同款 additive typed 块。
6. **composition_assets.py**（app/services/templates/composition_assets.py）：
   既有版本化资产生命周期先例（draft→new_version→publish→retire→restore），
   scope 纪律（private/org/project；不可见=404；DB 资产永不进全局注册表；
   钉 revision 重放只读放行）。模型对：
   `app/models/composition_template_asset.py`（CompositionTemplateAsset 主表 +
   Version 不可变快照表，migration 0098 additive 惯例：String(36) 版本行 PK、
   CHECK 词表与迁移一字不差、create_all-coexistence guard）。本任务照此模式建
   `cartographic_assets` 领域表，不建第二套生命周期服务语义。
7. **durable_blob_store.py**：唯一内容寻址二进制后端（sha256 键、put-if-absent、
   原子发布、digest 校验读、MAX_BLOB_READ_BYTES=64MB）。资产 SVG/Raster 内容
   走 put_blob/get_blob；GC 保护 = DB 引用行（artifact_lifecycle 的 promotion GC
   只删零修订引用+无 pin+超宽限期的 blob）→ 资产版本行持有 blob 键即受保护；
   显式删除须先查引用（S18）。
8. **ADR-0290**（G34）：units.py 是 px/mm/pt/DPI 唯一换算 corpus
   （PX_PER_MM=4、user unit = mm*4*dpi/72、采样 px = mm*dpi/25.4）——本任务
   出版尺寸换算复用它，不自造单位常量。
9. **ADR-0289**（G16）：障碍策略 geometry_v1 显式声明才生效；circle 障碍有
   身份（layer+feature）；求解器双孪生 label_collision.py ↔ label-solver.ts。
   新图元障碍走 adapter 计算变换后边界，复用既有求解器。
10. **CONTEXT.md 契约**：MapSpec=backend 权威；TS compiler 是 MapSpec→MapLibre
    的唯一编译权威；mutation 走 apply_mutation/CAS/expected_revision；
    「Assembled vs Committed」语义必须显式；Pi native surface 固定集 +
    webgis_execute 长尾代理；不建第二套 agent loop/registry/MapSpec store。

## 资源纪律

- pytest 单进程；vitest 1-2 workers；playwright 1 worker。
- 重阶段锁：`$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`。
- 本任务 DATA_DIR/端口/临时目录独立（后续记录实际值）。

## 预算记录

- 宿主未暴露 /goal usage budget 配置面；无法设置 320M token 数字。按长周期
  多轮执行，如实记录实际消耗，不伪造额度。
