# G47-DORLING-CARTOGRAM — PR_HANDOFF

## 分支与提交

| 项 | 值 |
|---|---|
| worktree | `/home/kevin/projects/webgis/webgis-worktrees/g47-dorling-cartogram-20261115T124822Z` |
| 分支 | `zcode/g47-dorling-cartogram-20261115T124822Z` |
| base | `655f769ca17f17c5797da13b4c299b8fa600e370`（`origin/master`，open PR = 0） |
| 提交 | 见 `git log 655f769c..HEAD`（feat 后端 → feat 前端 → test+文案与生成物 → docs 账本） |
| 主工作区 | 只读，未改动任何文件（`vendor/pi`、`.claude/skills/*` 均未触碰） |

## 复现验证（全部本机实际执行过）

```bash
WT=/home/kevin/projects/webgis/webgis-worktrees/g47-dorling-cartogram-20261115T124822Z
PY=/home/kevin/projects/webgis/webgis-worktrees/.g37-venv/bin/python

# 后端领域 + 回归（160 passed）
cd "$WT" && $PY -m pytest tests/unit/test_g47_*.py -p no:randomly -q --no-cov

# 纸面接缝 + 服务生命周期（19 passed；不需要重阶段锁）
cd "$WT" && $PY -m pytest tests/integration/test_g47_print_diagram_seam.py \
    tests/integration/test_g47_service_lifecycle.py -p no:randomly -q --no-cov

# 三格式真实导出回读（14 passed；**需要重阶段锁**）
cd "$WT" && $PY -m pytest tests/integration/test_g47_publication_readback.py \
    -p no:randomly -q --no-cov

# Pi 旅程（真子进程 + 生产扩展 + 生产 dispatch + 本域服务；需 vendor/pi 已构建）
cd "$WT" && PI_JOURNEY_E2E=1 $PY -m pytest tests/integration/test_pi_g47_dorling_cartogram_journey.py \
    -p no:randomly -q --no-cov

# 前端
cd "$WT/frontend" && TMPDIR=/home/kevin/.cache/g47tmp node_modules/.bin/tsc --noEmit
cd "$WT/frontend" && node_modules/.bin/eslint lib/cartogram components/cartogram \
    components/hud/dorling-cartogram-panel.tsx components/hud/layer-style-panel.tsx \
    components/chat/cartography-result-card.tsx --max-warnings 0
cd "$WT/frontend" && TMPDIR=/home/kevin/.cache/g47tmp node_modules/.bin/vitest run \
    lib/cartogram components/cartogram test/i18n/key-completeness.test.ts --maxWorkers=2
```

环境注意（会浪费时间的三件事）：

1. **`/tmp` 是 10MB tmpfs**：Vitest 在默认 `TMPDIR` 下会 `ENOSPC`。必须 `TMPDIR=/home/kevin/.cache/g47tmp`。
2. **`vendor/pi` 子模块 clone 会失败**（`fetch-pack: unexpected disconnect`）：复用同 pin
   `b4afdddf…` 的既有 worktree 检出即可（`pi_vendor_identity()` 应报
   `entry_built=True, kind=bundle, matches_expected_pin=True`）。
3. **重阶段锁**：`$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`
   被其它 worktree 占用时，`readback` 用例会在 `flock` 上排队（设计行为）。非加锁用例可先跑。

## 改动面（相对 base）

| 面 | 文件 |
|---|---|
| 后端领域 | `app/services/cartogram_authoring/`（12 模块，新增） |
| HTTP | `app/api/routes/cartogram_authoring.py`（新增）、`app/main.py` |
| 工具 | `app/tools/cartogram_tools.py`（新增）、`app/tools/__init__.py` |
| 作业 | `app/services/jobs/lifecycle.py`（`cartogram_layout`，加性） |
| 出版 | `app/lib/cartography/print_layout_schema.py`、`print_layout_authoring/{compile,html}.py` |
| 文案 | `render_diagnostics.py`、`model_packs/presentation_views.py`、`frontend/lib/map-kit/frame-composer.ts` |
| 生成物 | `docs/cartography/map-model-catalog.md`、`frontend/lib/map-components/component-catalog.generated.json` |
| 前端 | `frontend/lib/cartogram/`、`frontend/components/cartogram/`、`components/hud/{dorling-cartogram-panel,layer-style-panel}.tsx`、`messages/*`、`lib/i18n/messages.ts` |
| 测试 | `tests/unit/g47_fixtures.py` + 7 个 `test_g47_*.py`；`tests/integration/test_g47_*.py` 3 个 + Pi 旅程 1 个 |

