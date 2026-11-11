# G24 ACCEPTANCE — 验收矩阵映射（代码 → 测试 → 证据）

测试命令：
- 后端：`python -m pytest tests/unit/test_selection_handles.py tests/unit/test_selection_handle_routes.py tests/unit/test_selection_tools.py tests/unit/test_g24_identity_guards.py tests/unit/test_g24_scale_evidence.py tests/acceptance/gis_agent/test_g24_selection_edit_journey.py tests/unit/feature_edit/ tests/unit/test_mvt_positional_id.py tests/unit/test_mvt_encoder.py tests/unit/test_feature_pages.py --no-cov -q`
- 前端：`cd frontend && npx vitest run lib/edit/ lib/api/selection-handles.ts lib/map-components/selection-table-data.test.ts components/map/feature-layer-editor.test.tsx lib/selection/ components/map/map-panel.selection-truth.test.tsx lib/map-components/`

| # | 验收 | 实现锚点 | 测试锚点 | 结果 |
|---|---|---|---|---|
| A01 | inline 小图层不回退 | `feature-layer-editor.pickEditTarget` inline 分支 | feature-layer-editor.test.tsx「inline 层保持本地命中」 | ✅ |
| A02 | MVT 点击真实对象 | map-panel click（既有）+ `_wfid` 链修 | mvt-pick.test + map-panel.selection-truth.test.tsx（既有 3 例） | ✅ |
| A03 | 完整 geometry 不是瓦片碎片 | `pickAuthoritativeFeature` 只用服务端几何判定 | mvt-pick.test「A03 候选几何不参与判定」+ editor test vertexTarget | ✅ |
| A04 | 跨 tile 只选一次 | `candidateIdsFromHits`/`projectBrushHits` id 去重 | mvt-pick.test「跨子层/多副本去重」+ brush-select.test（既有） | ✅ |
| A05 | world-wrap 去重 | 同上（世界副本同 id） | 同 A04 用例 | ✅ |
| A06 | >2^53 id 不丢精度 | `mvt._encode_value` ±(2^53-1) 界字符串化 | test_g24_identity_guards `test_mvt_int_beyond_2p53…` | ✅ |
| A07 | 缺失/碰撞 id 只读或 typed 拒绝 | `ambiguous_target` finding + `missing_ids` 披露 | test_g24_identity_guards 2 例 + handles/routes tests | ✅ |
| A08 | Polygon hole 不误命中 | `preciseFeatureHit` 外环减洞（邻近不救回） | geometry-hit.test A08 两例 + MultiPolygon 洞例 | ✅ |
| A09 | 长线段中段命中 | 线=逐段距离 | geometry-hit.test A09 + editor inline 中段例 | ✅ |
| A10 | MultiGeometry 真实处理 | hitParts 部件分解 | geometry-hit.test A10/MultiLine/MultiPoint | ✅ |
| A11 | 框选 300 对象不是前 50 | handle 分页枚举全集 | routes test `test_bbox_handle_pages_full_selection_not_first_50`（300 枚举）+ journey 续页 | ✅ |
| A12 | 选区不依赖已加载瓦片 | 服务端 count（渲染面 matched_count 被替换/披露） | routes bbox count=300（服务端）+ map-panel 句柄接线（matchedAuthoritative） | ✅（服务端侧测试；浏览器端见 E2E 边界） |
| A13 | 属性表分页总数/完整性如实 | selection 视图 count/count_exact/未知三态 | selection-table-data.test 3 例 + routes meta | ✅ |
| A14 | 跨日期变更线范围正确 | `_normalise_bbox` w>e 拆两箱 | handles test AM split + routes AM 例 | ✅ |
| A15 | 过期 revision 拒绝写 | G14 CAS（既有）+ create v=409 | routes `test_create_with_stale_v_409` + feature_edit 既有 stale 用例 | ✅ |
| A16 | 跨租户 id/handle 拒绝 | 三重绑定 + require_owned_session | routes cross-session 404 + tools cross-session 例 | ✅ |
| A17 | 换 session 迟到响应不污染 | brush 句柄 epoch+revision 守卫；单选 seq（既有） | map-panel 既有 selection-truth + 新守卫代码（review 复核） | ✅（守卫逻辑随 vitest map-panel 套件回归） |
| A18 | 编辑成功真实 commit + undo | G14（既有）+ 权威拾取接线 | feature_edit 既有 123 例 + editor test delete 提交 | ✅ |
| A19 | split/merge 后 selection 消费 lineage | `applyLineageToSelection` + 回执 wfids | journey split 例（服务端血缘）+ bridge 行为（layer-edit-bridge.test 既有 V17 扩展，P5） | ✅ |
| A20 | 取消手势零服务端 mutation | pick AbortSignal；Escape 零写（既有 V05） | mvt-pick.test A17 取消零请求 + editor 未命中零提交 | ✅ |
| A21 | 标注与 fieldcompute 同一 scope | fieldcompute 经 materialize ref（ScopeSpec 不变）；标注链同 FEATURE_ID_KEYS | journey materialize（fieldcompute 通道）+ G16 链统一（identity guards） | ✅（通道级；未改标注 UI） |
| A22 | 数据刷新旧 handle 不静默复用 | revision 前进 → 410 + 释放 | routes revision 失效 + tools stale + hook stale 三层 | ✅ |
| A23 | 缓存按 owner/source revision 隔离 | ref_lifecycle epoch（既有）+ handle revision 绑定 | 既有 mvt epoch race 套件 + handles 410 | ✅ |
| A24 | 百万点档传输/内存有界 | 100k 档证据 + 1M 档 gated | test_g24_scale_evidence（100k：132B/0 扫描；1M G24_MILLION=1） | ✅（100k 实测；1M 档位披露） |
| A25 | 真实浏览器 MVT 点击到编辑走通 | — | **blocked/未做**：Playwright E2E 未在本方向 worktree 执行（重资源锁被并行方向长期占用 + 浏览器环境依赖 dev server 全栈）；以组件级 vitest（真实 maplibre mock 层之上）+ 服务端旅程测试覆盖同等断言，浏览器级留待集成 | ⚠️ 部分（见 TEST_EVIDENCE「边界」） |
| A26 | 真实 Pi 查询选择集不偷传 id | selection_inspect 只认 handle | journey Turn 2（工具只接受 handle/ref） | ✅ |
| A27 | readonly 远程瓦片明确限制 | 无 ref → 不进编辑通道（editTarget 需 `_refId`）；pick 链对无 ref 层不生效 | 既有 editTarget 语义 + mvt-pick sessionId/refId 前置（review 复核） | ✅（语义边界；无专门 UI 文案） |
| A28 | 旧 schema/project/无 selection 不崩 | 可选字段 + 空态 | 全量回归（selection-store/v4、map-panel、feature_edit 既有套件） | ✅ |

## 验证层级对照

1. deterministic unit/contract：上表绝大多数锚点（vitest + pytest）。
2. 真实工具旅程（无 LLM stub 需求 —— 工具为确定性只读；Pi 原生 RPC 面由 #2081 套件覆盖）：journey 测试走真实 ToolRegistry dispatch + 真实 session 数据面 + 真实 feature_edit 事务。
3. 真实浏览器：**未执行**（A25，见 TEST_EVIDENCE.md 边界节）。
