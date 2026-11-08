# G04-CARTOGRAPHY-COMPOSER REVIEW(独立冷读 + 清偿)

## 评审人:子代理 B(独立冷读,未参与实现)· 2026-11-08

**总评:APPROVE-WITH-FIXES**(16 项声明逐条核验属实;无 P0)。评审原文要点与清偿状态:

| 发现 | 级别 | 摘要 | 清偿 |
|---|---|---|---|
| P1-1 | P1 | 前端抑制不豁免浮动件(与后端矢量链分歧) | ✅ `!floating` 补入抑制条件 + 新增「浮动件在 suppressed 中保持在场」测试 |
| P1-2 | P1 | byte-stability 声明范围失实 + 新语义无字节级锁定 | ✅ QA §5.1 措辞如实化(承认语义反转而非"意图保持");新增 `test_nonhistorical_anchor_default_export_semantics_pinned`(字节级);PLAN 契约加成立条件 |
| P2-1 | P2 | 求解(已提交)与应用(live 合成)TOCTOU | 📋 记录为后续增强(回执携带求解时点指纹);QA §6 已披露 |
| P2-2 | P2 | A3 容量魔数 + 注释伪推导 | ✅ 注释改"审定启发式(宽度档位+预算+1)";新增 `test_a3_profiles_pinned`(改值即红) |
| P2-3 | P2 | 空组件面宣称 evaluated | ✅ 空 components → not_evaluated(reason="no_components") |
| P2-4 | P2 | planner statistic 变化未披露 + point_count float 归零 | ✅ float 宽容 coerce(24.0→24/profile);QA §5.3b 补披露 |
| P2-5 | P2 | 数值编码分类型字段残余误分级未文档化 | ✅ 语法文档 §1 粗判边界补注(逃生通道 profile.dataKind) |
| P3-1 | P3 | 文档数字 3 处不实(18→17 词表;646 口径;168 不可复现) | ✅ 全部修正(646=golden 链单次运行总数;168→"35 模板/326 槽中 204 个 required 槽") |
| P3-2 | P3 | 克隆残留死标记 constraint_layout | ✅ 删除写入;测试更新为断言缺席 + page_profile 在场 |
| P3-3 | P3 | 死分支(raster)/装饰参数/软断言 | ✅ 死分支删除;参数化移除;软断言改严格(success/message/hint 三断言) |
| P3-4 | P3 | 单位行降部字母 1px 压线 | ✅ unit_dy 13→15 |
| P3-5 | P3 | `_zone()` 画布型兜底 bottom-right(既有怪癖) | 📋 记录不修(master 既有,非本 PR 引入;改它影响求解容量语义需独立评审)。**复审附注**:本 PR 的 export 打印档求解给了它新激活面 —— 会话内 map_border/graticule 会作为 bottom-right 参与者占 2 个槽位容量(当前低风险:multiplier≥2/budget 4-5;后续处理时须一并评估) |

**P2-1/P3-5 为记录项**(明确后续 wave/独立处理),其余全部清偿。评审确认的安全面:session_id 注入非攻击面(schema 剥离+服务端注入);无凭据/无危险操作/无文件网络副作用。

## 评审实证(评审人本地)

后端 9 套件 206 项绿、前端 16 项绿、ruff 零告警、golden 零改动(无"改黄金让错误消失")。


## 复审(2026-11-08,同评审人)

**结论:APPROVE(可合并)** — P1×2/P2-2/3/4/P3-2/3/4 逐条实核为真修;P2-1/P3-5 记账获同意(P3-5 附激活面条件,已并入上表)。复审提出的 3 项非阻断小项已清偿:①GRAMMAR §1 粗判边界补注落地(初版补丁因 replace 目标不匹配静默失败,复审逮住,已修正)②两个微测(no_components/float coerce)落地 ③PARITY 646 措辞构成化。复审实证:后端 118 项 + 前端 17 项绿、ruff 零告警、A3 数值未偷改。
