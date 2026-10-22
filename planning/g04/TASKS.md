# G04 TASKS

状态图例：[ ] pending / [~] in progress / [x] done / [-] deferred(reason)

## W0 基线
- [x] recon（RECON.md）+ overlap matrix + venv（uv，CI 同款 lock）
- [x] worktree feat/g04-cartography-template-assets-vnext @ ee8c14f8
- [x] 干净 master cartography lane 基线：2345P/19S/3xf/0F
- [x] PLAN.md / TASKS.md / TEST_MATRIX.md 骨架

## W1 lib 契约层
- [x] app/lib/cartography/template_assets.py：AssetPayloadSpec（variant 引用式 / inline）、TemplateAssetReference、materialize（base+overrides→MapCompositionTemplate+派生契约）、validate_asset_template（单模板校验）、asset_fingerprint、BUILTIN_TEMPLATE_ASSETS（4 族）
- [x] composition_templates.py：registry upsert_asset（单调 revision、asset.* 前缀专用、不动 seed/pack）
- [x] composition_contract.py：CompositionIdentity additive asset_id/asset_revision + read 侧
- [x] tests/cartography/test_template_assets_lib.py（21 例）（物化确定性/指纹稳定/校验 fail-closed/内置 4 族合法/upsert 单调与防撞）
- [x] tests/cartography/test_composition_contract_v1.py（无回归） 既有测试不回归（身份块 additive 零影响）

## W2 生产入口
- [x] app/models/composition_template_asset.py（两张表）+ migrations/versions/0098_*.py（additive+guard+downgrade）
- [ ] tests/unit/test_composition_template_assets_db.py（建表/迁移共存/唯一约束）
- [x] app/services/templates/composition_assets.py：生命周期服务（create/save_from_spec/new_version/publish/retire/restore/list_scoped/resolve for_apply|for_replay + registry 惰性刷新 + savepoint 重试）
- [x] app/api/routes/composition_template_assets.py REST + main.py 挂载（404 不泄露/403 谓词）
- [x] app/tools/composition_tools.py：apply 可选资产参数；新工具 webgis_list_composition_template_assets / webgis_save_composition_template；reason codes additive
- [x] product 路径：_product_composition_identity 资产字段；product_manifest composition 投影 additive；export 前 ensure-registered（services 层）
- [x] tests/unit/test_composition_template_assets_api.py（10 例）、tests/unit/test_composition_asset_tools.py、tests/cartography/test_template_asset_apply_v1.py（e2e：真 mapspec_store；幂等/锁/指纹变化/旧 spec 兼容）
- [x] tests/cartography/test_h02_product_manifest.py（无回归+资产断言） 不回归 + 新增资产身份断言

## W3 失败/恢复/并发
- [x] 并发 new_version（uq 撞号守卫+savepoint 重试）（uq 撞号重试→409 语义）；publish 竞态（current_revision 单调）
- [x] IDOR：跨 org/跨用户读 404/非 creator 写 403/admin 全见、非 creator 写 403、admin 全见
- [x] retire 后：新 apply 拒、钉 revision 重放成功、restore→再 publish（reason）、钉 revision 重放成功、restore→再 publish
- [x] 损坏/非法 payload fail-closed + 坏身份块宽容读：物化失败 fail-closed 不落盘；registry 不污染
- [x] 资产模板升 revision 后：钉旧版重放/新 apply 取 current：旧 spec（钉旧 revision）重渲染/导出一致；新 apply 取 current

## W4 前端 + 回归
- [x] frontend/lib/api/composition-template-assets.ts + vitest 7（list/get/create/save-from-session/publish/retire/restore）+ vitest
- [x] 窄入口面板 + vitest 6 + i18n 双语（不新增巨型面板）
- [x] tsc exit 0 / vitest 14+9 / eslint 0 / CJK 门绿（-–maxWorkers=2）
- [x] cartography lane 2345P=基线逐项一致 + 179 存量回归绿，对照 W0 基线；ruff check 改动路径

## W5 独立 review
- [x] subagent C 两轮：R1 FIX-FIRST（P0×1/P1×2/P2×3）→ 修复 → R2 复审中（fake green/吞噬/泄漏/并发/双注册表/影子入口/性能）
- [x] 修复 5 commit（含红测试回归）（关键调用链）→ 无 P0/P1
- [x] REVIEW.md（第二轮结论由 reviewer 追加）

## W6 文档 + PR
- [x] docs/adr/0230-composition-template-asset-lifecycle-v1.md（或顺延编号）
- [ ] gh pr create（base master，head feat/g04-cartography-template-assets-vnext；body 含 overlap/DoD/测试/风险/回退）
- [ ] planning/g04/PR.md 记录 URL；DECISIONS.md 补全；TEST_MATRIX.md 回填真实退出码
