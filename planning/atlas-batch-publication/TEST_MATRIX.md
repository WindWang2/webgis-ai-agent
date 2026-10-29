# TEST_MATRIX

## 后端（pytest）

| 域 | 文件 | 关键断言 |
|---|---|---|
| 契约 | `tests/cartography/atlas_batch/test_atlas_job_spec.py` | spec 校验：local+非frames→typed 拒绝；session 缺失；policy 边界复用；版本字面量 |
| 规划 | `tests/cartography/atlas_batch/test_planner.py` | 12 区 category 驱动确定性（两次 plan 指纹相等）；页 feature_count 与数据对账；legend_digest 存在（内容页）；封面 binding（feature_count=0）；数据变更（content_revision/要素数变）→ 相关页指纹变、无关页不变 |
| 接缝 | 既有 `test_publication_atlas_pipeline.py` / `test_publication_ir_golden.py` / `test_vector_pdf_route_atlas.py` 原样全绿 | 同步路径字节不变 |
| 页编译 | `tests/cartography/atlas_batch/test_page_compiler.py` | 页头注入后 SVG 仍合法（lxml/根属性保持）；页码文本含 "n/N"；同步路由输出不含注入（对照） |
| QA | `tests/cartography/atlas_batch/test_qa.py` | 缺页/重复/乱序检测；shared 分类不一致（人为改一页 legend）→ fail 证据；页数不等→fail；local scope→comparability 披露 |
| manifest | `tests/cartography/atlas_batch/test_manifest.py` | 形状/schema 版本；bounded；per-page product_manifest 摘要在场；simulated 标注传播 |
| executor | `tests/cartography/atlas_batch/test_executor.py` | 工作目录生命周期；缓存命中跳过编译（计数器证明不重渲染）；失败页→job failed→retry 只渲染 failed；取消→目录清理+无终态副作用；忙碌→typed failed→重试不重渲染已过页；幂等提交复用 job 行 |
| API | `tests/cartography/atlas_batch/test_atlas_jobs_api.py` | POST 400 typed 族；GET 属主 200/他人 404；artifacts URL 指向 download 路由；与 /tasks/jobs 联动（cancel/retry） |
| 工具 | `tests/cartography/atlas_batch/test_atlas_tool_dispatch.py` | 真实 dispatch_tool("export_atlas") → durable job 行 + 幂等复用 + 轮询可达 |
| **旗舰验收** | `tests/cartography/atlas_batch/test_atlas_flagship_12_districts.py` | 12 合成行政区（simulated 标注）→ 13 页 PDF：pypdf 页数==13；逐页文本含区名且与 TOC/manifest 页序一致；manifest pages 与 planner bindings 全对账；下载属主负例（他人 404） |
| 回归 | jobs 族（tests/test_job_*.py 8 文件）+ cartography 出版族 + artifact/lineage 相关 | 全绿 |

## 前端（vitest / tsc / eslint）

| 域 | 断言 |
|---|---|
| `atlas-jobs.ts` 客户端 | 请求/响应形状；错误映射 |
| `jobs.ts` kind 词表 | atlas_export additive；旧 kind 渲染不变 |
| TasksTab | atlas 卡片页级状态渲染；下载链接；取消/重试按钮复用 |
| map-studio 提交区 | 表单→submitAtlasJob 派发；校验（local 需 frames 提示） |
| 全量 vitest | 既有 4660+ 用例不红 |

## 本地验证命令（PR evidence 用）

```
python -m pytest tests/cartography/atlas_batch -x -q
python -m pytest tests/cartography/test_publication_atlas_pipeline.py tests/cartography/test_publication_ir_golden.py tests/cartography/test_vector_pdf_route_atlas.py tests/test_job_lifecycle.py -q
python -m ruff check app tests
python -m mypy app/services/atlas_batch app/lib/cartography/atlas_job_spec.py（按仓库 mypy 范围）
cd frontend && pnpm vitest run（相关域 + 全量）&& pnpm tsc --noEmit && pnpm eslint（改动面）
```

## 明确不跑/条件跑（如实披露）

- Playwright 真浏览器 smoke：若本机依赖可用则跑提交→任务中心→下载旅程；不可用则披露（vitest 组件级覆盖主逻辑）。
- 远端 CI：不等（任务纪律）。
