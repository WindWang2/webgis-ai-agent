export interface ThemeColors {
  bg: string;
  bgSecondary: string;
  bgTertiary: string;
  text: string;
  textSecondary: string;
  textMuted: string;
  border: string;
  borderLight: string;
  shadow: string;
  shadowLight: string;
}

export const LIGHT_THEME: ThemeColors = {
  bg: '#dce8f2',
  bgSecondary: '#fcfdfe',
  bgTertiary: '#ffffff',
  text: '#0f172a',
  textSecondary: '#475569',
  textMuted: '#94a3b8',
  border: 'rgba(255,255,255,0.95)',
  borderLight: 'rgba(15,23,42,0.06)',
  shadow: 'rgba(15,23,42,0.12)',
  shadowLight: 'rgba(15,23,42,0.06)',
};

export const DARK_THEME: ThemeColors = {
  bg: '#0f172a',
  bgSecondary: '#1e293b',
  bgTertiary: '#334155',
  text: '#f8fafc',
  textSecondary: '#cbd5e1',
  textMuted: '#64748b',
  border: 'rgba(51,65,85,0.95)',
  borderLight: 'rgba(148,163,184,0.15)',
  shadow: 'rgba(0,0,0,0.4)',
  shadowLight: 'rgba(0,0,0,0.2)',
};

export function getThemeColors(theme: 'light' | 'dark'): ThemeColors {
  return theme === 'dark' ? DARK_THEME : LIGHT_THEME;
}
