# ADR-0147: 前端交互深度设施——命令注册框架、StoryMap 章节模型与检索范围决策

- 状态: Accepted
- 日期: 2026-09-12
- 线: feat/ux-depth-v9（交互深度与专业用户体验 V9，纯前端线）
- 关联: #1213（vector-pdf 死接口评估，见 §4）、V7/V8 workbench 系（undo/opsLog 基线）、#552（Story 分享回放契约）

## 1. 背景

专业 GIS 用户与演示场景缺一层「直接操作面」：10 个 rail tab 与数十个动作没有统一键盘入口；data-fabric
query 能力强但只能经 chat prompt 触达；/story 只回放地图（chart/表产物被丢弃）且只有线性播放；跨会话
transcripts 不可检索；undo 有全局键无可见层；新用户无引导。

P0 勘察结论（frontend/docs/ux-depth-recon.md）锁定了三个契约事实，直接构成本 ADR 的决策输入：

1. `use-keyboard-shortcut.ts` 是固定三键 hook 而非注册表，Ctrl+K / `?` 全仓空闲；
2. 后端 `GET /chat/sessions` 无全文检索参数，transcript 全文只能逐会话拉取；
3. `POST /api/v1/export/vector-pdf` 是**单 MapSpec 出版引擎**，无 chart/table 帧位。

## 2. 决策一：命令注册框架（append-only 外部 store）

- 落点 `frontend/lib/commands/`：纯数据注册表（Map + version + listener），
  React 侧经 `useSyncExternalStore` 订阅，模式同 `lib/workbench/undo.ts`（不进 zustand 防循环依赖）。
- **append-only 契约**：`registerCommands` 返回反注册函数；重复 id 后注册者被忽略并 dev 告警——
  多线（D–H）并发接入互不覆盖。React 便捷入口 `useRegisterCommands(defs, deps)` 供各组件在
  挂载期贡献命令（依赖上下文的命令随组件生命周期注册/反注册）。
- **shortcut 字段是描述性元数据**：仅用于总览展示与冲突检测；注册表不自动派发快捷键，实际按键绑定
  由命令方自行挂 listener（如 `edit.undo` 复用 use-undo.ts 既有全局键、`panel.palette` 由
  `useCommandPaletteHotkeys` 实绑）。这条规则防止同一键双触发（undo 是最危险的复分子）。
- 模糊搜索：预建索引（title/keywords/group 预 lowers）+ 子序列评分，5000 命令单查询 <16ms 有测试断言。
- 面板为 APG combobox（input role=combobox + aria-activedescendant + aria-live 播报），焦点圈闭/
  焦点归还复用 `useDialogFocus`；最近使用（LRU 8，localStorage）与参数化命令（paramSpec，回车进入
  参数收集）均支持。
- 为什么不改 use-keyboard-shortcut.ts：它是共享 lib/hooks 面（虽未被生产消费），改注册表化会扩大
  冲突面；新建 `lib/commands/` 是零风险等价物。

## 3. 决策二：StoryMap 章节模型（派生 + 编排覆盖层）

- 章节默认从会话消息派生（1 消息 = 1 章节，与既有线性播放器同序，#552 行为全部保留）；
  用户编排（重排/重命名/隐藏）以**覆盖层**持久化到 localStorage（key 含 sessionId），不篡改会话
  真相源（后端 transcripts）。兼容读取：编排引用的章节 id 越界/缺失按派生序兜底；**回滚面即覆盖层
  ——删除编排键即回默认视图**（对齐任务书 §7 风险条款）。
- 章节产物（chart/表回放）：ref 归属规则 = 消息文本提及的 `ref:*` 归该章节；会话终态 mapstate 中
  剩余的 ref 追加到最后章节作产物附录（深走 JSON 值扫描，抗 mapspec schema 漂移）。数据通道复用
  mapspec 图表面板同款 `loadChartArtifact` / `loadTableArtifact`（缓存 + in-flight 去重 + 会话
  cursor 鉴权），加载失败渲染降级卡片。
