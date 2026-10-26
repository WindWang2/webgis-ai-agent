# G3D05 验证证据（本地、可复现命令）

环境：主仓 `.venv`（Python 3.13.17，依赖集与基线一致）；`frontend/node_modules`
符号链接自主仓（同一基线 SHA、package.json 无差）。pytest `--no-cov`（本地
验证不重复 CI 覆盖率收集）；vitest `--maxWorkers=2`；`NODE_OPTIONS=--max-old-space-size=4096`。

## 后端

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/cartography/ -q --no-cov -m cartography` | **2050 passed**（含 D1 24 项新测试 + R1/R2 回归、golden corpus 再生成后 585 项、死码门 95 项） |
| `python -m pytest tests/unit/test_scene_quality_gate.py tests/unit/test_scene_tools.py -q --no-cov` | **42 passed**（D2 扩展 10 项） |
| `python -m pytest tests/cartography/test_scene_export_honesty.py -q --no-cov -m cartography` | **12 passed**（含前后端阈值互锁） |
| `python -m pytest tests/cartography/test_user_scene_mutation_api.py -q --no-cov -m cartography` | **6 passed**（D3 HTTP 契约） |
| `python -m pytest tests/unit/test_storymap_scene_v11.py -q --no-cov` | **17 passed**（D6 spec/资产/HTTP） |
| `python -m pytest tests/unit/test_storymap_orchestrator.py tests/unit/test_storymap_spec_boundaries.py tests/unit/test_storymap_compiler_export_gaps.py -q --no-cov` | **105 passed**（storymap 存量回归） |
| `python -m pytest tests/unit/test_composition_template_assets_db.py tests/quality/test_api_compatibility.py -q --no-cov` | 通过（OpenAPI/契约快照零漂移） |

## 前端（frontend/ 目录）

| 命令 | 结果 |
| --- | --- |
| `npx vitest run --maxWorkers=2` | **481 文件 / 4538 passed + 1 expected-fail**（全量，含 R1/R2 修复后复跑） |
| `npx tsc --noEmit && npx tsc -p tsconfig.test.json --noEmit` | **0 错误** |
| `npx eslint . --max-warnings 0` | **0 错误 0 警告** |

## 负例（反证）测试

- `test_scene_3d_pack.py::TestSceneDisclosurePayload::test_banned_scene_fields_rejected`
  —— options 复制场景字段（第二真相）被拒；
- `TestElevationProfilePayload`：非单调里程/NaN/Inf/超界/单点/非法 ref 前缀全拒；
- `test_no_vector_output_claims`：3D 模板声明矢量目标即红；
- `TestConformanceRequiredDisclosure::test_missing_scene_disclosure_is_error`
  —— 撤掉必填披露槽 → conformance error（护栏反证）；
- `test_user_scene_mutation_api`：词表外 mode 400（last-known-good 不变）、
  悬空 terrain 源 400、旧代 409、幂等重放不二次推进；
- `test_scene_export_honesty::test_2d_mode_no_receipt / test_no_scene_no_receipt`
  —— 2d/无场景零噪音（降级收据不滥发）；
- `test_storymap_scene_v11`：场景档越界（mode=4d/夸张 0 或 11）、visible_layers>32、
  引用不存在章节、资产损坏 typed 422（不伪造默认）、超预算 spec 拒绝。

## Golden corpus

`GOLDEN_CORPUS_UPDATE=1` 再生成新增 10 个 golden（6 个 3D 模板×模型交叉 +
4 个新组件变体），review 后入库；正常模式 585 项全过。catalog/types 投影
（`python -m app.lib.cartography.export_component_catalog` /
`ts_projection` / `catalog_docs`）全部再生成入库。

## 未运行/替代说明

- Playwright E2E（j1–j8 journeys）未跑：需要浏览器驱动与长时交互环境，
  本轮以契约级 E2E（HTTP mutation 语义 + 语义语料闭环 + 组件级行为测试）
  替代；PR 中如实披露。
- 线上 CI 不等待（工作单 §2 规则 4）。
