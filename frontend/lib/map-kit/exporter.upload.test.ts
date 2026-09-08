/**
 * V5（ADR-0118 D6）：导出诊断随成品上传 —— render_diagnostics Form 字段契约。
 *
 * uploadExport 在带降级清单时必须以 JSON 字符串附加 `render_diagnostics`
 * 字段（服务端 render_diagnostics.py 权威词表校验后持久化 sidecar）；
 * 无降级时不发字段（空清单不发，避免无谓 sidecar）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type FetchCall = { url: string; init: { rawBody: FormData } };
const fetchCalls: FetchCall[] = [];
const apiFetch = vi.fn(async (url: string, init: { rawBody: FormData }) => {
  fetchCalls.push({ url, init });
  return { url: '/x.png', filename: 'x.png' };
});

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8001' }));
vi.mock('@/lib/api/transport', () => ({ apiFetch }));
vi.mock('@/lib/utils/logger', () => ({ devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() } }));

import { uploadExport } from './exporter';

describe('uploadExport · render_diagnostics 透传（ADR-0118 D6）', () => {
  beforeEach(() => {
    fetchCalls.length = 0;
    apiFetch.mockClear();
  });

  it('带降级清单时附加 render_diagnostics JSON 字段', async () => {
    const blob = new Blob(['png'], { type: 'image/png' });
    await uploadExport(blob, 'export.png', 't', [
      { code: 'label_truncated', detail: 'layer=a len=240' },
      { code: 'chart_ref_unavailable', componentId: 'chart-1' },
    ]);
    expect(fetchCalls).toHaveLength(1);
    const raw = fetchCalls[0]!.init.rawBody.get('render_diagnostics');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(String(raw)) as Array<{ code: string }>;
    expect(parsed.map((d) => d.code)).toEqual([
      'label_truncated',
      'chart_ref_unavailable',
    ]);
  });

  it('空清单/未传时不发字段', async () => {
    const blob = new Blob(['png'], { type: 'image/png' });
    await uploadExport(blob, 'export.png', 't', []);
    await uploadExport(blob, 'export2.png', 't');
    expect(fetchCalls).toHaveLength(2);
    for (const call of fetchCalls) {
      expect(call.init.rawBody.get('render_diagnostics')).toBeNull();
    }
  });
});
