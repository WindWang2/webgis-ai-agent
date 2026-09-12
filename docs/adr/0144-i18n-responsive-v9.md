# ADR-0144: i18n 策略与响应式三档布局（foundation/i18n-responsive-v9）

状态：Accepted ｜ 日期：2026-09-12 ｜ 线：foundation/i18n-responsive-v9 ｜ 关联：#999、#1237

## 背景

约千级硬编码中文字符串散布于 127 组件 + 2 页面（P0 勘察：非测试源码 272 文件 /
7,426 CJK 行，其中 JSX 位置用户可见文案 938 处）；`<html lang="zh-CN">` 固定；
后端错误文案与信封 message 均为中文单语；布局为桌面像素常量壳（无宽度断点，
≤768px 地图不可用）。每拖一轮，并行 UI 线都会堆积新硬编码。

## 决策

### D1 i18n 框架：next-intl 作 ICU 引擎，上下文自管（无 i18n routing）

- 本应用是**单 URL 工作台**：语言是用户设置而非路由属性。不采用 `[locale]`
  路由段，不采用 `NextIntlClientProvider` 的路由耦合形态。
- 取 next-intl 的 `createTranslator`（ICU 消息格式：`{var}` 占位、复数/选择）
  作为唯一消息引擎；React 上下文由 `lib/i18n/i18n-provider.tsx` 自管
  （zustand 语言 store 驱动），语言切换 = store 更新 → 整树换 translator，
  无路由、无 `router.refresh()`、无整页重载。
- 无 provider 时（单测裸渲染）回落 zh 默认 translator —— 既有中文断言测试
  零改造，抽取过程不产生测试大爆炸。

### D2 无闪白链路（cookie 为 SSR 真相，localStorage 为意图副本）

```
setLocale() ─┬→ zustand persist（localStorage 'geoagent-locale'）
             ├→ document.cookie（'geoagent-locale'，SSR 唯一真相）
             └→ <html lang> 同步
刷新 → layout.tsx 读 cookie → SSR 首帧即正确语言
     → pre-paint 内联脚本写 window.__GEOAGENT_LOCALE__（同一 cookie）
     → store 首帧读全局变量 → hydration 首帧与 SSR 一致（零 mismatch）
     → mount 后 rehydrate localStorage：若与 cookie 分歧（cookie 被浏览器
       策略清除）以 localStorage 为准（post-mount 合法更新，单次闪退回）
```

### D3 catalog 结构与守卫

- `frontend/messages/{zh-CN,en-US}/{namespace}.json`（namespace：common/
  layout/chat/sidebar/map/settings/drawers/story/tweaks/errors）。
- 双语言同批登记；键集合 diff、`{placeholder}` 一致性、空值、namespace 登记
  由 `test/i18n/key-completeness.test.ts` 强制。
- no-raw-cjk 守卫（`test/i18n/no-raw-cjk.test.ts` + AST 扫描器
  `scripts/i18n/scan-lib.mjs`）：components/app/lib 的 JSX 位置裸 CJK 即 CI 红；
  白名单显式配额 + 「配额 ≠ 实际即红」反陈旧规则，倒逼逐批归零。
  v9 收口白名单见 `test/i18n/no-raw-cjk.whitelist.json`（起始 82 文件 → 收口 18 文件），
  归零计划：v9.1 agent/explorer/hud/shared/table 域；v9.2 lib 逻辑层扩面
  （canvas 导出文案、toast 消息的字符串字面量扫描）。

### D4 双轨文案职责（前后端错误文案）

**code/category 为准，message 为辅助。**

- 结构化字段（`code`/`category`/`retryable`/`degraded`）永不本地化、永不改动。
- 后端：Accept-Language 协商（`app/core/i18n.py`，RFC7231 宽松解析，
  不支持的主子标签跳过而非兜底）→ contextvar → 信封构造（exception.py
  分类块）本地化 `message`；catalog `app/locales/{zh_CN,en_US}.json` 以
  `ErrorCategory.value` 为键，zh 逐条镜像 `CATEGORY_DEFAULTS`（测试钉死）。
  回退链：locale → zh_CN → 分类学默认短语。
- 前端：`errors` namespace 按 category 键本地化（结构化驱动，不解析文案）。
- 本地化只作用展示层；异常原文（含 langchain/内部错误串）永不进 message
  （继承分类学防泄露红线）。

### D5 响应式三档布局

| 档 | 视口 | 形态 |
|---|---|---|
| desktop | >1180px | 现状壳：左栏 dock + 拖拽调宽（280–420）+ 地图 inset 推挤 |
| thin | 769–1180px | 面板覆盖模式：不推挤地图（inset 0），拖拽调宽禁用 |
| mobile | ≤768px | NavRail 折叠为底部横栏（横向 tablist + 安全区）；面板 BottomSheet 化（两档吸附 55%/92%、下拉关闭、焦点圈闭）；地图全屏优先 |

- 断点唯一真相：`lib/hooks/use-layout-mode.ts`（模块级 matchMedia +
  useSyncExternalStore；SSR 恒 desktop，hydration 后收敛；`<html
  data-layout-mode>` 标记供 e2e/visual 断言）。
- 768 沿用既有 `LEFT_PANEL_MIN_VIEWPORT_PX` 像素语义；新增 1180 为
  「面板(280)+地图可用(≈700)」挤压下限。
- 手柄/命中区：`@media (pointer: coarse)` 下 `.touch-target` 提升到 44px
  （审计与归零计划：`frontend/docs/touch-audit.md`）。

### D6 语言默认与回滚

默认语言 zh-CN；不切换语言的行为与 master 等价（catalog zh 值即抽取前的
原文案）。回滚面 = revert 本分支；语言数据（cookie/localStorage）无 schema
迁移、可弃。

## 后果

- 并行 UI 线（D/E/F/H/J）新组件直接 `useT()`（PR #1237 公告）；不再堆积新硬编码。
- `layout.tsx` 读 cookie → 根路由动态渲染（原静态 prerender）。自托管部署
  无 CDN 缓存面，影响可忽略；若未来上 CDN 需为 `/` 配置 cookie 变体缓存。
- 守卫 v1 不覆盖 lib 非对象字符串（canvas/toast 字面量）—— 扩面计划见 D3。
- 语言资源（cookie/localStorage）新增两键：`geoagent-locale`（cookie 与
  localStorage 同名）。

## 证据

- 前端：`test/i18n/`（框架 14 + 守卫 2 + 键完整性 6 + 伪 locale 3）、
  `test/layout-responsive.test.tsx`（9）。
- 后端：`tests/unit/core/test_i18n_errors.py`（19，含 TestClient 信封集成）。
- 勘察底稿：`frontend/docs/i18n-responsive-recon.md`；触控审计：
  `frontend/docs/touch-audit.md`。
