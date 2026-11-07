# G04-CARTOGRAPHY-COMPOSER PR HANDOFF

- base/head:master ← `zcode/goal-intelligent-carto-composer-20261107`(基于 585812ee4,实时复查无移动)
- worktree:`../webgis-wt-intelligent-carto-composer-20261107`
- commits:32eeb3992(W1 断链清偿)/ 972d47ddf(W2 组合语法)/ 8b12cc473(W3 一致性证据)/ 956e7da0a(W4 增量+黄金)/ 0aefaba57(a3 断言更新)/ e9a8f33af(质量门)
- PR URL:(创建后填)

## PR Body 要点(创建时使用)

问题:#1999/#2000/#2002/#2003 四条出版链断链(A4 导出不求解/求解标记污染默认导出/组件族锁失效/矢量指北针不随旋转)+ 判选输入两套口径 + 矢量图例丢单位 + 模板契约机读自相矛盾(168 处)。

实现:见各 commit message 与 planning/G04-CARTOGRAPHY-COMPOSER/{BASELINE,DECISIONS,QA_REPORT}.md;文档三份 docs/cartography/。

兼容性:默认导出字节稳定(#2000 断言);行为变化 5 项已在 QA_REPORT §5 披露;全部加性(新码入 closed vocab/新模块/新测试)。

证据:QA_REPORT.md §1 测试表(cartography 3333×2、前端 5003、typecheck/eslint/ruff/mypy 门)。

测试命令:QA_REPORT §1 逐行可复制。

风险:QA_REPORT §5-6(浏览器 E2E 未跑;A3 容量标定为工程判断;环境依赖隔离说明)。
