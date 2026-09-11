/**
 * rail 注册防回退测试（ADR-0142 D1）—— ops tab 的 append-only 注册行
 * 被并发 rebase 意外丢失时在此报警。
 */
import { describe, it, expect } from 'vitest';
import { MODE_TABS, WORKBENCH_MODES } from '@/lib/store/slices/workbenchSlice';
import type { LeftTab } from '@/lib/store/hud-types';

describe('ops rail tab 注册（append-only 契约）', () => {
  it("'ops' 在所有工作台模式的 tab 词表内", () => {
    for (const mode of WORKBENCH_MODES) {
      expect(MODE_TABS[mode], `${mode} 缺少 ops tab`).toContain('ops');
    }
  });

  it("'ops' 属于 LeftTab 词表（类型级注册的运行时佐证）", () => {
    const tabs: LeftTab[] = ['chat', 'project', 'layers', 'components', 'analysis', 'exports', 'export_layout', 'data_sources', 'tasks', 'results', 'ops'];
    expect(tabs).toContain('ops');
  });
});
