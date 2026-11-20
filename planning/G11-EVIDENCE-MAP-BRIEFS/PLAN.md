# G11-EVIDENCE-MAP-BRIEFS — PLAN

产品目标：用户把真实分析结果整理成**可编辑的地图简报**：地图/指标/图表/结论可追溯到固定版本数据与方法；保留人工编辑；发布前预检；输出可离线阅读的私有 HTML 与证据清单；复用既有受控分享。

## 架构总则

- **MapBriefSpec 继承 StoryMapSpec**（additive，pydantic extra=allow round-trip 兼容 v1.1 消费者），新增 `provenance / blocks / facts` 三层：blocks=编辑单元（稳定 ID+origin+locked），facts=结构化数值事实（唯一可 verified 的数值载体），provenance=会话/产物/MapSpec 指纹溯源。
- **证据=引用+只读投影**：EvidenceBinding 只存 ref+口径+用户可读来源标签+内部 fingerprint；验证复用 `evidence_claim.verify` positive-proof 管线；重读失败封闭（unverified+原因），绝不编造。
- **不建第二事实库**：artifact 真身在 ArtifactRegistry/CAS；release 真身=不可变 release 行+CAS 内容寻址；claims 编译期投影进 brief 资产。

## 数据模型（app/lib/storymap/brief_spec.py）

```
MapBriefSpec(StoryMapSpec):
  brief_schema_version="brief-1"
  provenance: BriefProvenance{session_id,turn_id,artifact_refs,mapspec_fingerprint,generator(deterministic|stub_llm|llm),generated_at}
  blocks: [BriefBlock{id,chapter_id,kind∈{text,map_view,metric,chart,table,method,limitation,source},order,origin∈{auto,manual},locked,text,fact_id,widget_id,map_view,binding}]
  facts: [BriefFact{fact_id,label,kind∈{count,sum,mean,rate,ratio,change,other},value,unit,basis:EvidenceBinding,denominator_fact_id,baseline_fact_id,status∈{verified,warning,unverified},status_reason,origin}]
EvidenceBinding{artifact_ref,dataset_version_ref,claim_id,source_label,produced_at,method,stat_semantics,unit,artifact_sha256,scope_spatial,temporal_window,bound_at}
BriefMapView{center,zoom,pitch,bearing,visible_layer_ids,mapspec_fingerprint,static_image_ref}
校验：block/fact/chapter 交叉引用存在且唯一；ratio 必须有 denominator；change 必须有 baseline；allow_inf_nan=False。
```

## 持久化（migrations/versions/0104_map_briefs.py）

- `map_briefs`：id/conversation_id FK CASCADE/title/spec JSON≤512KB/revision CHECK≥1/时间戳（沿用 story_map_assets 先例）
- `map_brief_releases`：id/brief_id FK **SET NULL**（草稿删除 release 保留）/conversation_id 冗余/release_no/frozen_revision/status∈{ready,failed}/manifest JSON/content_sha256（幂等键）/html_object_key/share_code UNIQUE NULL/share_expires_at/created_at；UNIQUE(brief_id,release_no)

## API（app/api/routes/brief.py，prefix=/briefs，main.py 一行挂载）

会话域（require_owned_session）：`POST /sessions/{sid}/draft`、`GET /sessions/{sid}`、`GET/PATCH /{id}`、`POST/PATCH/DELETE /{id}/blocks[/{block_id}]`、`POST /{id}/chapters`、`POST /{id}/regenerate`（mode=metrics_only|unlocked_text_only）、`GET /{id}/evidence/{fact_id}`、`POST /{id}/preflight`、`POST /{id}/releases`（幂等）、`GET /{id}/releases[/{rid}[/manifest|/download]]`、`POST|DELETE /{id}/releases/{rid}/share`
公共（限流）：`GET /shared/{code}`、`GET /shared/{code}/view`
写操作全部带 `expected_revision` → 409 冲突。

## 预检（brief_preflight，lib 纯函数）

检查项 → verdict pass/fail/not_evaluated：来源授权/引用可解析/数值单位方法一致（fact-check）/地图版本与镜头覆盖/导出可用性/内容缺失/隐私扫描/署名许可。
总判定：`deliverable`（MUST 全 pass）| `draft_exportable`（显式带披露可导出）| `blocked`（强事实错误/跨 owner/秘密泄漏/必需图件缺失）。

## 事实检查（brief_factcheck，确定性规则）

值一致（vs artifact 权威值）/单位一致/ratio→denominator、change→baseline 存在且口径可比/新鲜度（artifact revision vs registry head → stale）/权限可达（失败封闭→unverified+原因）。LLM 或手工文字中的数字不因此 verified。

## 离线导出（brief_export，纯函数 + 服务壳）

- 单文件自解压 HTML（零外链、复用 _embed_json/esc/sanitize 先例）：封面（版本/draft 徽标/指纹）、章节、块渲染（metric 卡+status 徽标+来源脚注、chart 内联 JSON 纯 JS 渲染（复杂图降级表格，诚实标注）、map_view 内联 SVG（compile_mapspec_to_svg；缺失→可读告警，不伪造空白图）、method/limitation/source）
- 机器可读 JSON manifest：files[{name,sha256,bytes,kind,status}]、evidence manifest（fact→binding→fingerprint→verdict）、dataset_versions、mapspec_fingerprint、draft 标记
- 附件 CSV（可选，公式注入防护）；ZIP 仅在必要时（受限路径/数量/字节/压缩比预算，不跟随 symlink）
- 安全负例：script 早闭合/XSS/危险 URL/CSV 公式/超深 JSON/超大打包/路径穿越

## 工具（app/tools/brief_tools.py，session_id 注入探针）

`brief_create_draft / brief_inspect_evidence / brief_update_block / brief_preflight / brief_freeze_release / brief_export`；结果紧凑（章节/指标数、证据缺口、产物 ref、文件状态）。

## 前端

- `frontend/lib/api/brief.ts` client；story 页编辑器解锁 specView 模式（块 CRUD/锁定/重排/正文/章节管理，409 冲突提示）
- 证据侧栏（metric 点击→binding+溯源+跳转产物）；map_view 点击→范围/时间窗/数据版本说明
- 预检面板（verdict 列表+阻断项）、冻结 release、私有 HTML 下载（apiFetchBlob）；share 管理
- i18n story.json 增 key（中英同步）；e2e j13 双模式（mock 桩 + real 真后端：草稿→编辑→证据→预检→冻结→下载→外部分享 401/403/404）

## 里程碑与验证

M2 模型+迁移+单测 → M3 编译器+factcheck → M4 编辑 API+regenerate → M5 preflight+release → M6 导出+分享 → M7 工具+Pi 链+e2e → M8 独立 Review → M9 master 复验+文档+PR。
每个 MUST 旅程对应 Oracle BRF-O01..O12 的证据记录在 TEST_MATRIX.md / FEATURE_ACCEPTANCE.md。
