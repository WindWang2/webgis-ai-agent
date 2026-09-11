# ADR-0145: 三个智能资产面板的信息架构与「诚实能力呈现」原则

日期：2026-09-12
状态：Accepted
线别：feat/knowledge-market-ui-v9（知识库 / 扩展市场 / ModelOps 三面 UI V9）

## 背景

三个「智能资产」子系统后端厚重、前端不可见：知识库 RAG 只有一个设置页文档计数
（专设面板是 #607 移除后的 `return null` 存根）；扩展市场后端有只读 HTTP 面
（browse/search/detail/download，写路径全在运维 CLI）而前端零调用；ModelOps
连一个 HTTP 路由都没有（能力全部以 agent 工具存在），且 #1212 指出模型不是
可检索的能力实体（Intent→capability→model 投影链断裂，模型经工具关键词发现）。

本 ADR 记录三面 UI 的信息架构决策，以及贯穿三面的「诚实能力呈现」原则 ——
即 #551/#607 诚实性决策在「能力可见化」方向上的延伸。

## 决策

### D1 替换存根而非另起挂载点；「有真实端点才有面板」

`rag-independent-panel.tsx` 保持 `{ open, onClose }` 挂载契约原样重写，
page.tsx / tweaks-panel 零改动。#607 的原则从「零生产者的面板必须移除」
升级为「**有真实端点才有面板**」：面板内每个数据区块都绑定一个真实端点调用，
后端缺失的能力（分块详情、multipart 上传、嵌入模型配置）一律诚实空态/提示，
不造数据、不放假按钮、不放死开关。

### D2 citation 注入走输入框草稿，不静默代发

chat API 的 `message` 是纯字符串，无结构化附件/annotations 字段。注入对话 =
把带 `[n]` 引用块（来源文档 + 分块 id + L2 分数 + 摘录）的文本经 zustand
`pendingChatInjection`（nonce 防重复）拼入聊天输入框草稿，由用户确认发送。
「静默代发」被否决：用户必须看到将要发出的内容。

citation 渲染做在共享件 `components/chat/citation.tsx`：定义块从正文剥离 →
文末「引用来源」列表；正文已知编号的 `[n]` 转 markdown 锚点链接
`[[n]](#cite-n)`，由 `components.a` 拦截渲染为角标 + 悬浮卡（键盘可达、
Escape 关闭、读屏语义）。不引 raw HTML / rehype-raw，不扩大 XSS 面。
story-markdown.tsx 经 `CitationArea` 消费（31 行 ≤ 契约 60 行）；chat 气泡
实际渲染器 mini-md.tsx 以 21 行最小改动复用同一共享件（任务书指定 story-markdown
为扩展点时的假设与实际渲染链不符 —— 此为已记录的适配，非行为偏离）。

零回归界定：无定义块的消息正文逐字节不变；非 citation 锚点行为不变；
story-markdown 链接消毒测试不回退。

### D3 分数语义原样呈现

知识库检索的 score 是 FAISS L2 距离（越小越相关）。UI 全链路（面板结果、
rag-config 分数分布条、citation 卡）一律标注「L2 距离（越小越相关）」，
分布条只做相对最差命中的线性比例，**不归一化为相似度百分比**。

### D4 扩展市场只读诚实化

HTTP 面只有 browse/search/detail/download。UI 提供：列表/搜索/标签过滤、
详情（版本历史、权限声明、依赖、digest/fingerprint/sbom_digest/签名 key）、
鉴权下载。安装/启用/停用/卸载与已安装列表只有运维 CLI —— 以固定说明条
代替假安装按钮；certification/trust store/SBOM 内容无端点 → 不渲染徽章；
列表 404（EXTENSION_REGISTRY_DIR 未配置）渲染为「市场未启用」空态而非错误。

### D5 ModelOps 经 executeToolDirect 驱动；运行历史限于本会话

ModelOps 无专用 HTTP 路由。面板经 `POST /api/v1/chat/tools/execute` 调用
`modelops_list_models` / `modelops_inspect_model` / `modelops_model_history`
（不改后端拿到真实注册表数据）。运行历史：后端无持久化 run 列表（run_id 仅
cancel 可用）→ 面板只显示本会话 chat 工具事件观察到的 run（use-modelops-runs），
空态明示「刷新后不保留（协调点）」。

#1212 诚实呈现：面板固定说明「模型经工具关键词发现」；descriptor.provenance
自由 dict 原文展示；不推导能力覆盖结论、不渲染「能力百分比」类美化。

### D6 侧栏注册 append-only

`LeftTab` union 追加 `'market' | 'modelops'`；`RAIL_GROUPS` 末尾追加一组
（Store=市场、Boxes=ModelOps）；`MODE_TABS` 三模式尾部追加两 tab；不重排
任何既有组/顺序（空间记忆不变式）。tool-call-card 中 modelops run 的跳转
链接为最小识别逻辑（29 行 ≤ 契约 30 行）。

## 后果

- 后端缺口成为显式协调点（见 PR《后端缺口协调清单》），任何缺口补齐后
  UI 只需把空态换成数据绑定，不需要结构变更。
- 无 msw 的适配：任务书指定 msw fixtures，但仓库既无该依赖也无 handlers
  目录；按仓库既有 fixture 模式（`vi.stubGlobal(fetch)` + Playwright 路由
  拦截）等效实现，避免 10 线并发下的 lockfile 冲突。
- 已知限制：chat 气泡的 citation 角标只在消息内出现 `[n]` 编号且定义块在
  同一条消息时生效（跨消息引用无数据源 —— 后端无引用注册表）。
- 嵌入模型名称是后端代码常量（faiss_store.py `_EMBEDDING_MODEL_NAME`），
  UI 刻意不硬编码该名（会漂移），仅提示「后端固定，无读写端点」。

## 参考

- #551 / #607：诚实性原则先例（假控件移除 / 零生产者面板移除）
- #1212：Model 能力实体投影链断裂（Harness V8 已做能力图；本线为 UI 侧诚实呈现）
- frontend/docs/knowledge-market-recon.md：三族端点契约表与缺口清单
