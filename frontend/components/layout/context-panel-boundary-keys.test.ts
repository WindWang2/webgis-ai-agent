/**
 * #1727 —— PanelErrorBoundary label 键的「消费方存在性」守卫。
 *
 * 背景：useT('layout') 会拼 `layout.` 前缀（lib/i18n/useT.ts:23），所以
 * ContextPanel 里必须传 `panel.boundary.X`。此前 18 处写错：
 *   - 9 处传已带命名空间的 `layout.panel.boundary.X` → 解析
 *     `layout.layout.panel.boundary.X`（双重前缀）；
 *   - 9 处用模板串 `panel.boundary.$X`（`$X` 是字面量而非插值）→ 解析
 *     `layout.panel.boundary.$X`。
 * 两者都解析不到 catalog，而 translator 的 onError 静默吞掉
 * MISSING_MESSAGE、fallback 返回键路径本身（lib/i18n/translator.ts:25-37），
 * 于是面板崩溃时用户看到「layout.layout.panel.boundary.chat面板遇到错误」
 * 这种键路径乱码。
 *
 * 守卫缺口：test/i18n/key-completeness.test.ts 只校验 zh↔en 两侧对称，
 * 从不校验「消费方引用的键真的存在」——写错的键在两侧同时缺失，对称性
 * 检查永远发现不了。本文件补上消费方视角：直接扫 ContextPanel 源码里
 * 引用的 boundary 键，逐个回查 layout catalog。
 *
 * 刻意只 import 两个语言的 layout.json（不 import 聚合的
 * lib/i18n/messages.ts）：本守卫只关心 layout 一个 namespace，不该被其它
 * namespace 的 catalog 状态连带挂掉。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import zhLayout from '@/messages/zh-CN/layout.json'
import enLayout from '@/messages/en-US/layout.json'
import { SUPPORTED_LOCALES, type AppLocale } from '@/lib/i18n/config'

const CATALOGS: Record<AppLocale, unknown> = {
  'zh-CN': zhLayout,
  'en-US': enLayout,
}

/** 点路径取 catalog 值；任一段缺失或类型不对即 undefined。 */
function resolve(catalog: unknown, key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>(
      (acc, part) =>
        acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined,
      catalog,
    )
}

const SOURCE = readFileSync(join(import.meta.dirname, 'context-panel.tsx'), 'utf8')

/** 源码里以字面量形式引用的 boundary 键（去命名空间前缀后的形态）。 */
const REFERENCED_KEYS: string[] = [
  ...new Set(
    [...SOURCE.matchAll(/t\('(panel\.boundary\.[a-z_]+)'\)/g)].map((m) => m[1] as string),
  ),
]

describe('ContextPanel boundary label 键（#1727）', () => {
  it('不再有双重前缀 / $ 笔误写法的 boundary 键', () => {
    expect(SOURCE).not.toMatch(/t\('layout\.panel\.boundary\./)
    expect(SOURCE).not.toMatch(/panel\.boundary\.\$/)
  })

  it('引用面覆盖全部 tab（防新增 tab 漏配 boundary）', () => {
    // PANEL_META 注册的 13 个 tab 各有一条 i18n boundary 键；
    // 唯一例外是 modelops（直接用字面量 label="ModelOps"）。
    expect(REFERENCED_KEYS.length).toBeGreaterThanOrEqual(13)
  })

  it('消费方引用的每个 boundary 键在 zh-CN / en-US catalog 中都存在且非空', () => {
    const missing: string[] = []
    for (const locale of SUPPORTED_LOCALES) {
      for (const key of REFERENCED_KEYS) {
        const value = resolve(CATALOGS[locale], key)
        if (typeof value !== 'string' || !value.trim()) missing.push(`${locale}: layout.${key}`)
      }
    }
    expect(missing).toEqual([])
  })

  it('解析出的 label 不是键路径本身（fallback 乱码回归）', () => {
    for (const locale of SUPPORTED_LOCALES) {
      for (const key of REFERENCED_KEYS) {
        const value = resolve(CATALOGS[locale], key) as string
        expect(value).not.toContain('panel.boundary.')
        expect(value).not.toContain('$')
      }
    }
  })
})
