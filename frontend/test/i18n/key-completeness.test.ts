/**
 * 键完整性测试（ADR-0144 / P2）。
 *
 * 1. zh-CN 与 en-US 的 namespace 文件集合一致
 * 2. 两语言展平键集合 diff 为空（双向缺失都报）
 * 3. 每个键的 {placeholder} 占位符两语言一致（漏占位符会渲染出字面 {var}）
 * 4. messages.ts 已登记全部 namespace 文件（防止新文件忘记 import）
 * 5. catalog 值非空字符串
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { messages } from '@/lib/i18n/messages'
import { SUPPORTED_LOCALES } from '@/lib/i18n/config'

const MESSAGES_DIR = join(import.meta.dirname, '..', '..', 'messages')

function flatten(obj: unknown, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  if (obj === null || typeof obj !== 'object') return out
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object') {
      for (const [k2, v2] of flatten(v, key)) out.set(k2, v2)
    } else {
      out.set(key, String(v))
    }
  }
  return out
}

function namespacesOnDisk(locale: string): Set<string> {
  return new Set(
    readdirSync(join(MESSAGES_DIR, locale))
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''))
  )
}

function placeholders(value: string): Set<string> {
  return new Set([...value.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1]))
}

describe('i18n key completeness', () => {
  it('两语言 namespace 文件集合一致', () => {
    const [zh, en] = SUPPORTED_LOCALES
    const zhNs = namespacesOnDisk(zh)
    const enNs = namespacesOnDisk(en)
    expect([...enNs].filter((n) => !zhNs.has(n))).toEqual([])
    expect([...zhNs].filter((n) => !enNs.has(n))).toEqual([])
  })

  it('zh-CN 与 en-US 键集合 diff 为空', () => {
    const zhKeys = new Set<string>()
    const enKeys = new Set<string>()
    for (const ns of namespacesOnDisk('zh-CN')) {
      const zh = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8')))
      const en = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'en-US', `${ns}.json`), 'utf8')))
      for (const k of zh.keys()) zhKeys.add(`${ns}.${k}`)
      for (const k of en.keys()) enKeys.add(`${ns}.${k}`)
    }
    const missingInEn = [...zhKeys].filter((k) => !enKeys.has(k))
    const missingInZh = [...enKeys].filter((k) => !zhKeys.has(k))
    expect(
      missingInEn.length ? `en-US 缺键：\n  ${missingInEn.join('\n  ')}` : ''
    ).toBe('')
    expect(
      missingInZh.length ? `zh-CN 缺键：\n  ${missingInZh.join('\n  ')}` : ''
    ).toBe('')
  })

  it('每个键的 {placeholder} 两语言一致', () => {
    const violations: string[] = []
    for (const ns of namespacesOnDisk('zh-CN')) {
      const zh = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8')))
      const en = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'en-US', `${ns}.json`), 'utf8')))
      for (const [k, zhVal] of zh) {
        const enVal = en.get(k)
        if (enVal === undefined) continue
        const zhPh = placeholders(zhVal)
        const enPh = placeholders(enVal)
        const onlyZh = [...zhPh].filter((p) => !enPh.has(p))
        const onlyEn = [...enPh].filter((p) => !zhPh.has(p))
        if (onlyZh.length || onlyEn.length) {
          violations.push(`${ns}.${k}: zh 独有 [${onlyZh}] en 独有 [${onlyEn}]`)
        }
      }
    }
    expect(violations.length ? '占位符不一致：\n  ' + violations.join('\n  ') : '').toBe('')
  })

  it('catalog 值非空字符串', () => {
    const empty: string[] = []
    for (const locale of SUPPORTED_LOCALES) {
      for (const ns of namespacesOnDisk(locale)) {
        const flat = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, locale, `${ns}.json`), 'utf8')))
        for (const [k, v] of flat) {
          if (!v.trim()) empty.push(`${locale}/${ns}.${k}`)
        }
      }
    }
    expect(empty.length ? '空值键：\n  ' + empty.join('\n  ') : '').toBe('')
  })

  it('messages.ts 已登记全部 namespace（防新文件忘记 import）', () => {
    for (const locale of SUPPORTED_LOCALES) {
      const registered = new Set(Object.keys(messages[locale]))
      const onDisk = namespacesOnDisk(locale)
      expect([...onDisk].filter((n) => !registered.has(n))).toEqual([])
    }
  })

  it('zh 键数量达到抽取规模（防 catalog 被意外清空 / 守卫降级）', () => {
    // 下限随 P3 归零进度只升不降（P3 完成后应为 >800）。
    let count = 0
    for (const ns of namespacesOnDisk('zh-CN')) {
      count += flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8'))).size
    }
    expect(count).toBeGreaterThan(50)
  })
})
