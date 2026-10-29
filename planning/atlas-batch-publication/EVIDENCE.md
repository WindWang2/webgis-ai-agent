# EVIDENCE — 验证证据（全部本地真实执行，命令可复现）

## M0（2026-10-30）

- worktree 建立：`git worktree add -b zcode/cartography-atlas-publishing-vnext ../webgis-worktrees/gis-atlas-publish origin/master` → HEAD `4b3629c90`，status clean。
- 开放 PR：#1868/#1869（gh pr list --state open，无重叠）；最近 50 合并 PR 与 86 开放 issue 逐条过滤 → 矩阵见 PR_OVERLAP_MATRIX.md；`zcode/*` 远端分支无相近命名；planning 命名空间干净。

## M1 基线（改动前）

```
python -m pytest tests/cartography/test_publication_atlas_pipeline.py \
  tests/cartography/test_publication_ir_golden.py tests/cartography/test_vector_pdf_route_atlas.py -q
→ 36 passed（真实 WeasyPrint 渲染）
python -m pytest tests/jobs/ -q → 247 passed
```

## M2 接缝提取（同步路径字节不变）

```
python -m pytest tests/cartography/test_publication_atlas_pipeline.py \
  test_publication_ir_golden.py test_vector_pdf_route_atlas.py tests/jobs -q
→ 283 passed
```

## atlas_batch 新增测试面（commit 1/2 后）

```
python -m pytest tests/cartography/atlas_batch -q → 40 passed
  含旗舰：test_atlas_flagship_12_districts（13 页 PDF；pypdf 页数==13；
  封面 TOC 12 区名齐全；逐页含区名+「第 n / 13 页」+图册题；manifest 13 页
  对账：页序/过滤身份/feature_count==1/指纹唯一 12/legend_digest 全一致；
  shared 可比性 true；simulated_data true；owner sidecar 在场）
executor 面：缓存命中 0 重编译（compile 计数器证明）；失败页证据直写
  result_summary → 重试仅重编 1 页；取消 → 工作目录清理+无产物；
  busy → typed atlas_pdf_busy → 恢复后 cache_hits==2 零编译
API 面：POST dry-run plan==3；幂等双提交复用同一 job；typed 400
  （atlas_local_requires_frames/atlas_category_property_missing）；
  404（atlas_spec_missing/非属主 job/非属主 session）；eager 全链完成 →
  GET 详情页级状态 + 任务中心 kind=atlas_export 可见
工具面：registry.dispatch("export_atlas") 真链（eager）→ job 句柄；
  双发幂等 reuse；local+category → typed error dict
```

## 回归面

```
python -m pytest tests/cartography -q
  --deselect test_mutation_parity_corpus[set_scene_invalid_mode]
→ 2962 passed, 1 skipped, 3 xfailed
python -m pytest tests/jobs tests/test_endpoint_scope_matrix.py \
  tests/test_tool_meta_contract.py tests/test_prompt_tool_name_integrity_438.py \
  tests/test_slim_tool_result_upgrade.py -q → 全绿
python -m pytest tests/quality/test_api_compatibility.py tests/unit/api_contract -q
→ 117 passed（唯一红 test_response_model_coverage 为 master 既有）
```

### master 既有红（干净对照验证，/tmp/atlas-master-check @ 4b3629c90）

- `test_response_model_coverage::test_all_json_endpoints_have_response_model`（data-fabric pmtiles + storymap spec 两端点缺 response_model）——master 同红，非本分支引入。
- `test_mutation_parity_corpus[set_scene_invalid_mode]`——master 同红，非本分支引入。

## 前端

```
cd frontend && pnpm vitest run
→ Test Files 493 passed (493)；Tests 4667 passed | 1 expected fail（共 3 次复跑；
  首轮 1 例 flaky 失败，后 3 轮全绿）
pnpm typecheck → 通过（tsc --noEmit × 2 配置）
pnpm eslint <改动面文件> → 0 警告
新增测试：tasks-tab 图册页级状态/下载（3 用例）+ atlas-jobs 客户端（3 用例）
```

## 快照/矩阵再生（项目机制，非手工）

```
python scripts/generate_endpoint_scope_matrix.py（补 atlas 域规则后）
→ POST /api/v1/atlas/jobs gis:write；GET /api/v1/atlas/jobs/{job_id} gis:read
API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py → 快照刷新
API_CONTRACT_FIELDS_UPDATE=1 pytest tests/unit/api_contract/test_field_contract.py → 刷新
复跑：快照字节闸全绿（test_snapshot_matches_and_no_breaking_changes 过）
```

## 未跑项（如实披露）

- **mypy**：本机 venv 未安装（CI 侧 dev 依赖）；mypy 白名单范围 app/core 本次零改动，新模块在白名单外。ruff 全仓 `app` + 新测试目录 → All checks passed。
- **Playwright 真浏览器 smoke**：本任务组件级（vitest）+ API e2e（eager Celery 真链）覆盖主语义；真浏览器旅程未跑（后续可补 j1 旅程旁挂用例）。
- **远端 CI**：任务纪律明确不等待；本地等价面已全绿。
- **真实 Celery worker（Redis broker + 独立进程）**：以 eager 内联 + worker simulation 覆盖（与 tests/jobs/test_job_celery_e2e.py 同诚实口径）；跨进程重投/心跳路径依赖既有 jobs 框架测试。

## R1/R2 独立评审与终跑（2026-10-30）

- R1：2×P1 + 4×P2 + 5×P3 → 全部清偿（commit 44ecd8a9e）；atlas_batch 46 绿。
- R2（终审）：R1 清偿逐条复核成立；新发现 1×P2 + 5×P3 → 全部清偿（commit 2571f3699）。
- 终跑：`pytest tests/cartography -q --deselect <master 既有红>` → **2969 passed**；
  出版族+jobs+API 契约+端点矩阵 351 绿；ruff 全仓绿。
- 前端自 4f93ae707 后零 diff —— 全量 vitest 493 文件/4667 用例绿结论有效。
- master 复查：origin/master 仍为 4b3629c90（无 rebase 需要；PR 前二次去重确认）。
