# INTERFACE_CONTRACTS — 冻结接口（v1）

## 1. AtlasJobSpecV1（app/lib/cartography/atlas_job_spec.py）

```python
ATLAS_JOB_SPEC_VERSION = "atlas_job.v1"

class ClassificationScope(str, Enum):
    shared = "shared"    # 全册同一 legend/classification（QA 逐页验证一致）
    local  = "local"     # 逐页自定义（仅 frames 驱动 + 显式 per-frame layerOverrides）

class AtlasJobSpec(BaseModel):  # frozen, extra=forbid
    spec_version: Literal["atlas_job.v1"]
    session_id: str                  # 权威 spec 与数据归属（verify_session_owner）
    atlas: AtlasPolicy               # 既有模型复用（publication_ir）
    classification_scope: ClassificationScope = shared
    target_dpi: int = 300            # clamp 72..600（复用 clamp_target_dpi）
    title: str = ""                  # PDF 元数据标题（≤160）
```

校验（构造期 typed 拒绝，code 便于 400）：
- `atlas_local_requires_frames`：classification_scope=local 且 driver≠frames → 拒绝（附指引）。
- `atlas_session_missing`：session_id 空。
- AtlasPolicy 自身校验复用（pydantic 词表/边界）。

## 2. 页绑定与指纹（planner.py）

```python
class AtlasPageBinding(BaseModel):
    page_id: str; title: str; page_number: int; page_count: int
    cover: bool
    bounds: list[float] | None
    filter_layer_id: str; filter_property: str; filter_value: str
    feature_offset: int | None; feature_limit: int | None
    feature_count: int          # 本页实际绑定要素数（封面=0）
    legend_digest: str | None   # 本页目标层 legend_spec canonical digest（封面=None）
    fingerprint: str            # "atlaspage-sha256:" + 40hex

def plan_atlas_binding(spec: AtlasJobSpec, document: dict) -> AtlasPlanBinding
# = PublicationIR（plan_publication_pages 复用） + 每页 feature_count/legend_digest/fingerprint
```

指纹输入（canonical JSON sha256，截 40hex）：`{page_id,title,page_number,page_count,paper{w,h,profile},bounds,filter 身份三元组,feature_offset/limit,feature_count,data_digest,legend_digest,classification_scope,atlas_title,target_dpi,ir_fingerprint,structural_fingerprint,compiler_key}`；
- `data_digest` = 页有效文档**全部内联源要素** canonical digest（目标层页粒度 + 其余源全敏感 —— R1-P1 清偿）；
- `structural_fingerprint` = 页有效文档结构指纹（`publication_export._structural_fingerprint` 同款：layers/paint/thresholds/view/scene/scenario_mode + 源描述符）；
- `compiler_key = f"{PUBLICATION_IR_VERSION}|atlas-svg-v1|pagehdr-v1"`（页头注入代次也入键）。

## 3. 页级状态与结果（executor → AnalysisTask）

```python
# result_summary（终态；页体 svg 只留内存不落库 —— R2-P3 清偿）
{
  "spec_version": "atlas_job.v1",
  "plan_fingerprint": "<ir_fingerprint>",
  "pages": [
    {"page_id","title","page_number","fingerprint","status":"rendered|failed",
     "feature_count","error": str|None, "svg_bytes": int,
     "diagnostics": [...≤16], "degradations": [...≤8 warning+]}
  ],
  "page_count_expected": int, "page_count_rendered": int,
  "qa": {...门结果...}, "cache_hits": int,
  "artifacts": {"pdf": filename|None, "manifest": filename|None},
  "lineage_recorded": bool
}
```

JobView additive：`page_status: list[PageStatusView] | None`（≤21 条；page_id/title/status/error 单行；仅 atlas_export kind 填充）。

## 4. REST（app/api/routes/atlas_jobs.py）

- `POST /api/v1/atlas/jobs`（auth user 或 owner_token + session 归属证明）
  body: `AtlasJobSubmitRequest{session_id, atlas: AtlasRequestPolicy(既有 schema 复用), classification_scope, target_dpi?, title?}`
  语义：committed spec 存在性预检（mapspec_store.get_mapspec + 基本可规划性 dry-run——plan 在提交线程跑一次（有界 ≤50ms 级，纯规划无渲染），失败 → 400 typed）→ `submit_durable_job(celery_task=run_atlas_export, kind=atlas_export, idempotent=True)`。
  resp: `{success, job_id, status, idempotent_reuse, poll_url}`。
  错误码：`atlas_owner_required`(401，匿名拒绝 —— 产物下载走属主通道)、`atlas_spec_missing`(404)、`atlas_policy_invalid`/`atlas_local_requires_frames`/`mapspec_ref_sources_unhydrated`/`atlas_source_unavailable`(400)。提交面与执行面同口径先 async 水合 `ref:` 源（R2-A）。
