import { describe, it, expect } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';

/**
 * Wave 10（audit 08）：漂移的第二真相源清理。
 *
 * 原 lib/theme.ts 是 globals.css 之外的一份硬编码色表（值已漂移：
 * #dce8f2 vs CSS 的 #dbe6f1），零生产消费者，但其测试把漂移值锁定为
 * 「正确真相」。主题颜色的唯一权威是 app/globals.css 的 CSS 变量
 * （--surface-*/--text-*/...，由 theme-contrast.test.ts 做 AA 对比守卫）；
 * 本文件只保留仍有意义的 store 主题切换契约。
 */
describe('theme store contract（CSS 变量为唯一颜色权威）', () => {
  it('allows dynamic theme switching via Zustand store', () => {
    useHudStore.getState().setTheme('dark');
    expect(useHudStore.getState().theme).toBe('dark');

    useHudStore.getState().setTheme('light');
    expect(useHudStore.getState().theme).toBe('light');
  });
});
