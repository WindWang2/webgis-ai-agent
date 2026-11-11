import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import postcss from 'postcss';
import tailwind from 'tailwindcss';
import postcssConfig from '../../postcss.config.mjs';

describe('production PostCSS configuration', () => {
  it('compiles the application stylesheet with its configured Tailwind plugin', async () => {
    const input = resolve(__dirname, '../../app/globals.css');
    const result = await postcss([
      tailwind(postcssConfig.plugins.tailwindcss),
    ]).process(readFileSync(input, 'utf8'), { from: input });

    expect(result.css).not.toMatch(/@tailwind\s/);
    expect(result.css).toContain('.bg-surface-panel');
    expect(result.css).toContain('background-color: var(--surface-panel)');
    expect(result.css).toContain('.bg-surface-raised\\/90');
    expect(result.css).toContain('color-mix(in srgb, var(--surface-raised) 90%, transparent)');
  });
});