## 发布声明（不许被 PR 叙事夸大）

- 交付的是 **Dorling 圆面积 cartogram**：面积 ∝ 数量、圆心是**示意布局**、保留原地理轮廓作定位参照。
- **没有**实现面域多边形变形（Gastner-Newman 等）；`cartogram_map` 视图仍是 `planned`，
  `cartogram_unsupported` 继续对它诚实降级（两处文案已分别说清）。
- 布局坐标**不是**经纬度，不能用于距离/面积/路由计算；artifact 与纸面 spec 都显式声明且拒绝地理字段。
- 质量门未过仍可保存布局（如实记 `publication_ready=false`），但应用必须显式 `allow_diagnostic`
  并标 `diagnostic`。

## 残留风险（未验证项，不当作已通过）

| 项 | 状态 |
|---|---|
| Pi 端到端旅程在本轮重阶段锁竞争下未跑完 | ⚠️ 测试已就位，需 `PI_JOURNEY_E2E=1` 且锁空闲时补跑 |
| 真实浏览器 E2E（缩放/平移/选择联动/键盘） | ⚠️ 未执行（需 Playwright 1 worker + dev server） |
| 大样本压测（≈400 单元 / 8000 边） | ⚠️ 未执行；参考轮廓 9000 点上限与简化容差随规模变化 |
| `stop_reason=time_budget` 下的逐位一致性 | ⚠️ 边界未声明（慢机布局不同，`elapsed_s` 已在 volatile） |
| `scripts/check_generated_staleness.py` 报 `docs/quality/*` 陈旧 | base 既有债，未触碰 |

## PR 正文草稿

```markdown
## 目标
交付 Dorling 圆面积 cartogram（面积 ∝ 数量）的作者链路与真实出版：计算内核 → 独立质量
oracle → durable job → 单笔 CAS apply → SVG/PNG/PDF，外加 6 个 Pi 工具与一个 HUD 工作台。

## 关键设计
- **示意坐标隔离**：artifact 声明 `coordinate_space=diagram`、`is_geographic=false`，
  无 CRS/经纬度圆心；圆为 `radius=sqrt(k·value)`（k 全图唯一，禁逐圆 clamp）。
- **独立质量 oracle**：解析几何复核圆面积、交叠 lens 面积、位移、邻接；两道重叠门
  （任一圆对硬门 / 超容差对数占比门）**互不派生**；面积门按**落盘精度**复核。
- **同代才可写**：会话锁 + 代次前后核对 + 单次深拷贝；apply 期 descriptor 复核，
  读不到即 `SOURCE_VERIFY_FAILED` 拒绝；`expected_revision` 不符即 `CAS_SUPERSEDED`。
- **出版窄接缝**：G34 新增受控 `diagram` 组件（严格模式拒绝 `crs`/`bbox`），
  圆与参考轮廓共用同一 `scale/origin`，HTML 内联 SVG data URI。

## 验证（本机实际执行）
- `tests/unit/test_g47_*.py`：160 passed
- `tests/integration/test_g47_print_diagram_seam.py` + `test_g47_service_lifecycle.py`：19 passed
- `tests/integration/test_g47_publication_readback.py`：SVG/PNG/PDF 真实内容独立回读
- 前端：`tsc --noEmit` 0 错、`eslint --max-warnings 0` 0 告警、Vitest 29 passed
- 独立评审（未参与实现）：3×P0 + 3×P1 + 5×P2 全部修复，见
  `planning/G47-DORLING-CARTOGRAM/REVIEW.md`

## 明确的非目标
面域多边形变形 cartogram（仍未支持，继续诚实降级）；布局坐标任何时候都不得当经纬度用。
```
