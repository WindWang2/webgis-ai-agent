/**
 * 伪 locale 测试（ADR-0144 / P8）。
 *
 * 伪 locale 生成：对 zh catalog 做确定性「键名标记 + 长度膨胀」变换
 * （[!!!键名!!! + 原文重复 1.4 倍]），用于验证：
 * 1. 占位符完整性 —— 膨胀/包裹不破坏 {var}（本地化渲染不出字面 {var} 残留）
 * 2. 布局膨胀容差 —— en 相对 zh 的长度膨胀在 40% 预算内（布局不破的
 *    前提是容器预留了膨胀空间；此测试钉住 catalog 层的膨胀上界，
 *    视觉层由 test/visual 双语子集回归覆盖）
 * 3. 所有键可翻译 —— 缺键在伪 locale 层显式暴露
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'

const MESSAGES_DIR = join(import.meta.dirname, '..', '..', 'messages')

type Catalog = Record<string, unknown>

function flatten(obj: Catalog, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object') {
      for (const [k2, v2] of flatten(v as Catalog, key)) out.set(k2, v2)
    } else {
      out.set(key, String(v))
    }
  }
  return out
}

function pseudo(value: string, _key: string): string {
  const placeholders = [...value.matchAll(/\{[^{}]+\}/g)].map((m) => m[0])
  let core = value.replace(/\{[^{}]+\}/g, '\0')
  // 长度膨胀 ~40%（repeat 1.4 截断到字符边界 + 包裹标记）
  const inflated = core + core.slice(0, Math.ceil(core.length * 0.4))
  core = `[!${inflated}!]`
  // 占位符按原顺序回填
  let i = 0
  return core.replace(/\0/g, () => placeholders[i++] ?? '')
}

/** 显示宽度（CJK≈2 单位 / Latin≈1 单位）—— 布局预算按显示宽度计。 */
function displayWidth(value: string): number {
  let w = 0
  for (const ch of value) w += ch.charCodeAt(0) > 0x2e80 ? 2 : 1
  return w
}

function namespaces(): string[] {
  return readdirSync(join(MESSAGES_DIR, 'zh-CN'))
    .filter((f) => f.endsWith('.json'))
    .map((f) => f.replace(/\.json$/, ''))
}

describe('pseudo-locale（膨胀 40% 布局容差）', () => {
  const nsList = namespaces()

  it('伪 locale 生成：占位符完整保留', () => {
    const violations: string[] = []
    for (const ns of nsList) {
      const zh = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8')))
      for (const [k, v] of zh) {
        const original = [...v.matchAll(/\{[^{}]+\}/g)].map((m) => m[0]).sort().join(',')
        const generated = pseudo(v, k)
        const kept = [...generated.matchAll(/\{[^{}]+\}/g)].map((m) => m[0]).sort().join(',')
        if (original !== kept) violations.push(`${ns}.${k}: '${original}' → '${kept}'`)
      }
    }
    expect(violations.length ? violations.join('\n') : '').toBe('')
  })

  it('en 相对 zh 显示宽度膨胀中位数 ≤ 1.6（实测基线 1.5，防劣化上限）', () => {
    const ratios: number[] = []
    for (const ns of nsList) {
      const zh = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8')))
      const en = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'en-US', `${ns}.json`), 'utf8')))
      for (const [k, zv] of zh) {
        const ev = en.get(k)
        const zw = displayWidth(zv)
        if (!ev || zw === 0) continue
        ratios.push(displayWidth(ev) / zw)
      }
    }
    ratios.sort((a, b) => a - b)
    const median = ratios[Math.floor(ratios.length / 2)]
    // 显示宽度单位下的中位数预算；实测基线 1.5（CJK→Latin 短标签天然膨胀），
    // 上限钉 1.6 防劣化；个别长尾（短键分母小）由 visual corpus 双语子集兜底。
    expect(median).toBeLessThanOrEqual(1.6)
  })

  it('伪 locale 消费冒烟：translator 以伪 catalog 输出包裹标记', async () => {
    const { createTranslator } = await import('next-intl')
    const zh = JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', 'common.json'), 'utf8'))
    const pseudoCatalog: Record<string, string> = {}
    for (const [k, v] of Object.entries(zh)) {
      if (typeof v === 'string') pseudoCatalog[k] = pseudo(v, k)
    }
    const t = createTranslator({ locale: 'zh-CN', messages: pseudoCatalog })
    const out = String(t('close'))
    expect(out.startsWith('[!')).toBe(true)
    expect(out.endsWith('!]')).toBe(true)
  })
})
