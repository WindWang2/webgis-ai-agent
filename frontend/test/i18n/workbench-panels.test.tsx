import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SkillWorkbench } from '@/components/settings/skill-workbench';
import { ScalePortrayalPanel } from '@/components/hud/scale-portrayal-panel';
import { I18nProvider } from '@/lib/i18n/i18n-provider';
import { useLanguageStore } from '@/lib/i18n/language-store';

afterEach(() => {
  cleanup();
  useLanguageStore.setState({ locale: 'zh-CN' });
  vi.unstubAllGlobals();
});

it('renders real workbench panels in English and switches their labels in place', async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    expect(['/api/v1/skill-jobs/catalog', '/api/v1/skill-jobs/presets']).toContain(path);
    expect(init?.method ?? 'GET').toBe('GET');
    return new Response(JSON.stringify(path.endsWith('/presets') ? { presets: [] } : { skills: [], md_guides: [], extension_candidates: [] }), {
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetch);
  render(<I18nProvider><SkillWorkbench /><ScalePortrayalPanel layerId="test-layer" /></I18nProvider>);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  act(() => useLanguageStore.getState().setLocale('en-US'));
  expect(screen.getByText('Skill workbench')).toBeInTheDocument();
  expect(screen.getByText(/No active session\. Start a conversation first/)).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Scale portrayal' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Scale portrayal: Enable automatic LOD' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Scale portrayal: Fixed density 60%' })).toBeInTheDocument();
  act(() => useLanguageStore.getState().setLocale('zh-CN'));
  expect(screen.getByText('技能作业工作台')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '按尺度表达' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '按尺度表达：启用自动 LOD' })).toBeInTheDocument();
  expect(screen.queryByText('Skill workbench')).not.toBeInTheDocument();
});
