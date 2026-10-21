# G04 TASKS

状态图例：[ ] pending / [~] in progress / [x] done / [-] deferred(reason)

## W0 基线
- [x] recon（RECON.md）+ overlap matrix + venv（uv，CI 同款 lock）
- [x] worktree feat/g04-cartography-template-assets-vnext @ ee8c14f8
- [~] 干净 master cartography lane 基线（后台 exec_f58f374a）
- [x] PLAN.md / TASKS.md / TEST_MATRIX.md 骨架

## W1 lib 契约层
- [ ] app/lib/cartography/template_assets.py：AssetPayloadSpec（variant 引用式 / inline）、TemplateAssetReference、materialize（base+overrides→MapCompositionTemplate+派生契约）、validate_asset_template（单模板校验）、asset_fingerprint、BUILTIN_TEMPLATE_ASSETS（4 族）
- [ ] composition_templates.py：registry upsert_asset（单调 revision、asset.* 前缀专用、不动 seed/pack）
- [ ] composition_contract.py：CompositionIdentity additive asset_id/asset_revision + read 侧
- [ ] tests/cartography/test_template_assets_lib.py（物化确定性/指纹稳定/校验 fail-closed/内置 4 族合法/upsert 单调与防撞）
- [ ] tests/cartography/test_composition_contract_v1.py 既有测试不回归（身份块 additive 零影响）

## W2 生产入口
- [ ] app/models/composition_template_asset.py（两张表）+ migrations/versions/0098_*.py（additive+guard+downgrade）
- [ ] tests/unit/test_composition_template_assets_db.py（建表/迁移共存/唯一约束）
- [ ] app/services/templates/composition_assets.py：生命周期服务（create/save_from_spec/new_version/publish/retire/restore/list_scoped/resolve for_apply|for_replay + registry 惰性刷新 + savepoint 重试）
- [ ] app/api/routes/composition_template_assets.py REST + main.py 挂载（404 不泄露/403 谓词）
- [ ] app/tools/composition_tools.py：apply 可选资产参数；新工具 webgis_list_composition_template_assets / webgis_save_composition_template；reason codes additive
- [ ] product 路径：_product_composition_identity 资产字段；product_manifest composition 投影 additive；export 前 ensure-registered（services 层）
- [ ] tests/unit/test_composition_template_assets_api.py、tests/unit/test_composition_asset_tools.py、tests/cartography/test_template_asset_apply_v1.py（e2e：真 mapspec_store；幂等/锁/指纹变化/旧 spec 兼容）
- [ ] tests/cartography/test_h02_product_manifest.py 不回归 + 新增资产身份断言

## W3 失败/恢复/并发
- [ ] 并发 new_version（uq 撞号重试→409 语义）；publish 竞态（current_revision 单调）
- [ ] IDOR：跨 org/跨用户读 404、非 creator 写 403、admin 全见
- [ ] retire 后：新 apply 拒（reason）、钉 revision 重放成功、restore→再 publish
- [ ] 损坏/非法 payload：物化失败 fail-closed 不落盘；registry 不污染
- [ ] 资产模板升 revision 后：旧 spec（钉旧 revision）重渲染/导出一致；新 apply 取 current

## W4 前端 + 回归
- [ ] frontend/lib/api/composition-template-assets.ts（list/get/create/save-from-session/publish/retire/restore）+ vitest
- [ ] 窄入口：组合模板资产选择/保存最小 UI（不新增巨型面板）
- [ ] pnpm exec tsc --noEmit + vitest 定向（-–maxWorkers=2）
- [ ] 后端：cartography lane + 新增/受影响 unit 全绿，对照 W0 基线；ruff check 改动路径

## W5 独立 review
- [ ] subagent C 逐文件审 diff（fake green/吞噬/泄漏/并发/双注册表/影子入口/性能）
- [ ] 修复 + 同审查员再审（关键调用链）→ 无 P0/P1
- [ ] REVIEW.md 记录两轮结果

## W6 文档 + PR
- [ ] docs/adr/0230_template_asset_lifecycle_v1.md（或顺延编号）
- [ ] gh pr create（base master，head feat/g04-cartography-template-assets-vnext；body 含 overlap/DoD/测试/风险/回退）
- [ ] planning/g04/PR.md 记录 URL；DECISIONS.md 补全；TEST_MATRIX.md 回填真实退出码
