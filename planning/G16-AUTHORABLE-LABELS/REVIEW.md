# REVIEW — G16-AUTHORABLE-LABELS

## R1（独立 reviewer，未参与实现）

- Reviewer: 独立 general-purpose subagent（全量 diff 69 文件 +7290/-153，真实执行测试与复现）。
- 结论：**Not ready** — 2×P1 + 10×P2 + 9×P3。关键发现：
  - P1-1 live 数值身份永不匹配（MapLibre match 严格类型相等；`to-string` 缺失）
  - P1-2 自身墨迹豁免在真实接线死亡（障碍层 id 原始 vs 标签层 id 合成 `-label` 后缀不同键）
  - P2：不可见层误报 orphan；pinned/callout ghost 子层；非 pin 偏移确定性导出被丢弃；hidden+pinned 矛盾双端口径相反；labels_config 白名单可被工具直调绕过；TS 缺披露层；Esc 拖动取消仍提交；键盘微调双计；>3 解析键静默丢
  - P3：inspect unverified 不可达；身份探测只扫前 50；形状拒绝消息空；zone 环非法点双端口径分叉；等
- 正面确认：调用链真实（除豁免）、双孪生一致、reset/原子/CAS/溯源/上限全过、legacy 逐字节（实测）、XSS 无、监听清理正确。

## R1 修复（commit bf47d432c）

全部 P1/P2 + P3-13/15/16/17/20/22 修复，逐项回归（真实 style-spec 求值器对照、真实接线 e2e、
引擎直调白名单、UI Esc/双计）。见 commit message 全清单。
有意不改（PR 披露为限制）：origin 保留首作者（溯源选择）；live callout 仅 Point 锚；
miss 点击 toast；拖拽 px→em 用 spec label.size（em 语义保持成比例）。

## R2（同一 reviewer 复审，@ bf47d432c）

- 结论：**Ready for PR** — 0 blocking。两个 P1 在真实接线层验证关闭
  （求值器级回归 + wired e2e）；10×P2 全部修复并复核；全量套件除两条
  已文档化基线外全绿；legacy 逐字节再次实测。
- 遗留（reviewer 判不阻塞）：1×P2 残留（unprojectable 披露未接 runtime
  事件）+ 4×P3（非 pin callout 起点=位移锚点、P2-11 测试未覆盖真实 bug
  路径、TS 披露无测试、Esc 后预览复活）。
- **R2 follow-up 全部修复于最终 commit**（F1 披露事件、F2 callout 起点=
  原始锚点双端 + 回归、F3 真实路径测试 + 跨重挂累计、F4 TS 披露测试、
  F5 Esc 清 startPx）。有意保留（PR 已披露）：origin 首作者、live
  callout 仅 Point 锚、miss toast、拖拽字号口径。
