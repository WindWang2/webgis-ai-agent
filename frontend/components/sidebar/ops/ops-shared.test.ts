/**
 * ops 面板时间格式化（#1628）：formatTime 随应用语言格式化，不再硬编码
 * zh-CN。断言策略与 project/format.test.ts 同源 —— 与同一引擎产出的
 * Intl 参考值逐字比对（时区无关）。
 */
import { describe, it, expect, afterEach } from 'vitest';
import { formatTime } from './ops-shared';
import { useLanguageStore } from '@/lib/i18n/language-store';

const ISO = '2026-10-18T14:30:45.000Z';

afterEach(() => {
  useLanguageStore.getState().setLocale('zh-CN');
});

describe('ops formatTime — 时间戳 locale 感知（#1628）', () => {
  it('跟随当前 locale（zh-CN 输出与旧硬编码逐字一致）', () => {
    useLanguageStore.getState().setLocale('zh-CN');
    expect(formatTime(ISO)).toBe(new Date(ISO).toLocaleTimeString('zh-CN', { hour12: false }));

    useLanguageStore.getState().setLocale('en-US');
    expect(formatTime(ISO)).toBe(new Date(ISO).toLocaleTimeString('en-US', { hour12: false }));
  });

  it('缺省值路径不受 locale 影响', () => {
    useLanguageStore.getState().setLocale('en-US');
    expect(formatTime(null)).toBe('—');
    expect(formatTime('not-a-date')).toBe('—');
  });
});
