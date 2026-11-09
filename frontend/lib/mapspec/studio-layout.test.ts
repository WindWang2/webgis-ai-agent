import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import {
  commitMapSpecDocument,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  commitStudioText,
  commitStudioToggle,
  readStudioLayout,
  studioExportParams,
} from '@/lib/mapspec/studio-layout';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

/**
 * PR-C：Map Studio ↔ MapSpec 布局投影。
 * - 读：标题/副标题/指北针/比例尺/图例只来自 committed MapSpec 组件；
 * - 写：patch_component + expected_revision（游标）+ 缺席组件 upsert 带类型；
 * - 导出：制图内容只取 MapSpec，本地 exportSettings 的同名旧字段被忽略。
 */

const fetchMock = vi.fn();

function spec(components: unknown[]): MapSpec {
  return { version: '1', layers: [], layout: { components } } as unknown as MapSpec;
}

function okResponse(revision: number, components: unknown[] = []) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify({
      success: true,
      mutation_revision: revision,
      mapspec: { layers: [], layout: { components } },
    })),
  };
}

function bodies(): Array<Record<string, unknown>> {
  return fetchMock.mock.calls.map((c) => JSON.parse(c[1].body as string));
}

describe('readStudioLayout', () => {
  it('无 spec → hasSpec=false，装饰缺省开启', () => {
    expect(readStudioLayout(null)).toEqual({
      hasSpec: false, title: '', subtitle: '',
      showCompass: true, showScale: true, showLegend: true,
    });
  });

  it('读取标题文本与组件 enabled；禁用的标题读为空', () => {
    const layout = readStudioLayout(spec([
      { id: 'title', type: 'title', enabled: true, options: { text: '成都市人口密度' } },
      { id: 'subtitle', type: 'subtitle', enabled: false, options: { text: '旧副标题' } },
      { id: 'north-arrow', type: 'north_arrow', enabled: false },
      { id: 'scale-bar', type: 'scale_bar', enabled: true },
      { id: 'lg-a', type: 'categorical_legend', enabled: false },
      { id: 'cb-a', type: 'continuous_colorbar', enabled: true },
    ]));
    expect(layout).toMatchObject({
      hasSpec: true, title: '成都市人口密度', subtitle: '',
      showCompass: false, showScale: true, showLegend: true,
    });
  });

  it('图例族全部禁用 → showLegend=false', () => {
    expect(readStudioLayout(spec([
      { id: 'lg-a', type: 'legend', enabled: false },
      { id: 'cb-a', type: 'continuous_colorbar', enabled: false },
    ])).showLegend).toBe(false);
  });
});

describe('studioExportParams —— 导出只用 MapSpec', () => {
  it('title/装饰取自 spec，本地旧字段忽略；DPI/纸张保留本地', () => {
    const params = studioExportParams(
      {
        title: '本地旧标题', subtitle: '本地旧副标题', showCompass: true, showLegend: false,
        dpi: 150, paperSize: 'A4', orientation: 'portrait', format: 'vector-pdf',
        isExportMode: true,
      },
      spec([
        { id: 'title', type: 'title', options: { text: 'Spec 标题' } },
        { id: 'north-arrow', type: 'north_arrow', enabled: false },
      ]),
    );
    expect(params).toEqual({
      dpi: 150, paperSize: 'A4', orientation: 'portrait', format: 'vector-pdf',
      title: 'Spec 标题', subtitle: '',
      showCompass: false, showScale: true, showLegend: true,
    });
  });
});

describe('commitStudioText / commitStudioToggle —— user mutation 写回', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    setMapSpecSessionCursor('sid-studio', 7);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('标题组件缺席 → upsert 新建，带 component_type 与 expected_revision', async () => {
    commitMapSpecDocument(spec([]), 7);
    fetchMock.mockResolvedValueOnce(okResponse(8));
    await commitStudioText('title', '  新标题 ');
    const [body] = bodies();
    expect(body).toMatchObject({
      intent: 'patch_component', expected_revision: 7,
      component_id: 'title', component_type: 'title', upsert: true,
      enabled: true, options: { text: '新标题' },
    });
  });

  it('已有标题 → 按其 id patch，不带 upsert；文本未变不提交', async () => {
    commitMapSpecDocument(spec([{ id: 'title-main', type: 'title', options: { text: 'A' } }]), 7);
    await commitStudioText('title', 'A');
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(okResponse(8));
    await commitStudioText('title', 'B');
    const [body] = bodies();
    expect(body.component_id).toBe('title-main');
    expect(body).not.toHaveProperty('upsert');
    expect(body).not.toHaveProperty('component_type');
  });

  it('清空标题 → enabled=false（保留组件）', async () => {
    commitMapSpecDocument(spec([{ id: 'title', type: 'title', options: { text: 'A' } }]), 7);
    fetchMock.mockResolvedValueOnce(okResponse(8));
    await commitStudioText('title', '');
    expect(bodies()[0]).toMatchObject({ component_id: 'title', enabled: false });
    expect(bodies()[0]).not.toHaveProperty('options');
  });

  it('关闭缺席的指北针 → upsert 一个 disabled north_arrow（关成为显式意图）', async () => {
    commitMapSpecDocument(spec([]), 7);
    fetchMock.mockResolvedValueOnce(okResponse(8));
    await commitStudioToggle('showCompass', false);
    expect(bodies()[0]).toMatchObject({
      component_id: 'north-arrow', component_type: 'north_arrow', upsert: true, enabled: false,
    });
  });

  it('图例族逐个 patch，已处于目标状态的跳过；revision 按串行链递进', async () => {
    commitMapSpecDocument(spec([
      { id: 'lg-a', type: 'categorical_legend', enabled: true },
      { id: 'lg-b', type: 'legend', enabled: false },
      { id: 'cb-a', type: 'continuous_colorbar', enabled: true },
    ]), 7);
    fetchMock
      .mockResolvedValueOnce(okResponse(8, [
        { id: 'lg-a', type: 'categorical_legend', enabled: false },
        { id: 'lg-b', type: 'legend', enabled: false },
        { id: 'cb-a', type: 'continuous_colorbar', enabled: true },
      ]))
      .mockResolvedValueOnce(okResponse(9));
    await commitStudioToggle('showLegend', false);
    const sent = bodies();
    expect(sent.map((b) => b.component_id)).toEqual(['lg-a', 'cb-a']);
    expect(sent.map((b) => b.expected_revision)).toEqual([7, 8]);
    expect(sent.every((b) => b.enabled === false)).toBe(true);
  });

  it('无 committed spec → 不提交', async () => {
    commitMapSpecDocument(null as unknown as MapSpec, 7);
    await commitStudioToggle('showScale', false);
    await commitStudioText('subtitle', 'x');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
