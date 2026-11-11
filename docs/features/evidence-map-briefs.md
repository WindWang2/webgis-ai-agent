# 证据化地图简报（Evidence Map Briefs）

> G11 / ADR-0288 —— 把一次真实 GIS 分析整理成**可编辑、可追溯、固定版本、
> 可私有交付**的地图简报。每个数字、图表与结论都知道来自哪里；人工编辑
> 永不丢失；发布前有预检；交付物可离线阅读并可对账。

## 用户旅程

1. **生成草稿** —— 对会话说"把刚才的学校分布分析整理成一份地图简报"
   （或 Agent 调用 `brief_create_draft`，或 Story 页打开"证据化地图简报"
   面板点"生成简报草稿"）：后端从会话**真实产物**（统计表/图表/图层台账）
   编译出章节 × 内容块结构：说明文本、指标卡、图表、地图视图、方法说明、
   局限建议、来源引用。
2. **编辑与锁定** —— 每个内容块可编辑正文（保存即标记 manual）、锁定
   （重生成永不覆盖）、删除、上下移动；章节可增删重排。编辑基于乐观
   revision，他人并发保存会得到显式 409 冲突提示，绝不无声吞掉你的修改。
3. **证据核对** —— 点指标卡打开证据侧栏：值、来源产物（可读名称而非
   hash）、字段路径（如 `table.rows.0.count`）、方法口径、时间窗、
   权威值比对结果、比例类指标的分子/分母/基期。地图视图块显示 pin 的
   镜头/图层/MapSpec 版本指纹 —— 当前会话换城市不影响旧简报。
4. **预检 → 冻结发布** —— 预检逐项给出 pass / fail / not_evaluated
   （来源授权、引用可解析、数值单位一致性、地图版本、隐私扫描、署名等），
   总判定 deliverable / draft_exportable / blocked。冻结生成不可变发布
   版本（HTML + 证据 manifest + facts.csv，内容寻址幂等）；继续编辑草稿
   不影响已发布版本。草稿可显式带"未验证"披露导出，manifest 标 draft。
5. **私有交付与分享** —— 下载单文件离线 HTML（零外链；指标卡带核验
   徽标与来源；静态图件净化内联；离线不含交互地图时诚实告警）+ 机器
   可读 JSON manifest + 公式注入防护的 CSV。默认 private；显式分享生成
   只读链接（可撤销、可过期）；撤销后服务端即刻拒绝，已下载副本无法
   收回（UI 明示）。
6. **数据更新** —— 产物更新后简报显示"待更新"徽标（只标记不改内容）；
   "只刷新自动指标"/"只重写未锁定说明"两种重生成保留全部人工内容。

## 数值的可信度模型

| 状态 | 含义 | 来源 |
|---|---|---|
| 已核验 verified | 值与产物权威字段一致（±1e-9）且单位/新鲜度无缺口 | 确定性核验管线 |
| 有提示 warning | 有正向证明但有披露级缺口（单位元数据缺失/来源 stale/小幅漂移） | 同上 |
| 未验证 unverified | 无正向证据（绑定缺失/载荷不可达/字段无法定位）—— 失败封闭，绝不猜值 | 同上 |

- 比例/百分比/增长率**必须有分母/基期事实**且复算命中，否则永不可
  verified（模型或人文字里的数字不因此升级）。
- 与权威值/复算值**矛盾**的事实标记 contradicted，预检直接阻断发布。

## Agent 工具

`brief_create_draft` · `brief_inspect_evidence` · `brief_update_block` ·
`brief_preflight` · `brief_freeze_release` · `brief_export`
（session_id 由运行时注入，模型不可伪造；导出只返回 owner 下载地址，
对外分享必须用户显式操作）。

## REST API（/api/v1/briefs）

会话域（所有权守卫）：`POST /sessions/{sid}/draft`、`GET/PATCH
/sessions/{sid}`、块与章节 CRUD、`POST /sessions/{sid}/regenerate`、
`GET /sessions/{sid}/evidence/{fact_id}`、`POST /sessions/{sid}/preflight`、
`POST /sessions/{sid}/releases`（幂等）、`GET .../releases[/{rid}/manifest|
download|facts.csv]`、`POST|DELETE .../releases/{rid}/share`。
公共面（限流）：`GET /shared/{code}`、`GET /shared/{code}/view`。

## 存储与不可变性

- `map_briefs`（会话唯一草稿行，revision 单调；spec ≤512KB）
- `map_brief_releases`（不可变发布：content_sha256 幂等键、CAS html 载荷、
  manifest、分享列；草稿删除后 release 保留）
- 迁移 `0104_map_briefs`（head 0103 → 0104，领号登记 .alloc.json）

## 安全要点

XSS/`</script>` 早闭合（JSON \u003c 转义 + esc 白名单渲染）、SVG 净化
（无脚本/事件/外链）、CSV 公式注入防护、敏感键 REDACT + URL 凭证 scrub、
release_meta 白名单投影（分享码/owner secret 不进交付物）、块/事实 ID
与 artifact ref 词表校验（防路径穿越/引用注入）、深度门（>64 层 422）、
交付物离线零外链、跨 owner 统一 404 不泄露存在性。

## 已知边界（诚实披露）

- 离线 HTML 不含交互式地图（无许可底图/瓦片不复制）—— 静态图件或
  明确告警占位；交互分析在工作台完成。
- 印刷 PDF 不在本线交付（由既有出版链负责）。
- 已下载的离线副本无法远程收回。
- 模型写作质量未实测（本线默认 deterministic 生成；LLM 只组织解释文字
  的接入位已定义并标注 stub）。

## 复现示例

```bash
# 后端（隔离 sqlite）
DATABASE_URL="sqlite:////tmp/g.db" USE_REDIS=false JWT_SECRET_KEY=<32+> \
  python -m uvicorn app.main:app --port 8001
# 浏览器旅程（真后端）
cd frontend && E2E_MODE=real E2E_USER=<u> E2E_PASS=<p> \
  pnpm exec playwright test e2e/journeys/j13-brief-delivery.journey.ts --grep real
# 单元/集成
.venv/bin/python -m pytest tests/unit/test_mapbrief_core.py \
  tests/unit/test_mapbrief_api.py tests/unit/test_mapbrief_security.py \
  tests/unit/test_brief_tools.py
```
