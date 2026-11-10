# G15 REVIEW — 独立评审与修复记录

## R1 独立评审（reviewer 未参与实现；全量 diff + 现场复跑/对抗复现）

结论：APPROVE-WITH-FIXES（3 blocker + 2 强烈建议 + 若干 P3）。

| # | 级 | 发现 | 修复 |
|---|---|---|---|
| 1 | P0 | 面板 frames patch 只带 4 键 → 共享产品整笔被 schema 拒绝；独立产品静默丢 data 绑定（测试 mock 了端点未拦截） | 投影携带 committed 原始槽（`raw`）；面板 patch 基底 = raw 逐字携带、只覆写编辑键；vitest 断言 data/时间窗存活 + 引擎级回归（丢 data 的 patch 被契约拒绝） |
| 2 | P1 | PDF 共享图例带读主文档局部分级 → 图例与地图色矛盾（fixture 同 breaks 掩盖） | `_shared_legend_band_html` 改为消费 resolver 盖章的共享图例（与 SVG 面同源）；fixture 局部/共享 breaks 故意分离 + PDF 文本层断言局部边界不出现 |
| 3 | P1 | 导出路由决策/产品画幅投影/同步组纯函数零消费者；vector-pdf 请求不能发 driver=product；客户端 PNG 实际产出 N 张无差异图且无披露 | (a) `publication-export.ts`：产品在场 → driver=product（类型面 + 检测接线）；(b) `exporter.ts`：产品画幅投影真实进 canvas 导出（where 过滤承载）+ `layer_filter_unsupported` 诊断披露限制；(c) 同步组运行时按评审允许的诚实降级处理：ADR D7/PR 明确「契约+纯函数已交付，交互视窗未接线」 |
| 4 | P2 | count-vs-rate 门在 bind 同名字段路径不可达；rate 无 denominator 通道（永远拒绝→静默降级）；测试丢弃真实混布结果 | semantics/unit/denominator 经工具面+服务贯通到 compute（rate 可声明分母走共享）；混布测试改写为诚实锚定（同名字段=同推导不 mismatch 是契约设计，声明矛盾拒绝有测）+ denominator 服务旅程 |
| 5 | P2 | 解绑/绑定第二步失败后保留的 layout.frames 并非惰性 —— 缺省导出逐帧分页（每页整幅无过滤）未披露 | 解绑回执 + 模块 docstring 如实披露分页副作用与清除路径 |
| 6 | P2 | `_apply_frame_overrides` 声称单一事实但存两份逐字副本 | publication_export 改薄别名转发 resolver |
| 7 | P3 | resolver：skipped 也记 applied；paint/色带重投影静默失败（图例与地图色漂移无诊断） | applied 区分 skipped；两处失败路径加 disclosures |
| 8 | P3 | disclosures 截断可吞共享拒绝通知 | 拒绝通知前置再截断 |
| 9 | P3 | 长 slug 截断可撞 frame_id（晚期 mutation 拒绝难懂） | plan 层去重 + 确定性哈希后缀 |
| 10 | P3 | HTML 槽 svg 缺 viewBox 回退 | 镜像 SVG 面 fallback |
| 11 | P3 | 测试 junk 表达式；能力图 planned 措辞；catalog `blues` 行疑漂移；bind 第二笔无 CAS | junk 删除；bind 产品写透传 expected_revision；`blues` 行经 base 版本再生成证实为**基线既有 generator drift**（非本 PR 引入，保留新输出） |

评审同时确认（原文）：工具→服务→CAS→handler 链路真实；共享分级并集单次计算无视口采样；
verification 快照≠live 且空 required 不可判 complete；未水合拒绝继承；版本阶梯/parity
golden additive；资源界真实；无 ChatEngine 复活；ADR-0288 唯一；i18n 双语对称。

## 修复后验证

- 后端：`tests/cartography/ tests/unit/gis_harness/ + mapspec lifecycle/store/export_frames/svg/model_library` → **6949 passed / 0 failed**
- 前端：`vitest run lib/ + 面板 + comparison + i18n` → **3305 passed**；`tsc --noEmit` 0 错误
- 真实工件复验：PDF 文本层含共享域标签、不含局部边界；像素级四画幅着墨

## R2 复审（reviewer 对 R1 修复的复审）

结论：APPROVE-WITH-FIXES —— R1 全部 11 项确认修复（两项经 reviewer 独立
复现验证）；发现 1 个 R1 修复引入的新 P0 + 2 个遗留：

| # | 级 | 发现 | 修复 |
|---|---|---|---|
| A | P0 | CAS superseded 回执（is_error=False）被当成功：bind/update/unbind 在过期 CAS 下回执 ok 而零写入/半写入；bind 第二笔复用调用方旧基线必 superseded | `_write_refusal`：is_error **或 superseded** → typed `mutation_superseded` 拒绝（三写面统一）；bind 第二笔 CAS 基线链式取 frames 写落定 revision；新增 3 个 CAS 语义测试（fresh 链式成功 / stale 拒绝不留空壳 / update+unbind stale 拒绝） |
| B | P2 | 产品 PDF 路由被 atlasEnabled 门挡住（默认关）→ 缺省路径仍产出 N 张无过滤页 | 检测提升到门之前：产品在场即 driver=product（图册开关无关）；非产品路径门语义逐字节保留 |
| C | P3 | 工件测试局部存储标签与共享标签同文（derive_legend_items 优先 labels）→ 判别力不全 | fixture 局部 labels 改为局部 breaks 文本（"20 – 26.5"）+ PDF 断言局部标签不出现 |
| D | P3 | semantics 词表无早校验（传 rate 会在 schema 晚期挂难懂错） | 工具面按 SHARED_EXPRESSION_SEMANTICS 早拒（invalid_semantics typed） |

修复后验证：后端 6925 passed / 0 failed（含 3 个新 CAS 测试）；前端 650
passed + tsc 0 错误。


## R3 终审（对 R2 修复的复审）

**APPROVE** —— R1/R2 全部发现经 reviewer 独立执行验证关闭（含新增的
「两笔写之间交错写入者」竞态复现：chained CAS 落 refused + 部分状态如实
披露；atlas 门 5 场景执行验证 —— 非产品路径逐字节保持）。遗留 2 项 P3
非阻塞：显式 category/feature 选择被产品覆写（本 PR 交付前已顺手修复：
仅 driver 未选/frames 时自动路由）；解绑后保留帧分页按诚实披露处置。

评审证据链：R1（APPROVE-WITH-FIXES，3 blocker）→ 修复 f8dfe6bad →
R2（APPROVE-WITH-FIXES，新 P0 CAS）→ 修复 db8d79357 → R3（APPROVE）。
