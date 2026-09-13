# AC-V11 W6 交付台账（出版与交付）

> 波次:W6 · ADR-0166 · 状态:已完成 · 回滚点:tag `ac-v11-w6`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W6.1 | IR 三渲染器 parity | `frontend/lib/layout/ir-parity.test.ts`（IR/React DOM/canvas-SVG 三方锚点 + Z 序语义） | 3 例 | 放置层对拍锁定；像素级随接线增量 |
| W6.1b | 残壳清理 | `lib/map-exporter/` 目录删除（测试迁 `lib/map-kit/exporter-engine.test.ts`）；`exportCommands.ts` 陈旧注释修正 | 迁移后 18 例全绿 | S1 债扫描项销项 |
| W6.2 | PDF 图体矢量 | `frontend/lib/map-kit/pdf-vector.ts`（svg2pdf.js）+ `exporter.ts`（vectorSvg 优先 + 栅格兜底 + onBodyMode） | 4 例（无 Image XObject/路径算子/栅格对照/fail-soft/结构确定性） | 两处互操作实证（UMD peer 全局、jsdom getBBox）入 ADR |
| W6.3 | 高分评估 | `docs/dev/ac-v11-highdpi-evaluation.md`（三方量化对比）+ `lib/export/tile-zoom-plan.ts` | 4 例（增益封顶/无 headroom 披露/确定性） | 重建实例方案否决有据；C 折中 opt-in |
| W6.4 | 格式矩阵 | `docs/dev/ac-v11-export-formats.md` | — | 缺口四条移交登记 |
| W6.5 | 批量导出队列 | `app/services/export_batch_queue.py` | 3 例（串行/重试/断点续传/fail-soft/守卫） | 串行恒 1 assert |
| W6.6 | 可访问性清单 | `app/lib/cartography/accessibility_manifest.py` | 3 例（完整/诚实缺失/色盲实测/确定性） | context_matrix 同源 |

## 门禁

`SKIP_BROWSER=1 quality_gate_local.sh`（独占运行——见下「运维教训」）：
913 passed；覆盖率 57.68%（floor 50）；全步通过。

**运维教训（入档）**：pytest 并发进程共用 cwd 下 `.coverage` 数据文件
（addopts 默认 collect）——门禁与其它 pytest 同时跑会互相覆盖数据，
出现「913 passed 但覆盖率 2%」的假象。纪律：**门禁运行期间不得并发
任何 pytest**（比 §0.4「至多 2 个重型命令」更紧：pytest 类命令互斥）。

## 遗留与移交

- `options.vectorSvg` 的生产端接线（导出命令把 `buildVectorSvgExport`
  产物传入）→ 后续批次（接口已就位）。
- GeoTIFF 导出菜单 + 打印档自动化核验 → W8 矩阵。
- 批量队列路由暴露；可访问性清单随件携带 → 后续批次。
