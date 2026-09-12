# Journey E2E (quality-e2e-v9, ADR-0146)

用户旅程端到端测试：`frontend/e2e/`。与 runtime validator（渲染管线门）
互不替代——这里驱动真实应用壳，保护用户路径编排。

## 运行

```bash
cd frontend
pnpm exec playwright test --grep @smoke   # PR 冒烟档（mock，无后端）
pnpm exec playwright test                 # 全部六条旅程（mock）
E2E_MODE=real pnpm exec playwright test   # real 档（需真实后端，nightly）
```

mock 模式零后端：`e2e/fixtures/api-stubs.ts` 以 `page.route` 应答全部产品
API（继承 `test/visual/capture.mjs` 的模式，做成有状态 + 旅程可编排），
SSE 对话流按生产契约回放（`fixtures/sse.ts`）。

real 模式契约（ADR-0146）：真实后端（uvicorn 最小配置）+ 真实前端 +
**确定性 LLM 边界**（`tools/llm-stub.mjs`，OpenAI wire 格式脚本化应答；
`STUB_FAIL_AT`/`STUB_DELAY_FIRST_TOKEN_MS` 供取消/重试旅程编排）。
真实 LLM 冒烟不在此档（见 P6，env 门控）。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `E2E_MODE` | `mock` | `real` = 走真实后端 |
| `E2E_PORT` / `E2E_BASE_URL` | `3310` | 前端地址（mock 档 webServer 自动起 `next dev`） |
| `E2E_API_URL` | `http://localhost:8001` | real 档后端地址 |
| `E2E_USER` / `E2E_PASS` | — | real 档登录（nightly 由 `manage.py create-admin` 预置；缺失 = 显式失败） |
| `REQUIRE_BROWSER` | — | nightly 档置 1：缺浏览器 = 硬红（非 skip） |

## 旅程注册表（D–J 线扩展点）

`helpers/registry.ts` 是唯一索引。并行线扩展：新增
`journeys/jN-<name>.journey.ts`（命名 `*.journey.ts` 与 vitest 互斥）→
注册表登记 → 需进 PR 冒烟则标 `@smoke`。CI 档面由
`tools/changed-lanes.mjs` 前缀映射决定（映射正确性由
`frontend/tests/e2e-lanes/changed-lanes.test.ts` 锁定，#1216 类失效即红）。

## 已知缺口（旅程勘察发现）

- 上传 UI（`components/upload/upload-zone.tsx`）存在但未挂载到任何界面，
  J1 的「上传」腿在传输契约层钉住（mock POST /upload），UI 挂载后应改走界面。
- 「结果」工作台 rail tab 已不在当前壳上（capture.mjs 为旧版界面），
  结果到达信号改为地图上的分析结果卡。
