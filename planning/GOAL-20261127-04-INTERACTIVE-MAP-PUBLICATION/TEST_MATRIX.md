# TEST_MATRIX — 04 线·交互式只读专题地图发布

## 后端 pytest（全部真实断言；`pytest tests/unit/publication/ tests/cartography/test_publishing.py` = 38 passed）

| 文件 | 覆盖 |
|---|---|
| test_publication_contract.py (9) | 快照构造/指纹确定性/编辑变更高/白名单查询/typed 阻断往返/红线扫描（键名归一、绝对路径）/构造拒绝 |
| test_publication_lifecycle.py (11) | draft 世界/publish→reader 全旅程/读者负例 404（撤销/错码）/跨 owner 403×3+状态不变/confirm 门 400/license 阻断 422/executor 防 not_draft 重放/版本不可变+显式 v2/链接过期 404/字段边界 400×4（fields/where/order/summary）/unsupported source typed 阻断+读者剔除 |
| test_publication_accessibility.py (4) | manifest 随快照（alt 作者优先/合成回退/投影/来源）、色觉诚实性、读者 doc 等价信息、alt 建议入口 |
| test_publication_tools.py (4) | 注册、无 publish/link 红线、draft side_effect 词表、session fail-closed |
| test_publication_pi_journey.py (4) | 真实 registry.dispatch：inspect/prepare（license 阻断解释）/draft+validate（can_publish+同意边界）/跨会话可信注入 fail-closed/无 publish 工具 |
| tests/cartography/test_publishing.py (6, 基线) | 既有 accessibility/出版测试无回归 |

## 迁移

- `alembic upgrade head`（sqlite 全链）→ g5202 建表/索引/CHECK ✓；`downgrade -1` → 表删除 ✓
- `alembic heads` = g5202_map_publications（单 head，锚 aa356ce2fcdc）✓

## 前端 vitest（`pnpm exec vitest run lib/publication/ lib/api/publication-authoring.test.ts` = 18 passed）

- reader-filter (6)：where AST 映射（空/单/and）、MapLibre 表达式（单/all/contains）、白名单防御、aria 描述
- reader-api (4)：code 形态防御（注入/过短/空格）、doc 端点 skipAuth、features 查询串编码
- publication-authoring (4)：ownerToken、confirm 布尔透传、ttl、draft 路径
- typecheck（app tsconfig 0 新增错误；基线既有 live-spec.test.ts 错同 master）
- eslint --max-warnings 0 全部新文件 ✓

## 真实服务旅程（planning/.../journeys/）

- journey_backend.py：真实 uvicorn（USE_REDIS=false → Celery eager）+ 真实 sqlite（alembic 建库）
  + 生产权威链注入（apply_mutation + patch_component title）→ HTTP 旅程 1/2 全绿（evidence JSON）
- journey_browser.cjs / 交互式 Playwright：生产 next build + next start（3101）+ 后端（8001）；
  桌面/窄屏/键盘/无 WebGL 四变体全绿；截图 desktop-reader/desktop-table/desktop-stats/
  desktop-about/desktop-filtered-table/narrow-reader/keyboard-flow/nowebgl-reader.png

## 性能（同环境同 fixture）

见 PROGRESS.md 性能节（p50/p95 具体数字；读者端点 ~7ms@journey fixture）。

## 未测/未验证（诚实声明）

- Pi RPC 全链（bridge secret + turn token 签名）——无凭证环境未验证；同链已验证。
- 屏幕阅读器实测（NVDA/VoiceOver）未做；键盘与 aria 结构已验证，语义播报为 aria-live 实现。
- 真实 Postgres 上的迁移（sqlite 实测；列型为常规 String/Integer/JSON，风险低）。
- 大数据量（5 万要素上限）压测未做；预算门有 typed 阻断（24MiB/50k），已由单测覆盖边界逻辑。