- 章节相机：从消息内容的 ```json 围栏解析 `fly_to`（解析失败一律 null，保持当前相机，不猜）；
  切章触发 fly_to + 容器透明度过渡；**reduced-motion 降级为跳切**（`usePrefersReducedMotion`
  JS 门控，CSS 侧由 globals.css media query 兜底——双轨约定）。
- 48 章节长叙事：派生/编排/相机/产物归属纯逻辑层 <16ms 有测试断言，渲染以 48 节点全出现为门禁。

## 4. 决策三：叙事 PDF 不消费 vector-pdf（#1213 评估结论）

`/api/v1/export/vector-pdf`（app/api/routes/map.py:296-373，ADR-0120 W8）是「MapSpec → 出版级地图
PDF」引擎（WeasyPrint、spec 级帧、429/503 typed）。评估结论：**StoryMap 叙事 PDF 不消费该端点**：

1. 叙事文档是异构章节（地图 + chart + 表格 + 文本），端点只有 spec 级地图帧位，chart/table 章节无契约位；
2. ref 载体源须调用方内联（安全决策 R2-M3/M8），逐章内联会话 ref 产物成本高且 429/503 失败面扩大到分享场景；
3. 既有栅格多页链 `exportToPDF(canvas, ..., {pages})` 已满足需求（零后端改动，本线禁改后端）。

**#1213 缺口本身继续开放**：其修法（exporter 增加矢量入口或标记 backend-only）属于制图面（Map Studio
单图出版），与本线叙事导出正交——留给后续制图线，并在 PR 协调点重申。本线分享卡/叙事 PDF 全部走既有
exporter 能力（`captureMapCanvas` + `composeShareCard` 本地合成 + `exportToPDF pages`）。

## 5. 决策四：跨会话检索 = client 侧本地索引（后端缺口显式化）

- 契约事实：后端 sessions 列表无 q= 参数、无消息内容字段；全文只能逐会话拉取。
- 决策：`lib/search/session-index.ts` 建立本地持久化索引（最近 20 会话 LRU + 1MB 序列化预算 +
  单会话 200 docs/单 doc 2000 字符上限），打开面板时按需补索引最近会话（顺序、可取消、进度可见）。
- 边界诚实披露：面板状态条明示「本地索引：N 会话 / M 条消息（最近 20 会话，LRU 上限）」；
  搜索范围仅覆盖已索引会话，不做伪装的全量承诺。
- 协调点：后端全文检索端点（sessions q= 或独立 search API）就位后，本模块可平滑替换为该端点的
  缓存层；接口已按 `SessionMeta / fetcher / buildIndexFromSessions` 分层，替换面收敛在一个模块。

## 6. 其他约束落点

- **undo 只增不改**：`lib/workbench/undo.ts` 仅新增只读访问器（`getUndoStack`/`getRedoStack`/
  `getNextUndoRedoLabels`），栈语义、`undo()/redo()`、journal 路径零改动。时间线/按图层视图与
  「回退到此处」（连续 `undo()` 至目标深度）全部走既有 API；Ctrl+Z 可见反馈经 opsLog journal
  头部驱动（undo/redo 本就会 journal），不重绑全局键。
- **hud store 零改动**：所有新开关（palette/查询控制台/搜索抽屉/操作历史/onboarding）均为独立
  zustand store 或组件态，不触碰 `useHudStore` partialize 白名单与 rail 注册语义。
- **query 控制台危险守卫**：`POST /data-fabric/catalog/{id}/query` 为只读检索端点（QuerySpec 无写
  语义），前端在发送前拦截写类动词（注释剥离后词边界匹配）与多语句，并诚实披露拦截原因；这不是
  沙箱，是前置校验。
- **测试基建**：仓库未引入 msw（P0 勘察 §5），本线遵循既有网络 mock 惯例（模块级 vi.mock +
  fixtures），未新增依赖。

## 7. 后果

- D–H 线获得公共命令注册框架（接入方式：组件内 `useRegisterCommands`，API 契约见
  `lib/commands/types.ts` 注释与 PR 说明）。
- StoryMap 数据模型扩展对旧会话向后兼容（派生模型 + 覆盖层），回滚零成本。
- #1213 的前端消费缺口仍开放（协调点重申）；后端检索缺口仍开放（协调点重申）。
- 已知让步：消息定位依赖 chat-tab 内的轻量锚点接线（append-only，约 20 行）；story 相机仅取自
  fly_to 围栏，无 fly_to 的章节保持当前相机（诚实而非猜测）。
