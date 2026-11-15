# LOOP_LEDGER — G46 执行账本

## 状态（截至 2026-11-16 01:1x）
- base: 655f769ca17f17c5797da13b4c299b8fa600e370（master，G40 #2124 已合并）
- branch: zcode/g46-linked-exploration-20261115T154748Z
- head: ec68d86fb（capability 注册 + Pi driver 修复）
- commits: 5（M1 后端 / M2 前端 / 导出+Pi / 面板修 / capability）

## 已验证（真实执行）
- 后端单元 50+：contract/buckets/filter 语义对齐（独立 oracle 手算）
- 集成 19：服务旅程（E02-E30 服务端面）+ 三格式导出真实编码
  （rsvg-convert/WeasyPrint 产物魔数+尺寸+同计划一致性；统一锁内）
- alembic 空库 upgrade head：exploration_views 落地、head=g46
- 前端 22（lib/组件）+ 既有相关面 271+169 全绿；eslint/tsc 净（本域）
- Pi journey：capability 修复后重跑中（结果待记录）

## 发现并修复的实现 bug（自证）
1. bool/数字 dict hash 归并（Python hash(True)==hash(1)）→ type-aware 分组键
2. mapspec_filter == null/!= null 与 MapLibre/evaluate_where 不一致 → 三处对齐
3. ChartSpec bins 下限 10 过严 → 2
4. patch_component origin=user 无 expected_revision 被拒 → origin=agent + is_error 检查
5. 组件工厂/ComponentType 词表缺 exploration_chart_panel → 三处登记
6. SVG 编译器不水化 ref：sources 表 inlineData 同代水化
7. annotation 行走 options.text
8. capabilities 未注册 id 被 runtime manifest 严格校验拒绝 → 注册+改词表

## 既有失败（干净 base 复现，非本域）
- tests/test_alembic_metadata.py % 编码 URL 用例（连接即失败）
- cartography-result-card.tsx tsc 2 错（G47 #2125 在改的文件）
- 均属 issue #2123 报告的基线

## 未完成/未运行（诚实披露）
- 浏览器 e2e journey（Playwright）未运行 —— 逻辑面已由组件测/store 测覆盖
- Pi journey 重跑结果待记录（本文件下轮更新）

## 下一步
1. Pi journey 结果记录
2. E23/E24 前端竞态补充测试（generation/epoch 拦截单测）
3. 独立 review（未参与实现的 subagent）→ 修复 → 复审
4. fetch 最新 master → 兼容检查 → push → PR
