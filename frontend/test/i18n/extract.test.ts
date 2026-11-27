/**
 * extract slugify 回归（P3-frontend_scripts-00）。
 *
 * 缺陷：`text.replace(CJK, '')` 缺 /g，混排文本只删首个 CJK，后续 CJK 被
 * 标点清洗成空格，拉丁词被切断成 aB / abC 之类的碎片键。
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

// extract.mjs 顶层会全仓扫描；单测只取 slugify，故把扫描器打桩为无发现。
vi.mock('../../scripts/i18n/scan-lib.mjs', () => ({
  collectFindings: () => [],
  isJsxPosition: () => false,
}));

type Slugify = (text: string, used: Set<string>) => string;

let slugify: Slugify;

beforeAll(async () => {
  const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
  try {
    const mod = (await import('../../scripts/i18n/extract.mjs')) as { slugify: Slugify };
    slugify = mod.slugify;
  } finally {
    logSpy.mockRestore();
  }
});

describe('extract slugify', () => {
  it('混排文本去掉全部 CJK 后按完整拉丁词生成 key（缺 /g 时为 abC）', () => {
    expect(slugify('A中B文C', new Set())).toBe('abc');
  });

  it('纯中文串仍回退 copy 并递增后缀', () => {
    const used = new Set<string>();
    expect(slugify('取消', used)).toBe('copy');
    expect(slugify('保存', used)).toBe('copy2');
  });
});
