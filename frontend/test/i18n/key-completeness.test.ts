/**
 * 键完整性测试（ADR-0144 / P2）。
 *
 * 1. zh-CN 与 en-US 的 namespace 文件集合一致
 * 2. 两语言展平键集合 diff 为空（双向缺失都报）
 * 3. 每个键的 {placeholder} 占位符两语言一致（漏占位符会渲染出字面 {var}）
 * 4. messages.ts 已登记全部 namespace 文件（防止新文件忘记 import）
 * 5. catalog 值非空字符串
 * 6. 代码里 t()/useT() 引用的键 ⊆ catalog（#1748：此前缺键无任何闸）
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { messages } from '@/lib/i18n/messages'
import { SUPPORTED_LOCALES } from '@/lib/i18n/config'
import { collectKeyReferences } from './key-refs.mjs'

const ROOT = join(import.meta.dirname, '..', '..')
const MESSAGES_DIR = join(ROOT, 'messages')

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
    // 2026-10-20 实测：zh-CN catalog 3047 键（28 个 namespace 展平统计）。
    // 旧下限 50 只占 1.6% —— 删掉 98% 文案该守卫依然绿，#1748 正是拿它
    // 当「形同虚设」的证据。新下限 2500（≈ 实际的 82%）：日常增删文案
    // 不会踩线，但任何规模的 catalog 丢失/清空立刻红。
    let count = 0
    for (const ns of namespacesOnDisk('zh-CN')) {
      count += flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'zh-CN', `${ns}.json`), 'utf8'))).size
    }
    expect(count).toBeGreaterThan(2500)
  })

  it('F-13: en-US 值不含 CJK（#1436 未译副本回归闸）', () => {
    // 刻意双语的条目（语言切换标签）列入白名单；新增需说明理由。
    const ALLOW = new Set(['settings.system.language'])
    const offenders: string[] = []
    for (const ns of namespacesOnDisk('en-US')) {
      const flat = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, 'en-US', `${ns}.json`), 'utf8')))
      for (const [k, v] of flat) {
        if (/[\u4e00-\u9fff]/.test(v) && !ALLOW.has(`${ns}.${k}`)) offenders.push(`${ns}.${k} = ${v}`)
      }
    }
    expect(offenders.length ? `en-US 含中文（${offenders.length}）：\n  ` + offenders.slice(0, 20).join('\n  ') : '').toBe('')
  })
})

/** zh-CN catalog 全键集合（`<namespace>.<子路径>`）。 */
function catalogKeys(locale: string): Set<string> {
  const keys = new Set<string>()
  for (const ns of namespacesOnDisk(locale)) {
    const flat = flatten(JSON.parse(readFileSync(join(MESSAGES_DIR, locale, `${ns}.json`), 'utf8')))
    for (const k of flat.keys()) keys.add(`${ns}.${k}`)
  }
  return keys
}

/**
 * 「代码引用键 ⊆ catalog」契约（#1748）。
 *
 * translator.ts:27 声称缺 key 由本文件在 CI 兜住 —— 在那之前这只是个愿望：
 * 上面那些用例只比对 catalog **内部**一致性（namespace 集合 / 两语言 diff /
 * 占位符 / 空值 / 键数规模），没有任何一条把代码里 t('...') / useT('ns')
 * 引用的键对照 catalog 校验存在性。删掉一个 catalog 条目，界面只会多渲染
 * 一行键路径（onError 对 MISSING_MESSAGE 静默回落），CI 全绿。
 */
describe('i18n 代码引用键 ⊆ catalog（#1748）', () => {
  it('t()/useT() 静态引用的键都存在于 catalog', () => {
    // 只查 zh-CN：en-US 的键集合与 zh-CN 一致由上面的 diff 用例保证。
    const catalog = catalogKeys('zh-CN')
    const { refs } = collectKeyReferences(ROOT)
    const missing = refs.filter((r) => !catalog.has(r.key))
    expect(
      missing.length
        ? `代码引用了 catalog 中不存在的键（${missing.length}）：\n` +
            missing.slice(0, 40).map((r) => `  ${r.file}:${r.line} → ${r.key}`).join('\n') +
            (missing.length > 40 ? `\n  … 共 ${missing.length} 处` : '')
        : '',
    ).toBe('')
  })

  it('扫描器没有空转（引用量与分目录贡献守住基线）', () => {
    const { refs } = collectKeyReferences(ROOT)
    // 基线 2026-10-20：2566 处引用（components 2474 / app 81 / lib 11）。
    // 下限留 ~40% 余量：日常增删文案不会踩线，但扫描器一旦写坏（正则失灵 /
    // 绑定识别失败 / 只扫了一个目录）引用量会断崖下跌 —— 那时必须红。
    // 空转的守卫比没有守卫更坏，#1748 的 50 键下限就是同款教训。
    expect(refs.length).toBeGreaterThan(1500)
    const perDir = new Map<string, number>()
    for (const r of refs) perDir.set(r.dir, (perDir.get(r.dir) ?? 0) + 1)
    for (const dir of ['components', 'app', 'lib']) {
      expect(perDir.get(dir) ?? 0, `${dir}/ 零引用 —— 扫描面漏了？`).toBeGreaterThan(0)
    }
  })

  it('不可静态判定的引用不超出登记配额（盲区棘轮，不许静默扩大）', () => {
    const { unresolved } = collectKeyReferences(ROOT)
    const actual = new Map<string, number>()
    for (const u of unresolved) actual.set(u.file, (actual.get(u.file) ?? 0) + 1)

    // translator 作为参数传给模块级 helper（`function label(t: TranslateFn)`）时，
    // namespace 不在调用点作用域内，无法静态解析。配额 = 实际，与
    // no-raw-cjk 白名单同款棘轮：新增要么内联掉，要么在这里登记并说明理由。
    // 模板串/变量键（`t(\`ns.${x}\`)`）天然不可静态判定，本扫描器不计数
    // （基线 2026-10-20：42 处 / 25 文件）。
    const QUOTA: Record<string, number> = {
      // carto-design（#1870）：design-candidates.tsx 的 3 处 namespace 经
      // props/helper 传入调用点作用域外，静态不可判定 —— 登记实际数（棘轮：
      // 增长即失败，收敛请内联）。
      'components/map/map-components/design-candidates.tsx': 3,
      'components/chat/cartography-result-card.tsx': 7,
      'components/settings/map-config.tsx': 2,
      'components/sidebar/modelops/modelops-model-detail.tsx': 7,
      'components/workbench/review-drawer.tsx': 3,
    }

    const failures: string[] = []
    for (const [file, allowed] of Object.entries(QUOTA)) {
      const n = actual.get(file) ?? 0
      if (n !== allowed) {
        failures.push(`${file}: 登记 ${allowed} ≠ 实际 ${n}（${n === 0 ? '删除条目' : '更新配额'}）`)
      }
    }
    for (const [file, n] of actual) {
      if (!(file in QUOTA)) failures.push(`${file}: ${n} 处引用无法静态判定（配额外，请内联或登记）`)
    }
    expect(failures.length ? '不可静态判定的引用需维护：\n  ' + failures.join('\n  ') : '').toBe('')
  })
})
