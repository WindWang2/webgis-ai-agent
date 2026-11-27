/**
 * apply-wave injectUseT 回归（P2-frontend_scripts-00）。
 *
 * 缺陷：injectUseT 原先只识别 `function Foo(...) {` 声明；箭头函数组件
 * `const Foo = () => {` 里的 JSX 文案被改写成 `{t('key')}` 后不会注入
 * `const t = useT(ns)`，整批改写产物编译不过。
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

type InjectUseT = (src: string, ns: string) => string;
type PinYinish = (s: string) => string;

let injectUseT: InjectUseT;
let pinYinish: PinYinish;

beforeAll(async () => {
  // apply-wave.mjs 顶层会按 CLI 参数执行一次；这里传入最小合法参数并加 --dry
  // （不落盘），只为取到导出的 injectUseT 做单元测试。
  const savedArgv = process.argv;
  const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
  process.argv = [
    process.execPath,
    'scripts/i18n/apply-wave.mjs',
    '--ns',
    'apply-wave-test',
    '--files',
    'test/i18n/apply-wave.test.ts',
    '--dry',
  ];
  try {
    const mod = (await import('../../scripts/i18n/apply-wave.mjs')) as {
      injectUseT: InjectUseT;
      pinYinish: PinYinish;
    };
    injectUseT = mod.injectUseT;
    pinYinish = mod.pinYinish;
  } finally {
    process.argv = savedArgv;
    logSpy.mockRestore();
  }
});

describe('apply-wave injectUseT', () => {
  it('箭头函数组件注入 const t = useT(ns)', () => {
    const src = [
      'const Widget = () => {',
      "  return <div>{t('hello')}</div>;",
      '};',
    ].join('\n');

    const out = injectUseT(src, 'demo');

    expect(out).toContain("const t = useT('demo');");
    expect(out).toContain('const Widget = () => {\n  const t = useT(\'demo\');');
  });

  it('带 props/返回类型标注的 export const 箭头组件同样注入', () => {
    const src = [
      'export const Widget = ({ title }: { title: string }): JSX.Element => {',
      "  return <span>{t('hello')}</span>;",
      '};',
    ].join('\n');

    const out = injectUseT(src, 'demo');

    expect(out).toContain("const t = useT('demo');");
  });

  it('function 声明组件的既有注入行为不变', () => {
    const src = [
      'export function Widget() {',
      "  return <div>{t('hello')}</div>;",
      '}',
    ].join('\n');

    const out = injectUseT(src, 'demo');

    expect(out).toContain("const t = useT('demo');");
  });

  it('非组件（小写箭头函数）不注入', () => {
    const src = [
      'const widget = () => {',
      "  return t('hello');",
      '};',
    ].join('\n');

    const out = injectUseT(src, 'demo');

    expect(out).not.toContain('useT(');
  });
});

describe('apply-wave pinYinish', () => {
  it('去掉全部 CJK 后按完整拉丁词生成 key（缺 /g 时会被 CJK 截断成 abC）', () => {
    expect(pinYinish('A中B文C')).toBe('abc');
  });

  it('纯中文串仍走稳定 hash 兜底', () => {
    expect(pinYinish('取消')).toMatch(/^k[0-9a-z]+$/);
  });
});
