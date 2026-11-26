# HANDOFF — 03 多源空间联动探索（交接记录）

分支：`zcode/goal-multi-source-linked-exploration-20261126`
worktree：`/home/kevin/projects/webgis/webgis-multi-source-linked-exploration-20261127`
基线：origin/master `59eb3f527`（#2282 后）；Pi gitlink `ef4a1cf`（v1.6.1，未改动）

## 提交序列

| commit | 内容 |
|---|---|
| 8d4a67025 | planning 账本（基线/去重/探查证据） |
| 642632d04 | WP1 契约 v3：sources/links/层级 + 传播键集语义（relations.py） |
| 5b141a16f | WP2 指标解释合同 + 确定性求值器（metrics.py，分母治理） |
| d10f74bb1 | WP3 服务编排 preview_plan/逐源锚/传播半连接/多源导出 |
| b1473b315 | WP4 drill + Pi 工具×2 + REST×2 + 前端类型/API/组件 + conv.id 缺陷修复 |
| 78e090ef4 | WP5 selection_ref 指代 + 性能/Pi journey 证据 |
| （本条后） | WP6 审查修复 + openapi 快照刷新说明 |

## 用户旅程闭环（两类现实多源任务）

1. **学校点 × 区县人口表（成都场景抽象）**：
   inspect 两源 → v3 计划（键关系 district↔district、many_to_one、
   left_to_right 传播）→ 选 public 类别 → 学校 4 所 + 人口 3 区（港区被传播
   排除）→ 每万人学校数指标（武侯 0.03 所/万人，零人口区 unavailable 不冒 0）→
   drill down 武侯区（3 所）→ 上卷 → 保存/重开（逐源锚）→ 应用到地图
   （per-chart sourceRef）→ 多源导出（逐源图层过滤 + 口径卡）。
   入口：REST /plan-preview+/drill 与 Pi 工具 webgis_exploration_plan_preview/
   webgis_exploration_drill（同一 service）。
2. **「只看刚才选中的那组」**：selection_ref=previous → selection_history 解析
   （两代演示：当前代替换后 previous 生效）→ primary 源 id 集合条件 → 传播到
   人口源。不可解析 → 最小澄清（SELECTION_REF_UNRESOLVED）。

## 已知限制（如实）

1. **跨源空间关系指标未实现**（attribute 键关系已闭环）：spatial_within 关系上
   的指标 typed 拒绝（SPATIAL_METRIC_UNSUPPORTED）并指引 G38 物化路径；
   spatial_within 本身可用于传播吗——不可（传播只支持 attribute 键集；
   空间关系可用于关系声明但传播/指标走物化）。见 contract.LinkSpec kind 词表
   与 metrics.SPATIAL_METRIC_UNSUPPORTED。
2. **行政区层级**：无县级表（master 既有现状）；钻取层级必须显式声明
   hierarchy.parent_field（不从名称猜）→ 「区县下钻街道」在无声明时诚实阻塞
   （HIERARCHY_NOT_DECLARED），这是任务书要求的诚实路径。
3. **Pi journey 全绿被 master 既有基线缺陷阻塞**（非本线，已证 diff 不涉及）：
   ① Runtime Manifest 11 项悬空 capability（#2274/#2275）；② SQLite 托管水位
   0103 落后迁移（messages.attachments 在 g36/0107+）。降级开关
   GIS_MANIFEST_STRICT=0 后本线最重的 preview/求值/句柄 6 gate 真实通过。
4. **map_state 账本读依赖 Redis**：本环境无 Redis → selection_ref 集成测试以
   monkeypatch 提供账本读取（解析/注入/传播链全真实代码）。
5. **HTTP 层遗留缺口**：G46 既有 7 路由的 HTTP 直接测试仍未补全（本线为新
   路由建立了测试网 + 修复 conv.session_id 潜伏缺陷；旧路由测试网列入后续）。
6. 前端 multi-source-scope 组件已交付（纯展示 + 测试），面板主框架的深度
   接线（面板内嵌多源区块/钻取面包屑交互）为下一层增量；当前 Agent 与 REST
   已可完成全闭环（UI 组件供挂载消费）。

## 测试规模

- 后端新增：单元 71（contract 46 + metrics 25）+ 工具/HTTP 8 + 集成旅程 14
  = 93；全域回归 170 passed（G46 既有 77 不退化）。
- 前端新增：组件 5；既有面板 4 回归；tsc/eslint 本域净。
- openapi 快照显式刷新（additive + master 既有漂移，见 TEST_EVIDENCE）。

## 回滚

- 全部为 additive 模块 + 既有文件局部扩展；回滚 = revert 分支。
- PLAN_SCHEMA_VERSION=3 读取兼容 v2（旧视图零迁移）；exploration_views 表
  无新迁移（多源清单存 plan JSON）。
- 唯一行为变更的既有路径：conv.session_id→conv.id（修复 AttributeError 潜伏
  缺陷，见 WP4 commit message）。
