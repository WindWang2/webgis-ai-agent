import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import React from 'react'
import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  LOCALE_GLOBAL_VAR,
  LOCALE_STORAGE_KEY,
  normalizeLocale,
} from '@/lib/i18n/config'
import { translatorFor, defaultTranslator } from '@/lib/i18n/translator'
import {
  useLanguageStore,
  applyLocaleToDom,
  writeLocaleCookie,
} from '@/lib/i18n/language-store'
import { I18nProvider, useI18n } from '@/lib/i18n/i18n-provider'
import { useT, useLocale, useSetLocale } from '@/lib/i18n/useT'
import { t as imperativeT, currentLocale } from '@/lib/i18n/t'

function resetLangDom() {
  window.localStorage.clear()
  document.cookie = `${LOCALE_COOKIE}=; path=/; max-age=0`
  delete (window as unknown as Record<string, unknown>)[LOCALE_GLOBAL_VAR]
  document.documentElement.lang = ''
  // 重置 store 到默认（绕过 persist：直接 setState）
  useLanguageStore.setState({ locale: DEFAULT_LOCALE })
}

describe('i18n config', () => {
  it('normalizeLocale 按 BCP-47 前缀归一', () => {
    expect(normalizeLocale('zh')).toBe('zh-CN')
    expect(normalizeLocale('zh-TW')).toBe('zh-CN')
    expect(normalizeLocale('en')).toBe('en-US')
    expect(normalizeLocale('en-GB')).toBe('en-US')
    expect(normalizeLocale('fr')).toBe('zh-CN')
    expect(normalizeLocale(null)).toBe('zh-CN')
    expect(normalizeLocale('')).toBe('zh-CN')
  })
})

describe('translator', () => {
  it('点路径取值 + {var} 插值', () => {
    const t = translatorFor('zh-CN')
    expect(t('common.close')).toBe('关闭')
    expect(t('errors.httpWrapper', { fallback: ' boom ', status: 500 })).toBe(
      ' boom （HTTP 500）'
    )
  })

  it('en translator 返回英文（ICU 引擎工作）', () => {
    const t = translatorFor('en-US')
    expect(t('common.close')).toBe('Close')
    expect(t('errors.category.TIMEOUT')).toBe(
      'The operation timed out. Please try again later'
    )
  })

  it('缺 key 回落键路径且不抛错', () => {
    const t = translatorFor('zh-CN')
    expect(t('no.such.key')).toBe('no.such.key')
  })

  it('defaultTranslator 是 zh 默认（无 provider 场景）', () => {
    expect(defaultTranslator('common.close')).toBe('关闭')
  })

  it('translatorFor 缓存同实例', () => {
    expect(translatorFor('zh-CN')).toBe(translatorFor('zh-CN'))
  })
})

describe('language-store', () => {
  beforeEach(() => {
    resetLangDom()
  })

  it('setLocale 更新 store + <html lang> + cookie', () => {
    act(() => {
      useLanguageStore.getState().setLocale('en-US')
    })
    expect(useLanguageStore.getState().locale).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(document.cookie).toContain(`${LOCALE_COOKIE}=en-US`)
  })

  it('applyLocaleToDom / writeLocaleCookie 独立可用', () => {
    applyLocaleToDom('en-US')
    writeLocaleCookie('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(document.cookie).toContain(`${LOCALE_COOKIE}=en-US`)
  })

  it('persist 经 storage.setItem 写出（VITEST 环境默认启用门控；setup 的 localStorage 是 vi mock，断言调用载荷）', () => {
    const setItem = window.localStorage.setItem as unknown as ReturnType<typeof vi.fn>
    setItem.mockClear()
    act(() => {
      useLanguageStore.getState().setLocale('en-US')
    })
    const call = setItem.mock.calls.find(([name]) => name === LOCALE_STORAGE_KEY)
    expect(call).toBeTruthy()
    const payload = JSON.parse(call![1] as string)
    expect(payload.state.locale).toBe('en-US')
  })
})

describe('I18nProvider + useT', () => {
  beforeEach(() => {
    resetLangDom()
  })

  function Probe() {
    const t = useT('settings')
    const locale = useLocale()
    const setLocale = useSetLocale()
    return (
      <div>
        <span data-testid="lang">{locale}</span>
        <span data-testid="label">{t('tabs.system')}</span>
        <button data-testid="switch" onClick={() => setLocale('en-US')} />
      </div>
    )
  }

  it('provider 内随语言即时切换文案', () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    expect(screen.getByTestId('label').textContent).toBe('系统')
    act(() => {
      fireEvent.click(screen.getByTestId('switch'))
    })
    expect(screen.getByTestId('label').textContent).toBe('System')
    expect(screen.getByTestId('lang').textContent).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
  })

  it('无 provider（裸渲染）回落 zh 默认，单测零改造', () => {
    render(<Probe />)
    expect(screen.getByTestId('label').textContent).toBe('系统')
    expect(screen.getByTestId('lang').textContent).toBe('zh-CN')
  })

  it('useI18n 暴露全键 t', () => {
    function FullKeyProbe() {
      const { t } = useI18n()
      return <span>{t('common.close')}</span>
    }
    render(
      <I18nProvider>
        <FullKeyProbe />
      </I18nProvider>
    )
    expect(screen.getByText('关闭')).toBeTruthy()
  })
})

describe('imperative t()', () => {
  beforeEach(() => {
    resetLangDom()
  })

  it('跟随 store 当前语言', () => {
    expect(imperativeT('common.close')).toBe('关闭')
    expect(currentLocale()).toBe('zh-CN')
    act(() => {
      useLanguageStore.getState().setLocale('en-US')
    })
    expect(imperativeT('common.close')).toBe('Close')
    expect(currentLocale()).toBe('en-US')
  })
})

describe('pre-paint 全局变量初始化', () => {
  beforeEach(() => {
    resetLangDom()
  })

  it('window.__GEOAGENT_LOCALE__ 在 store 创建前被读取（模拟 pre-paint）', async () => {
    // 模拟 layout.tsx 内联脚本：cookie → window 全局
    document.cookie = `${LOCALE_COOKIE}=en-US; path=/`
    ;(window as unknown as Record<string, unknown>)[LOCALE_GLOBAL_VAR] = 'en-US'
    vi.resetModules()
    const { useLanguageStore: freshStore } = await import('@/lib/i18n/language-store')
    expect(freshStore.getState().locale).toBe('en-US')
    // 恢复共享 store 状态，避免泄漏到后续用例
    useLanguageStore.setState({ locale: DEFAULT_LOCALE })
  })
})
