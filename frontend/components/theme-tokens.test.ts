import { describe, it, expect } from 'vitest';
import { getThemeColors, LIGHT_THEME, DARK_THEME } from '@/lib/theme';
import { useHudStore } from '@/lib/store/useHudStore';

describe('Frontend Theme System & Design Tokens', () => {
  it('provides complete light and dark theme color definitions', () => {
    expect(LIGHT_THEME.bg).toBe('#dce8f2');
    expect(LIGHT_THEME.text).toBe('#0f172a');
    expect(DARK_THEME.bg).toBe('#0f172a');
    expect(DARK_THEME.text).toBe('#f8fafc');
  });

  it('returns appropriate ThemeColors object for light and dark themes', () => {
    expect(getThemeColors('light')).toEqual(LIGHT_THEME);
    expect(getThemeColors('dark')).toEqual(DARK_THEME);
  });

  it('allows dynamic theme switching via Zustand store', () => {
    useHudStore.getState().setTheme('dark');
    expect(useHudStore.getState().theme).toBe('dark');

    useHudStore.getState().setTheme('light');
    expect(useHudStore.getState().theme).toBe('light');
  });
});