- `GET /api/v1/atlas/jobs/{job_id}`：JobView + `pages`（同 status.json 页数组）+ `artifacts`（下载 URL：`/api/v1/export/download/{filename}`）。非属主 → 404（不泄露存在性；owner 三元组判定与 jobs.py 同款）。
- 取消/重试复用 `POST /tasks/jobs/{id}/cancel|retry`（既有）。

## 5. Agent 工具（app/tools/atlas_export_tools.py）

`export_atlas(session_id, driver="category", layer_id="", category_property="", features_per_page=50, page_budget=20, include_cover=True, atlas_title="", classification_scope="shared", target_dpi=300)`：
- session_id 一律经 `_resolve_session_id` 运行时归属校验（显式提供不直通，R1-P1）；无 origin owner（匿名 agent 流）→ typed `atlas_owner_required`（R1-P2）；origin contextvar 自动携带 owner/agent 链；`submit_durable_job`；返回 `{status, job_id, task_id, idempotent_reuse, message, poll_hint}`。
- 注册：`app/tools/__init__.py` 导出 + 既有 registry/dispatch 面（与 nature_resources NDVI 同模式）；真实 `dispatch_tool` 链测试。

## 6. 图册 manifest（manifest.py）

文件：`atlas_{job_id}_{ts}.manifest.json`（与 PDF 同 owner 落盘）。

```json
{
  "schema": "atlas_publication_manifest.v1",
  "generated_at": iso, "job_id": int, "attempt": int,
  "atlas": {"driver","layer_id","category_property","page_budget","include_cover","atlas_title","classification_scope","cross_page_comparability": bool},
  "inputs": {"session_id", "mapspec_revision", "spec_fingerprint(结构)", "ir_fingerprint", "target_dpi", "compiler_key"},
  "pages": [{
     "page_id","title","page_number","cover","bounds",
     "data": {"filter_layer_id","filter_property","filter_value","feature_offset","feature_limit","feature_count"},
     "classification": {"scope","legend_digest","class_count","method","palette_id","field"},
     "page_fingerprint", "product_manifest_digest",   # 每页单图语义证据（#1755 v1）摘要锚点
     "degradations": [...warning+ 诊断≤8], "render_diagnostics": [...≤16]
  }],
  "qa": {"page_count_expected","page_count_actual","duplicate_page_ids":[],"missing":[],"ordering_ok":bool,
          "classification_consistent":bool|null,"comparability_disclosed":bool},
  "disclosures": {"simulated_data": bool, "notes":[...]}
}
```

红线：bounded（页 ≤21；digest ≤40hex）；无坐标载荷/凭据；`simulated_data` 由提交方/测试显式标注（生产默认 false，合成 fixture 测试置 true）。

## 7. publication_export 接缝（本 PR 内提取，同步输出字节不变）

- `_compile_page(...)`：单页 → (name, svg, w_mm, h_mm) | 页失败——同步循环体与 executor 共用。
- `_assemble_pages_pdf(page_specs, title, sink) -> (pdf_bytes, page_count)`：命名 @page HTML + render_pdf_exclusive + pypdf 计数——两路共用。
- 同步路径 `render_publication_pdf` 改为调用接缝；既有测试（atlas pipeline 15 用例/IR golden/route）必须原样绿。

## 8. 前端

- `frontend/lib/api/atlas-jobs.ts`：`submitAtlasJob(req)`、`getAtlasJob(id)`；类型与 §4 对齐。
- `frontend/lib/api/jobs.ts`：JobKind 联合 + `"atlas_export"`；JobView + `page_status?`。
- TasksTab：kind=atlas_export 卡片渲染页级进度列表（page_id/title/status 徽标），result_ref→下载链接（PDF/manifest）。
- map-studio 导出面板新增"批量图册"提交区（driver/layer/property/budget/cover/scope/dpi）→ submitAtlasJob → 任务中心联动；i18n en-US/zh-CN。
