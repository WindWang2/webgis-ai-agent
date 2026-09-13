# qc-loop Round 1（2026-09-13）

base: `17c77c73`（origin/master）→ 分支 `quality/review-optimize-loop`

## R · Review

- 扫描范围：`app/lib/cartography/**` 全量 63 文件 / 24,059 行，4 个并行审查代理全文精读（分片清单 `round1-part{1..4}.txt`）。
- 产出：`round-1.pool.csv`，65 条候选（**P0=0 / P1=7 / P2=58**），每条含 file:line + 原文摘录 + 影响面；证据闸复核：全部 7 条 P1 由主会话逐一读码确认。
- 未知项（不进池）：21 条记入各代理 UNCERTAIN（无法从代码证实的推测一律不收）。

### 任务书既有声明的裁定

| 声明 | 裁定 | 证据 |
|---|---|---|
| `composition_selection.select_composition_alternatives` 孤儿 | **属实**（仅测试调用） | grep 全仓只有 tests/cartography/test_composition_selection_v7.py |
| `ts_projection` 孤儿 | **否定** | 它是 `frontend/lib/mapspec-compiler/types.generated.ts` 的代码生成入口（文件头注明），且有契约测试 `test_ts_projection_contract.py` 锁定；仅存在局部死代码（Q050） |
| `golden_diff` 孤儿 | 部分：`_clamp`（golden_diff.py:36）零调用（Q049） | 模块本身被 scripts/golden_baseline.py 与 tests/quality 引用 |
| label_engine/label_collision 8 组同名函数 | **属实**：7 组逐字相同 + 1 组已漂移（`_keep_upright` 归一化口径不一致） | Q040，双侧行号齐备 |

## P · Prioritize

score = severity_weight × blast_radius / (fix_cost × risk)，取 top-3：

| id | 位置 | 类别 | 分数 |
|---|---|---|---|
| Q001 | semantic_checks.py:518 | correctness P1 | 27.78 |
| Q052 | thematic_spec.py:183 | correctness P1 | 23.53 |
| Q022 | symbology.py:246 | correctness P1 | 23.08 |

落选说明：Q002（pdf_renderer 缺 `transform=fig.transFigure`，指北针/图例框从未渲染）与 Q037（render_scene 舍入与前端 Math.round 漂移）触及渲染/导出产物，按 §2 须跑完整门禁（含 golden），留待下一轮专设；Q023（component_registry fail-open）分数 17.31 排第四。

## F · Fix（每条独立 commit）

1. `0fe188e8` fix(qc-loop): guard divergent legend center check against non-numeric domain (round 1, correctness)
   - semantic_checks.py:518：center_valid 增加 `_is_num(mn) and _is_num(mx)` 前置；域非法时走既有 fail 路径，不再 TypeError 炸掉整轮语义审查。+回归测试 2 条。
2. `8c4faf00` fix(qc-loop): tolerate properties:null GeoJSON features in build_graduated_spec (round 1, correctness)
   - thematic_spec.py:183：`f.get("properties", {})` → `(f.get("properties") or {})`。RFC 7946 允许 properties:null，5 个工具接线点此前对合法 GeoJSON 直接 AttributeError。+回归测试 2 条。
3. `81e303f4` fix(qc-loop): render None skew as n/a in template-preference reason (round 1, correctness)
   - symbology.py:246：`skew_text = f"{skew:.0%}" if skew is not None else "n/a"`。median≤0 的 diverging 数据（变化检测/异常面）带模板推荐时 7 个接线点不再崩溃；非 None 分支理由串逐字不变（测试锁定）。+回归测试 2 条。
4. `e9b87796` test(qc-loop): lock design_system manifest contract, lift cartography lane coverage (round 1, gate) —— 见下。

无公共 API 语义变更（三个修复均为异常路径/输入容错，正常路径行为逐字不变），免 ADR。

## 门禁恢复（E4 处置，本轮最高优先级）

基线门禁两段红：

1. **环境红**：`pip install -e .` 因 setuptools flat-layout 多顶层包自动发现失败（本仓既存缺陷，本循环不修打包）；`pydantic_settings` 缺失即其连锁。处置：venv 改 `--system-site-packages`（Anaconda 3.13.9 全量依赖）+ 补 pytest 9.1.1 / pytest-xdist / pypdf；清华 PyPI 镜像 403 改阿里云。
2. **真实质量红**：cartography lane 覆盖率 **48.55% < 下限 50**。裁定为 master 真实回归——gate 脚本 docstring 参考值 50.1% 属今天较早的 `1fd4b035`，当前 `17c77c73` 合入 3 个 adaptive-cartography PR 后分母增大未补测试；pytest 8→9 升级后数字完全一致，排除环境计量偏差。按 §3 精神作为本轮最高优先级修复：新增 `tests/cartography/test_design_system_manifest.py`（6 条，`pytest.mark.cartography`，锁定孤儿模块 design_system.py 的 V4 投影契约）→ lane 覆盖率 **51.14%**，闸转绿。

## V · Verify

- ruff：4 个变更文件 + 4 个新测试文件全部通过。
- 回归测试：每修复的测试文件单独跑绿；lane 全量（`pytest -m cartography`，806 项）随覆盖率闸通过。
- 门禁（SKIP_BROWSER=1）：见 `round-1.gate.log`（本轮未触及导出/渲染/版面，按 §2 跳过 golden）。

## L · Log · 指标

| 指标 | 修前（pool 发现时） | 修后（open） |
|---|---|---|
| P0 | 0 | 0 |
| P1 | 7 | 4 |
| P2 | 58 | 58 |
| lane 覆盖率 | 48.55%（红） | **51.14%（绿）** |

棘轮：P0+P1+P2 总量 65→62，未上升；覆盖率只升（48.55→51.14）。判定：门禁绿 → 打 tag `qc-loop-r1`，进入 Round 2。

## 遗留（Round 2 输入）

- **P1 未修 4 条**：Q002 pdf_renderer transform（需完整门禁轮）、Q037 render_scene 舍入 parity（需完整门禁轮 + 前端对照）、Q023 component_registry fail-open、Q053 style_tokens 迁影映射与 docstring 相悖（test-only）。
- 扫描游标：gis_harness 114 文件自 `scan-order.txt` index 63 起，未扫。
- P2 池 58 条按分数就绪（死代码/重复实现为主，删除类修复注意 golden 契约测试关联）。
- 资源纪律执行情况：重型命令（lane 门禁）单线程串行；与并行开发线无冲突。
