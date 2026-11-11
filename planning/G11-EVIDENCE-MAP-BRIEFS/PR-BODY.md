# feat(brief): G11 证据化地图简报 —— 块级证据绑定 · 可编辑草稿 · 预检 · 不可变发布 · 私有离线交付

## 新增用户功能（BRF-O01..O12 全覆盖）

1. **证据化草稿**：对会话说"整理成地图简报"（或 Agent 工具 `brief_create_draft`、
   或 Story 页新"证据化地图简报"面板）→ 从会话**真实产物**（统计表/图表/
   图层台账）生成章节 × 内容块：说明文本/指标卡/图表/地图视图/方法/局限/
   来源引用。每个自动数值绑定 artifact ref + 字段路径 + 用户可读来源口径
   （数据名/方法/时间窗），内部保留 sha256 对账指纹。
2. **可信度模型**：指标卡带 已核验/有提示/未验证 徽标；比例/增长必须有
   分母/基期且复算命中才可核验；矛盾事实直接阻断发布 —— deterministic
   positive-proof 管线，LLM 文字中的数字永不自动升级。
3. **真实编辑器**：块级正文编辑（保存即标 manual）/锁定/删除/重排，章节
   增删；乐观 revision（并发 409 显式冲突，不吞人工内容）；重生成永不
   覆盖 manual/locked；产物更新只标"待更新"不改内容；指标点开证据侧栏
   （值/来源/字段/权威值/分子分母基期），地图视图块 pin 镜头+图层+
   MapSpec 指纹（换城市不影响旧简报）。
4. **预检与不可变发布**：8 检查项 × pass/fail/not_evaluated →
   deliverable/draft_exportable/blocked；冻结生成不可变 release（CAS 内容
   寻址幂等 + manifest 对账 + 版本 pin），继续编辑不影响旧版；草稿可带
   未验证披露显式导出（标 draft）。
5. **私有交付**：单文件离线 HTML（零外链、SVG 净化内联、缺图诚实告警）+
   JSON manifest + 公式注入防护 CSV；默认 private；显式分享（可撤销/过期，
   token 仅限该 release 只读）；跨 owner 统一 404。
6. **Agent 工具面**：六工具（draft/inspect_evidence/update_block/preflight/
   freeze_release/export），session_id 注入探针，回执紧凑。

## 复用基础（不建第二事实库）

StoryMapSpec v1.1（继承扩展，round-trip 兼容）· lib story_compiler（唯一
叙事编译器）· export_packager 脱敏/转义纪律 · ArtifactRegistry/CAS
BlobStore · evidence_claim positive-proof 语义 · report.py 分享先例 ·
mapspec_to_svg 静态渲染 · ToolRegistry/Pi 桥 · 前端 transport/StoryView/
设计系统。共享接缝仅 4 处一行级 additive（main.py/_TOOL_MODULES/i18n/
能力平台）。

## 真实证据（本机实跑，详见 planning/G11-EVIDENCE-MAP-BRIEFS/）

- 后端：mapbrief core/api/security/tools = 32+14+13+5 passed；storymap
  基线 106 + 能力面 82 回归 passed；alembic upgrade/downgrade head 实跑。
- Pi 链：agent_pi_bridge.dispatch_tool（生产回调入口）→ 真实
  ToolDispatchService → 真实领域计算 passed。
- 浏览器：j13 双模式 e2e —— mock 1 passed；**real 1 passed**（真实登录/
  LLM stub 真实空间分析产物/编辑 revision/预检冻结 v1/CAS 下载 HTML 零外链
  断言/分享→外部者 401-404→撤销）；j4 mock+real 回归 2 passed。
- typecheck 双 tsconfig 绿；vitest i18n+story 64 passed。

## 破坏性变更 / 迁移

无破坏性变更。新增迁移 `0104_map_briefs`（head 0103→0104，.alloc.json
领号）；ADR-0288（watermark 288）。

## 未验证项

真实 LLM 写作质量（deterministic 生成，LLM 接入位已定义）；生产
PostGIS/Redis 规模表现；nightly CI real lane（本地 real 全绿为交付依据）。

## 已知预存红（master 债，非本分支引入）

catalogue-contract（webgis_requirement_compile 幽灵命令）、data-plane
perf 上界、alembic percent-encoded URL 环境例 —— 见 TEST_MATRIX 附录。

## 回滚方式

6 个可回滚切片 commit；迁移 downgrade 实测可用；共享接缝全部 additive。
