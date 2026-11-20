import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import postcss from 'postcss';
import { createRequire } from 'node:module';
import type { AcceptedPlugin } from 'postcss';
import postcssConfig from '../../postcss.config.mjs';

describe('production PostCSS configuration', () => {
  it('compiles the application stylesheet with its configured Tailwind plugin', async () => {
    const input = resolve(__dirname, '../../app/globals.css');
    const requirePlugin = createRequire(resolve(process.cwd(), 'package.json'));
    const plugins = Object.entries(postcssConfig.plugins).map(([name, options]) => {
      const factory = requirePlugin(name) as (options: unknown) => AcceptedPlugin;
      return factory(options);
    });
    expect(Object.keys(postcssConfig.plugins)).toContain('@tailwindcss/postcss');
    const result = await postcss(plugins).process(readFileSync(input, 'utf8'), { from: input });

    expect(result.css).not.toMatch(/@tailwind\s/);
    expect(result.css).toContain('.bg-surface-panel');
    // Tailwind4 emits theme aliases; assert the complete chain rather than
    // requiring the old Tailwind3 inlined variable/color-space spelling.
    expect(result.css).toContain('--color-surface-panel: var(--surface-panel)');
    expect(result.css).toContain('background-color: var(--color-surface-panel)');
    expect(result.css).toContain('--color-surface-raised: var(--surface-raised)');
    expect(result.css).toContain('.bg-surface-raised\\/90');
    expect(result.css).toContain('color-mix(in oklab, var(--color-surface-raised) 90%, transparent)');
  });
});
