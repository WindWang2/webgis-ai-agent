# G47-DORLING-CARTOGRAM — PLAN

> 目标：把「Dorling 圆面积变形图 + 真实出版」做成一条可复算、可核验、可发布的产品闭环。
> 唯一算法模式是 **Dorling 圆面积 cartogram**（不是泛 cartogram），面积 ∝ 数量，
> 布局坐标是**示意坐标**（绝非经纬度），并保留原地理轮廓作定位参照。

## 分层与顺序（实现即裁决顺序）

```
数量语义 ──▶ 地理资格/邻接 ──▶ 共同面积系数 ──▶ 有界位移求解
                                                   │
                                     图面归一 ──────┤
                                                   ▼
                                   独立质量 oracle（与求解器分离）
                                                   │
                           布局 artifact（唯一真源，含指纹）
                                     ┌─────────────┴──────────────┐
                                     ▼                            ▼
                         Pi 工具 / HUD 面板              纸面 diagram adapter
                                     │                            │
                                durable job                  SVG / PNG / PDF
                                     │
                            单笔 CAS apply（层证据块）
```

不收敛或质量门未过**不抛异常**：artifact 如实记 `status` 与 `publication_ready=false`
（保存真实失败，而不是伪成功）；只有 `apply` 会因未达门而 typed 拒绝。

## 工作包

| WP | 内容 | 落点 | 状态 |
|---|---|---|---|
| WP01 | 计划合同 + typed 错误面 | `cartogram_authoring/{contract,errors}.py` | ✅ |
| WP02 | 数量语义（正/零/缺失/非法分列，拒绝 rate/mixed） | `quantities.py` | ✅ |
| WP03 | 地理资格（身份/几何/轴序/支持矩阵/投影/代表性点） | `geography.py` | ✅ |
| WP04 | 共同面积系数 + 极端跨度拒绝 + 图例参考圆 | `quantities.build_area_scale` | ✅ |
| WP05 | 有界位移求解（重叠排斥 + 原邻接吸引，可取消） | `solver.py` | ✅ |
| WP06 | 独立质量 oracle（面积/重叠/位移/邻接） | `quality.py` | ✅ |
| WP07 | 布局 artifact（坐标隔离 + 指纹 + 参考轮廓） | `layout.py` | ✅ |
| WP08 | 强同代冻结（会话锁 + 代次前后核对 + 单次深拷贝） | `snapshot.py` | ✅ |
| WP09 | durable job + 服务面（inspect/plan/run/status/apply/export） | `service.py`、`jobs/lifecycle.py` | ✅ |
| WP10 | 纸面 `diagram` 组件 adapter（受控窄接缝） | `publication.py`、`print_layout_schema.py`、`print_layout_authoring/{compile,html}.py` | ✅ |
| WP11 | 三格式真实导出与独立回读 | `render.py` | ✅ |
| WP12 | Pi 工具 + HTTP 路由 + HUD 面板（i18n） | `cartogram_tools.py`、`api/routes/cartogram_authoring.py`、`frontend/components/{cartogram,hud}/…` | ✅ |

## 硬约束（写进实现，不靠口头约定）

1. **面积 ∝ 数量**：`radius = sqrt(k·value)`，`k` 全图唯一；逐圆 clamp/最小可见圆兜底一律禁止，
   跨度过大到无法可靠表达时 `EXTREME_VALUE_RANGE` typed 拒绝。
2. **坐标隔离**：artifact 声明 `coordinate_space="diagram"`、`units="diagram_unit"`、
   `is_geographic=false`；没有 CRS、没有经纬度圆心、没有伪 GeoJSON；纸面 spec 严格模式拒绝 `crs`/`bbox`。
3. **两道重叠门互不派生**：`max_pair_overlap_ratio`（任一圆对真实最大重叠）与
   `max_overlapping_pair_ratio`（超容差圆对占比）必须同时满足。
4. **落盘精度即核验精度**：半径/坐标只在一处量化（`RADIUS_DECIMALS`），oracle 按同一精度复核，
   artifact 不再二次 round。
5. **同代才可写**：apply 期用 ref descriptor 的当前代次比对，读不到/无法判定 → typed 拒绝
   （不做"读不到就当没过期"）。
6. **单笔 CAS**：`expected_revision` 不符即 `CAS_SUPERSEDED` 且 nothing written；第二次 apply 记 `previous`。
7. **诚实门**：未达发布资格门只有显式 `allow_diagnostic` 才能应用，回执与层证据块都标 `diagnostic`。
8. **资源有界**：要素/迭代/秒/邻接边/候选对全部有硬帽，作业每轮检查取消。
9. **i18n**：用户可见文案全部走 `messages/<locale>/cartogram.json`（zh-CN/en-US 键集一致）。

## 预算与并行纪律

- pytest 单进程、`-p no:randomly -q --no-cov`（覆盖率门不在本任务范围，跑全量 cov 只会拖慢重阶段）。
- 前端 Vitest ≤2 workers；Playwright 1 worker。
- 同仓重阶段锁：`$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`。
- 主工作区只读；全部改动在独立 worktree + feature 分支内完成。

## 完成判据

1. 本地关键验证**实际执行**（不是等 CI）：后端 G47 全量 + 前端 tsc/eslint/vitest + 真实导出回读。
2. 独立 reviewer（未参与实现）给出结论，P0/P1 全部修复并复审。
3. Pi 旅程在**真实子进程 + 生产扩展 + 生产 dispatch + 本域服务**上跑通。
4. 账本齐备：BASELINE / DEDUP_MATRIX / PLAN / INTERFACES / LOOP_LEDGER / TEST_MATRIX / REVIEW / PR_HANDOFF。
