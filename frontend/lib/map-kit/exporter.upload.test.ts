/**
 * V5（ADR-0118 D6）：导出诊断随成品上传 —— render_diagnostics Form 字段契约。
 *
 * uploadExport 在带降级清单时必须以 JSON 字符串附加 `render_diagnostics`
 * 字段（服务端 render_diagnostics.py 权威词表校验后持久化 sidecar）；
 * 无降级时不发字段（空清单不发，避免无谓 sidecar）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn(async () => ({ url: '/x.png', filename: 'x.png' }));

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8001' }));
vi.mock('@/lib/api/transport', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock('@/lib/utils/logger', () => ({ devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() } }));

import { uploadExport } from './exporter';

describe('uploadExport · render_diagnostics 透传（ADR-0118 D6）', () => {
  beforeEach(() => {
    apiFetch.mockClear();
    apiFetch.mockImplementation(async () => ({ url: '/x.png', filename: 'x.png' }));
  });

  it('带降级清单时附加 render_diagnostics JSON 字段', async () => {
    const blob = new Blob(['png'], { type: 'image/png' });
    await uploadExport(blob, 'export.png', 't', [
      { code: 'label_truncated', detail: 'layer=a len=240' },
      { code: 'chart_ref_unavailable', componentId: 'chart-1' },
    ]);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const form = apiFetch.mock.calls[0][1].rawBody as FormData;
    const raw = form.get('render_diagnostics');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(String(raw));
    expect(parsed.map((d: { code: string }) => d.code)).toEqual([
      'label_truncated',
      'chart_ref_unavailable',
    ]);
  });

  it('空清单/未传时不发字段', async () => {
    const blob = new Blob(['png'], { type: 'image/png' });
    await uploadExport(blob, 'export.png', 't', []);
    await uploadExport(blob, 'export2.png', 't');
    for (const call of apiFetch.mock.calls) {
      const form = call[1].rawBody as FormData;
      expect(form.get('render_diagnostics')).toBeNull();
    }
  });
});
