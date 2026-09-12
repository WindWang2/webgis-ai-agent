/**
 * no-raw-cjk 守卫（ADR-0144 / P2）。
 *
 * 规则：components/** 与 app/** 的 JSX 位置（文本/属性/表达式字符串）出现裸 CJK
 * 即 fail；lib/** 同样覆盖 JSX 位置（lib 几乎无 JSX，成本为零）。
 * lib/** 非对象字符串字面量（canvas 文案、toast 消息等）v1 不在守卫范围
 * —— 靠 P3 批 4 抽取 + 后续版本扩面（见白名单 _meta 归零计划）。
 *
 * 白名单 test/i18n/no-raw-cjk.whitelist.json 显式维护：
 *   - 文件 → 允许的残留条数；实际少于配额也 fail（强制向下修剪）
 *   - 归零计划见 _meta，逐批清零后删除对应条目
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { collectFindings, isJsxPosition } from '../../scripts/i18n/scan-lib.mjs'

const ROOT = join(import.meta.dirname, '..', '..')
const WHITELIST_PATH = join(import.meta.dirname, 'no-raw-cjk.whitelist.json')

interface Finding {
  file: string
  line: number
  kind: string
  text: string
}

const all: Finding[] = collectFindings(ROOT, ['components', 'app', 'lib']) as Finding[]

// 守卫范围：JSX 位置全仓库；obj:* 文案键仅 components/app（lib 的对象文案 v1 豁免）。
const guarded = all.filter(
  (f) => isJsxPosition(f.kind) || (f.kind.startsWith('obj:') && !f.file.startsWith('lib/'))
)

const whitelist = JSON.parse(readFileSync(WHITELIST_PATH, 'utf8')) as {
  files: Record<string, number>
}

describe('no-raw-cjk guard', () => {
  it('JSX 位置无白名单外裸 CJK，白名单配额不被突破', () => {
    const byFile = new Map<string, Finding[]>()
    for (const f of guarded) {
      const list = byFile.get(f.file) ?? []
      list.push(f)
      byFile.set(f.file, list)
    }

    const failures: string[] = []
    for (const [file, list] of byFile) {
      const allowed = whitelist.files[file]
      if (allowed === undefined) {
        failures.push(
          `${file}: ${list.length} 处裸 CJK（白名单外）\n` +
            list.slice(0, 5).map((f) => `    L${f.line} [${f.kind}] ${f.text}`).join('\n') +
            (list.length > 5 ? `\n    … 共 ${list.length} 处` : '')
        )
      } else if (list.length > allowed) {
        failures.push(
          `${file}: ${list.length} 处 > 白名单配额 ${allowed}（不得新增，请键化或下调配额）`
        )
      }
    }

    expect(failures.length ? '发现裸 CJK：\n' + failures.join('\n') : '').toBe('')
  })

  it('白名单无陈旧条目（配额必须与实际一致，倒逼逐版归零）', () => {
    const byFile = new Map<string, number>()
    for (const f of guarded) byFile.set(f.file, (byFile.get(f.file) ?? 0) + 1)

    const stale: string[] = []
    for (const [file, allowed] of Object.entries(whitelist.files)) {
      const actual = byFile.get(file) ?? 0
      if (actual !== allowed) {
        stale.push(`${file}: 白名单 ${allowed} ≠ 实际 ${actual}（${actual === 0 ? '删除条目' : '下调配额'}）`)
      }
    }
    expect(stale.length ? '白名单需修剪：\n' + stale.join('\n') : '').toBe('')
  })
})
