/**
 * project 面板时间格式化（#1628）：时间戳随应用语言格式化，不再硬编码
 * zh-CN —— zh-CN 用户输出与旧实现逐字一致（既有面板断言零改造），英文
 * locale 下跟随 Intl 的 en-US 格式。
 *
 * 断言策略：与同一引擎产出的 Intl 参考值逐字比对（时区无关），并交叉验证
 * 两种 locale 的输出确实不同（证明 locale 真被消费，而非回落固定值）。
 */
import { describe, it, expect, afterEach } from 'vitest';
import { formatEpoch, formatIso } from './format';
import { useLanguageStore } from '@/lib/i18n/language-store';

const ISO = '2026-10-18T14:30:45.000Z';
const EPOCH_S = Math.floor(new Date(ISO).getTime() / 1000);

afterEach(() => {
  useLanguageStore.getState().setLocale('zh-CN');
});

describe('project format — 时间戳 locale 感知（#1628）', () => {
  it('formatIso：跟随当前 locale（zh-CN 输出与旧硬编码逐字一致）', () => {
    useLanguageStore.getState().setLocale('zh-CN');
    expect(formatIso(ISO)).toBe(new Date(ISO).toLocaleString('zh-CN', { hour12: false }));

    useLanguageStore.getState().setLocale('en-US');
    expect(formatIso(ISO)).toBe(new Date(ISO).toLocaleString('en-US', { hour12: false }));
    // 跨 locale 输出不同 —— locale 真被消费。
    expect(formatIso(ISO)).not.toBe(new Date(ISO).toLocaleString('zh-CN', { hour12: false }));
  });

  it('formatEpoch：跟随当前 locale（epoch 秒 → 日期时间）', () => {
    useLanguageStore.getState().setLocale('zh-CN');
    expect(formatEpoch(EPOCH_S)).toBe(
      new Date(EPOCH_S * 1000).toLocaleString('zh-CN', { hour12: false }),
    );

    useLanguageStore.getState().setLocale('en-US');
    expect(formatEpoch(EPOCH_S)).toBe(
      new Date(EPOCH_S * 1000).toLocaleString('en-US', { hour12: false }),
    );
  });

  it('缺省值路径不受 locale 影响（null/非有限值 → 未知占位）', () => {
    useLanguageStore.getState().setLocale('en-US');
    expect(formatIso(null)).toBe('—');
    expect(formatEpoch(Number.NaN)).toBeTruthy();
  });
});
