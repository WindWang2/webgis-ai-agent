# ADR-0288：证据化地图简报 —— 块级证据绑定与私有可追溯交付（G11）

- 状态：Accepted（G11-EVIDENCE-MAP-BRIEFS）
- 日期：2026-11-09
- 关联：ADR-0196（StoryMap 编排）、ADR-0275（D6 会话资产）、
  ADR-0226（durable runtime）、ADR-0284（类型化产物封套）、
  evidence_claim 体系（Direction 03）

## Context

StoryMap（ADR-0196）已能把证据链编译成叙事章节并打包离线 HTML，但它是
"一次性投影"：章节正文是编译期文本，数值没有结构化身份，用户不能编辑、
锁定或重生成；没有发布预检与不可变版本；分享不存在；"这个 20% 怎么来的"
无法机器对账。同时报告域（reports）有分享 token 先例、运行时域有 publish
freeze/CAS 先例、证据域有 positive-proof claim 验证器 —— 缺的是把它们
串成**简报域**的胶合层，而不是第二事实库。

## Decision

1. **MapBriefSpec 继承 StoryMapSpec**（additive，extra=allow round-trip），
   新增三层：`blocks`（编辑最小单元：稳定 ID + origin(auto|manual) +
   locked）、`facts`（结构化数值事实：唯一可 verified 的数值载体，
   EvidenceBinding 绑 artifact_ref + field_path + 用户可读来源口径 +
   对账指纹）、`provenance`（会话/产物/MapSpec 指纹）。
2. **比例/增长强校验**：ratio/percentage/share/rate 必须有
   denominator_fact_id，change 必须有 baseline_fact_id；verified 必须有
   产物绑定或可复算依赖链 —— 不凑数、不盖章。
3. **确定性事实核验**（brief_factcheck）：值定位（field_path 解析产物
   载荷）+ 派生复算 + 单位一致 + 新鲜度，positive-proof 纪律：无正向
   证据 → unverified（失败封闭）；矛盾 → unverified+contradicted（预检
   阻断）。LLM/stub 只组织解释文字，不产生权威数值。
4. **编辑与重生成**：乐观 revision（expected_revision → 409）；locked
   块内容不可改；merge_regenerated 永不覆盖 manual/locked，删除的块不
   复活；产物更新只标 stale（compute_stale），不改内容。
5. **预检三态**：pass/fail/not_evaluated × 7 检查项 → deliverable /
   draft_exportable / blocked；强事实错误、跨 owner、必需图件缺失阻断
   冻结；草稿可显式导出（封面/manifest 标 draft，不冒充正式发布）。
6. **不可变 release**：CAS 内容寻址（content_sha256 幂等键）+ manifest
   （files/evidence/指纹/版本 pin）；brief 行删除后 release 保留（SET NULL
   + conversation 冗余归属）。
7. **离线交付**：单文件自解压 HTML（零外链；metric 卡带 status 徽标与
   来源口径；SVG 静态图净化内联；缺图诚实告警）+ facts.csv（公式注入
   防护）+ JSON manifest 对账；release_meta 白名单投影，秘密不进交付物。
8. **私有分享**：沿 report.py 先例（share_code/ttl/撤销/限流/公共投影
   CSP sandbox）；token 只授予该 release 只读素材。显式决策：公开分享
   面交付的是冻结简报**本体**（单一不可变工件），其中 provenance 的
   session 标识/产物 ref 属非秘密内部对账指纹（单独持有不可读取任何
   资源，跨 owner 访问统一 404）；元数据端点的公共投影不携带这些字段。
9. **工具面**：brief_create_draft / brief_inspect_evidence /
   brief_update_block / brief_preflight / brief_freeze_release /
   brief_export（session_id 注入探针；回执紧凑；导出仅私有地址）。

## Consequences

- StoryMap v1.1 消费者读简报 dict 时未知键保留（数据保全），旧端点无回归。
- 简报数值的"已核验"只由 deterministic 管线授予；文字里的数字永远不可
  自动升级为已核验。
- 离线 HTML 无交互地图（诚实告警占位）—— 交互留在工作台。
- 后续扩展（LLM 文案模式、ZIP 附件包、印刷 PDF）在此契约上 additive。
