/**
 * #1746-10：生产卫生闸 —— 对比覆盖层不得直接 console.*。
 *
 * 仓库约定（lib/utils/logger.ts）：生产环境所有 console 调用必须经 devOnly
 * （NODE_ENV === 'production' 时全部抑制），防止把内部错误对象/堆栈泄漏到
 * 浏览器控制台。comparison-view 的副图 reconcile 兜底此前是裸 console.warn。
 *
 * 模式同 lib/interaction/contract.test.ts：读源码断言（无 DOM 依赖）。
 */
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const DIR = resolve(process.cwd(), 'components/map/comparison');

function sourceFiles(): string[] {
  return readdirSync(DIR).filter((f) => (f.endsWith('.ts') || f.endsWith('.tsx')) && !f.includes('.test.'));
}

describe('comparison 覆盖层生产卫生（devOnly）', () => {
  it('源码无裸 console.warn / console.error / console.log', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const src = readFileSync(resolve(DIR, file), 'utf-8');
      const lines = src.split('\n');
      lines.forEach((line, i) => {
        if (/console\.(warn|error|log)\s*\(/.test(line)) {
          offenders.push(`${file}:${i + 1} ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  it('reconcile 失败兜底走 devOnly.warn', () => {
    const src = readFileSync(resolve(DIR, 'comparison-view.tsx'), 'utf-8');
    expect(src).toContain("import { devOnly } from '@/lib/utils/logger'");
    expect(src).toMatch(/devOnly\.warn\('\[comparison\] secondary reconcile failed:'/);
    expect(src).not.toMatch(/console\.warn\('\[comparison\]/);
  });
});
