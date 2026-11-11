# G24 REVIEW — 独立评审循环记录

Reviewer：未参与实现的独立 subagent（本目录外）；全程只读 + 自行复跑。
轮次：review（HEAD=`566624daa`）→ fix（`254d9a3f1` + docs `8921ba126`）→ re-review（R2 findings）→ fix（见 Round 2）→ re-review。

## Round 1 — verdict: REQUEST_CHANGES（2026-11-12）

### Findings → 修复映射

| # | 严重度 | finding | 修复 | commit |
|---|---|---|---|---|
| P1-1 | 阻断 | Pi 句柄无生产获取通道：IR `feature_selected` 无 handle 字段，`webgis_selection_inspect` 文档/旅程注释与事实不符 | ① IR 契约白名单 += `selection_handle`（contracts.py + contract_manifest.json additive 再生成，manifest 漂移闸绿）；② `bus.emitFeatureSelected` 透传（≤128 字符）；③ 建柄成功后 map-panel 补发事件；④ 抽取 `brush-handle.ts`（守卫/补发布/IR 补发）并以 5 例钉住；⑤ 工具文档/旅程 docstring 如实化 | fix commit（review 循环） |
| P1-2 | 阻断 | 三处新前端行为零测试而 ACCEPTANCE 标 ✅：brush 迟到守卫 / applyLineageToSelection / 句柄生命周期 | `brush-handle.test.ts` 5 例（epoch/revision/建柄失败三向守卫 + count 不可证如实）；`layer-edit-bridge.lineage.test.ts` 7 例（split/merge 替换、纯 create 不并入、跨 ref 不并集、全退役清空、无关编辑不动、无选择幂等）；`selection-handle-lifecycle.test.ts` 6 例（透传/清空/替换/同柄增强/会话切换/释放器异常静默）；ACCEPTANCE A17/A19/A26 改为实测锚点 | 同上 |
| P2-1 | 应修 | `webgis_pick_detail` 事件循环上线性全扫（100k≈66ms，1M≈0.7s 投影） | spatial index 在场 → `feature_id_index` O(1)（与路由同款）；否则线性回退 `asyncio.to_thread` | 同上 |
| P2-2 | 应修 | 工具 stale 路径与路由语义漂移（不释放死句柄；死分支 `exc.detail`） | 过期/失效句柄即刻 `handle_store.release`；删除死分支 | 同上 |
| P3-1 | 注记 | routes 测试 docstring 声称 403/超预算覆盖与实际不符 | docstring 如实（403 由共享授权层承载）；补路由级 materialize 422 用例 | 同上 |
| P3-2 | 注记 | table-panel 选中集视图 shown/status/truncated 引用全图层数据 | 三处切到 `view.*`；truncated 注记仅图层模式 | 同上 |
| P3-3 | 注记 | `_chain_stable_id`/`resolve_feature_stable_id` 注释声称「字节不变」不实（编辑创建件不再编码位置 id） | 注释如实化（有意变更，正确性优先） | 同上 |
| P3-5 | 注记 | 建柄失败静默 | `map.brush.authoritativeFailed`（zh/en）诚实反馈 | 同上 |
| P3-7 | 注记 | 句柄 64 字符截断隐患 | 透传界 64→128（对齐 IR MAX_STR_VALUE_CHARS） | 同上 |
| P3-4/6 | 注记 | brush AM 不可达（预存投影行为，披露为粗集超集）；DUPLICATION_MATRIX 引用 REVIEW.md 未建 | 本文件即补；AM 语义保持披露 | 本文件 |

### Reviewer 复跑结果（Round 1，节选）

- 指定套件 38 passed；回归（feature_edit/MVT/pages）180 passed；scale 证据 3 passed；
  vitest 指定面 132 passed；`tsc` 0；安全/并发/测试质量检查单结论：SECURITY PASS、
  CORRECTNESS PASS（见其报告）；A25 诚实性 PASS。
- Reviewer 直接代码核验三断言：pick 不用瓦片几何判定（确认）、materialize 超预算先拒后
  store（确认）、100k 索引 0 扫描（复跑确认）。

## Round 2 — re-review（fix commit 后）

修复面全部落地并本地验证：
- 后端：selection routes/tools/journey/identity 18+5+2+5 passed；IR 全族 175 passed
  （manifest 再生成）；feature_edit/MVT 回归绿。
- 前端：tsc 0；eslint（改动文件）0/0；touched 套件 20 files / 182 tests passed。
- 全量 tests/unit（HEAD 树）：20486 passed / 47 failed / 27 errors —— **47+27 与干净
  BASE（b8f2a6ecc）同条件复跑完全一致（同一测试清单逐个对照）**，即全部为 master
  预存失败，G24 引入数为 0。

（re-review 结论由 reviewer 复核后回填：见下方「Round 2 结论」。）

## Round 2 — verdict: REQUEST_CHANGES（narrow，仅 R2-1 一项阻断）

Reviewer 确认 Round 1 P1×2/P2×2 全部真修复（代码直读 + 自行复跑：后端 165
passed 含 IR 127；前端 150 passed；tsc 0；manifest JSON 语义 diff 验证纯增量）。
剩余 findings：

| # | 严重度 | finding | 修复 |
|---|---|---|---|
| R2-1 | P2（阻断） | `authoritativeFailed` 在真实失败路径不可达（brush-handle 吞错返 null，map-panel 早退；.catch 只兜 then 回调异常）| 返回值改判别联合 `{status: augmented|discarded|failed}`；map-panel 仅 `failed` 反馈、`discarded` 静默；测试同步改判别式（5 passed） |
| R2-2 | P3 | 截断框选双 `feature_selected` 事件未披露 | INTERFACES §2 双事件语义节（同 layer 取最新；计数差异=披露） |
| R2-3 | P3 | REVIEW.md 引用不存在 commit hash | 改为 `254d9a3f1`/`8921ba126` |
| R2-4 | P3 | manifest 再生成重排全文缩进（232± 行噪音） | 以 BASE 文本为模板做手术式单键插入 —— diff 2 行纯增量（drift 闸绿） |
| R2-5 | P3 | bus 映射/后端摄入无直接测试 | emitters.test +1 例（映射/缺省/128 裁剪）；IR test_contracts +1 例（透传/裁剪/白名单隔离/标量语义） |

## Round 2 结论

_PENDING_REVIEWER_ROUND3_

