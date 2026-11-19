/**
 * GeoAI 面板测试（Platform 11 / WP-G）。
 *
 * oracle：坐标数学用 bounds 手算期望；面板行为以 mocked fetch 的
 * 请求体/渲染产物断言（不依赖被测代码生成期望）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));

import {
  type MapPrompt,
  type PreviewMeta,
  boxFromDrag,
  buildArtifactPayload,
  mapToScreen,
  screenToMap,
} from './geo-prompt-math';
import { I18nProvider } from '@/lib/i18n/i18n-provider';
import { GeoAiPanel } from './geoai-panel';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

const META: PreviewMeta = {
  preview_width: 200,
  preview_height: 100,
  source_width: 2000,
  source_height: 1000,
  crs: 'EPSG:4326',
  bounds: [100, 0, 110, 50], // 10° x 50° → 0.05°/px x 0.5°/px
};

describe('geo-prompt-math', () => {
  it('screenToMap / mapToScreen 手算往返（像元中心约定）', () => {
    // 像元中心：屏幕 (100,50) → fx=100.5/200 → 地图 (105.025, 24.875)。
    const m = screenToMap(100, 50, META);
    expect(m.x).toBeCloseTo(105.025, 6);
    expect(m.y).toBeCloseTo(24.75, 6);
    const s = mapToScreen(105, 25, META);
    expect(s.px).toBe(100);
    expect(s.py).toBe(50);
    // 左上角第一像元中心 → 地图左上角 1/8 预览步长处。
    const tl = screenToMap(0, 0, META);
    expect(tl.x).toBeCloseTo(100.025, 6);
    expect(tl.y).toBeCloseTo(49.75, 6);
  });

  it('crs=null（无地理参考）走源像元坐标（y 不翻转）', () => {
    const pixelMeta: PreviewMeta = {
      preview_width: 200,
      preview_height: 100,
      source_width: 200,
      source_height: 100,
      crs: null,
      // GDAL 默认式 bounds（y 向下）——像素面必须忽略它。
      bounds: [0, -100, 200, 0],
    };
    const m = screenToMap(100, 50, pixelMeta);
    expect(m.x).toBeCloseTo(100.5, 6);
    expect(m.y).toBeCloseTo(50.5, 6); // 正的像素行（翻转 bug 会给负值）
    const back = mapToScreen(m.x, m.y, pixelMeta);
    expect(back.px).toBe(100);
    expect(back.py).toBe(50);
    const box = boxFromDrag(0, 0, 100, 50, pixelMeta);
    expect(box.x).toBeCloseTo(0.5, 6);
    expect(box.y).toBeCloseTo(0.5, 6);
    expect(box.w).toBeCloseTo(100, 6);
    expect(box.h).toBeCloseTo(50, 6);
  });

  it('boxFromDrag 归一为正 w/h（北向东向）', () => {
    // 从屏幕 (0,0) 拖到 (100,50)（向东南）→ 地图从西北到东南的框。
    const box = boxFromDrag(0, 0, 100, 50, META);
    expect(box.kind).toBe('box');
    expect(box.x).toBeCloseTo(100.025, 6);
    expect(box.y).toBeCloseTo(24.75, 6); // 地图 y 较小端（南）
    expect(box.w).toBeCloseTo(5, 6);
    expect(box.h).toBeCloseTo(25, 6);
  });

  it('buildArtifactPayload 携带 CRS 与 provenance', () => {
    const prompts: MapPrompt[] = [
      { kind: 'point', x: 105, y: 25 },
      { kind: 'box', x: 100, y: 0, w: 2, h: 5 },
    ];
    const payload = buildArtifactPayload(prompts, META);
    expect(payload.schema_version).toBe(1);
    expect(payload.crs).toBe('EPSG:4326');
    expect(payload.points).toEqual([[105, 25]]);
    expect(payload.boxes).toEqual([[100, 0, 2, 5]]);
    expect(payload.provenance.created_by).toBe('geoai-panel');
  });
});

const previewBody = {
  png_base64:
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  preview_width: 200,
  preview_height: 100,
  source_width: 2000,
  source_height: 1000,
  crs: 'EPSG:4326',
  bounds: [100, 0, 110, 50],
};

const candidatesGeojson = {
  features: [
    {
      type: 'Feature',
      properties: { candidate: 0, score: 0.8, source: 'heuristic' },
      geometry: {
        type: 'Polygon',
        coordinates: [[[104, 24], [106, 24], [106, 26], [104, 26], [104, 24]]],
      },
    },
  ],
};

function jsonOk(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  });
}

const fetchMock = vi.fn();

async function bootPanel() {
  render(
    <I18nProvider>
      <GeoAiPanel />
    </I18nProvider>,
  );
  await userEvent.type(screen.getByTestId('source-uri'), '/data/demo.tif');
  await userEvent.click(screen.getByTestId('load-models'));
  await userEvent.click(screen.getByTestId('load-preview'));
  await waitFor(() =>
    expect(screen.getByTestId('prompt-overlay')).toBeTruthy(),
  );
}


beforeEach(() => {
    setMapSpecSessionCursor('real-session', 0, 'real-owner-token');
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/geoai/models')) {
        return Promise.resolve(
          jsonOk({
            models: [
              { model_id: 'tiny-promptable-seg', task_types: ['promptable_segmentation'] },
              { model_id: 'tiny-chip-embedder', task_types: ['embedding'] },
            ],
          }),
        );
      }
      if (url.includes('/geoai/preview')) {
        return Promise.resolve(jsonOk(previewBody));
      }
      if (url.includes('/geoai/artifact-geojson')) {
        return Promise.resolve(jsonOk(candidatesGeojson));
      }
      if (url.includes('/geoai/prompt-segment')) {
        return Promise.resolve(
          jsonOk({
            run_id: 'run-1',
            status: 'completed',
            outputs: {
              prompt_candidates: {
                path: '/data/modelops/outputs/run-1/prompt_candidates.geojson',
                windows: [
                  {
                    candidates: [
                      { index: 0, score: 0.8, source: 'heuristic' },
                      { index: 1, score: 0.5, source: 'heuristic' },
                    ],
                  },
                ],
              },
            },
          }),
        );
      }
      if (url.includes('/geoai/prompt-refine')) {
        return Promise.resolve(
          jsonOk({ run_id: 'run-2', status: 'completed' }),
        );
      }
      return Promise.resolve(jsonOk({}));
    });
});

describe('GeoAiPanel', () => {
  it('加载模型（过滤 promptable）与预览', async () => {
    await bootPanel();
    const select = screen.getByTestId('model-select') as HTMLSelectElement;
    expect(select.value).toBe('tiny-promptable-seg');
  });

  it('点击添加点提示 → 提交 artifact（CRS + 地图坐标）→ 候选渲染与接受', async () => {
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    // 预览中心 → 地图 (105, 25)：coords 直控 clientX/clientY
    // （user-event 的 pointer 坐标通道；无 coords 时沿用 (0,0)）。
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 100, y: 50 } },
      { keys: '[/MouseLeft]', coords: { x: 100, y: 50 } },
    ]);
    expect(screen.getByTestId('prompt-point-0')).toBeTruthy();

    await userEvent.click(screen.getByTestId('submit'));
    await waitFor(() =>
      expect(screen.getByTestId('candidate-list')).toBeTruthy(),
    );
    const segCall = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/geoai/prompt-segment'),
    );
    const body = JSON.parse(String(segCall?.[1]?.body));
    expect(body.model_id).toBe('tiny-promptable-seg');
    expect(body.artifact.crs).toBe('EPSG:4326');
    expect(body.artifact.points).toEqual([[105.025, 24.75]]);
    expect(body.return_candidates).toBe(true);

    // 候选几何渲染为 polygon。
    await waitFor(() =>
      expect(screen.getByTestId('candidate-poly-0')).toBeTruthy(),
    );
    // 接受候选 0 → refine 请求携带候选路径与下标。
    await userEvent.click(screen.getByTestId('accept-0'));
    await waitFor(() => {
      const refineCall = fetchMock.mock.calls.find(([u]) =>
        String(u).includes('/geoai/prompt-refine'),
      );
      expect(refineCall).toBeTruthy();
      const refineBody = JSON.parse(String(refineCall?.[1]?.body));
      expect(refineBody.session_id).toBe('real-session');
      expect(new Headers(refineCall?.[1]?.headers).get('X-Session-Token')).toBe('real-owner-token');
      expect(refineBody.candidate).toBe(0);
      expect(refineBody.candidates_path).toContain('run-1');
    });
    // 队列显示两次完成提交。
    await waitFor(() => expect(screen.getByText('run-2')).toBeTruthy());
  });

  it('撤销弹出提示；无提示时清除候选', async () => {
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 10, y: 10 } },
      { keys: '[/MouseLeft]', coords: { x: 10, y: 10 } },
    ]);
    expect(screen.getByTestId('prompt-point-0')).toBeTruthy();
    await userEvent.click(screen.getByTestId('undo'));
    expect(screen.queryByTestId('prompt-point-0')).toBeNull();
    // 无提示再撤销 → 清除候选区（不抛错）。
    await userEvent.click(screen.getByTestId('undo'));
    expect(screen.getByText('提交后按分数列出')).toBeTruthy();
  });
});

describe('GeoAiPanel 可访问性（#2157/#2159/#2163）', () => {
  it('输入/select 有可编程名称（label + aria-label，#2163）', () => {
    render(
      <I18nProvider>
        <GeoAiPanel />
      </I18nProvider>,
    );
    expect(screen.getByRole('textbox', { name: '数据源栅格路径' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: '分割模型' })).toBeTruthy();
    // 可见 <label for>（aria-label 之外的第二通道，读屏/点击双生效）。
    expect(document.querySelector('label[for="geoai-source-uri"]')).toBeTruthy();
    expect(document.querySelector('label[for="geoai-model-select"]')).toBeTruthy();
  });

  it('错误 role=alert；候选/队列有 aria-live 播报（#2163）', async () => {
    render(
      <I18nProvider>
        <GeoAiPanel />
      </I18nProvider>,
    );
    await userEvent.click(screen.getByTestId('load-preview'));
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入数据目录内的栅格路径');
    expect(screen.getByTestId('geoai-live').getAttribute('aria-live')).toBe('polite');
  });

  it('暗色渲染不含裸 slate/emerald 色类（#2157 回归网）', () => {
    const root = document.documentElement;
    root.classList.add('dark');
    root.setAttribute('data-theme', 'dark');
    try {
      const { container } = render(
        <I18nProvider>
          <GeoAiPanel />
        </I18nProvider>,
      );
      const bare = container.querySelectorAll(
        '[class*="bg-slate"],[class*="text-slate"],[class*="bg-emerald"],[class*="text-emerald"],[class*="bg-red-5"],[class*="text-red-7"]',
      );
      expect(bare).toHaveLength(0);
    } finally {
      root.classList.remove('dark');
      root.removeAttribute('data-theme');
    }
  });

  it('键盘：Tab 可聚焦叠加层，方向键移动光标，Enter 加点（#2159）', async () => {
    const user = userEvent.setup();
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    overlay.focus();
    expect(document.activeElement).toBe(overlay);
    await user.keyboard('{ArrowRight}{ArrowDown}');
    expect(screen.getByTestId('kbd-cursor')).toBeTruthy();
    await user.keyboard('{Enter}');
    expect(screen.getByTestId('prompt-point-0')).toBeTruthy();
    expect(screen.getByTestId('submit')).toHaveTextContent('提交（1 提示）');
  });

  it('键盘：B 切框模式，两次 Enter 定框；Escape 取消（#2159）', async () => {
    const user = userEvent.setup();
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    overlay.focus();
    await user.keyboard('b');
    await user.keyboard('{Enter}');
    expect(screen.getByTestId('kbd-box-start')).toBeTruthy();
    await user.keyboard('{ArrowRight}{ArrowRight}{ArrowDown}');
    await user.keyboard('{Enter}');
    expect(screen.getByTestId('prompt-box-0')).toBeTruthy();
    // Escape 取消未完成的第二个框：起点清除、无新框提交。
    await user.keyboard('{Enter}');
    expect(screen.getByTestId('kbd-box-start')).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('kbd-box-start')).toBeNull();
    expect(screen.queryByTestId('prompt-box-1')).toBeNull();
  });

  it('触屏等价路径：框模式按钮切换后无修饰键拖拽 = 框提示（#2159）', async () => {
    await bootPanel();
    await userEvent.click(screen.getByTestId('mode-box'));
    expect(screen.getByTestId('mode-box').getAttribute('aria-pressed')).toBe('true');
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 10, y: 10 } },
      { target: overlay, coords: { x: 80, y: 45 } },
      { keys: '[/MouseLeft]', coords: { x: 80, y: 45 } },
    ]);
    expect(screen.getByTestId('prompt-box-0')).toBeTruthy();
  });

  it('pointercancel 清理拖拽预览框，不留粘住状态（#2159）', async () => {
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([{ target: overlay, keys: '[MouseLeft>]', coords: { x: 10, y: 10 } }]);
    fireEvent.pointerMove(overlay, { clientX: 60, clientY: 40 });
    expect(screen.getByTestId('drag-box')).toBeTruthy();
    fireEvent.pointerCancel(overlay);
    expect(screen.queryByTestId('drag-box')).toBeNull();
    // 取消后移动不再复活预览框。
    fireEvent.pointerMove(overlay, { clientX: 90, clientY: 60 });
    expect(screen.queryByTestId('drag-box')).toBeNull();
  });

  it('#2163：提交 pending 时切源，旧请求返回后队列仍落终态、结果不渲染', async () => {
    let resolveSeg: ((v: unknown) => void) | undefined;
    const segPending = new Promise((resolve) => {
      resolveSeg = resolve;
    });
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/geoai/prompt-segment')) return segPending;
      if (url.includes('/geoai/models')) {
        return Promise.resolve(
          jsonOk({ models: [{ model_id: 'm1', task_types: ['promptable_segmentation'] }] }),
        );
      }
      if (url.includes('/geoai/preview')) return Promise.resolve(jsonOk(previewBody));
      return Promise.resolve(jsonOk({}));
    });
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 100, y: 50 } },
      { keys: '[/MouseLeft]', coords: { x: 100, y: 50 } },
    ]);
    await userEvent.click(screen.getByTestId('submit'));
    expect(screen.getByTestId('run-queue')).toHaveTextContent('进行中');
    // 提交期间切换数据源 → 上下文改变，提示被清空。
    const source = screen.getByTestId('source-uri');
    await userEvent.clear(source);
    await userEvent.type(source, '/data/other.tif');
    expect(screen.queryByTestId('prompt-point-0')).toBeNull();
    resolveSeg?.(jsonOk({ run_id: 'run-stale', outputs: { prompt_candidates: { windows: [
      { candidates: [{ index: 0, score: 0.9, source: 'heuristic' }] },
    ] } } }));
    // 队列项必须落 done（showing run id），而不是永久 running。
    await waitFor(() => expect(screen.getByText('run-stale')).toBeTruthy());
    // 旧上下文的结果不得渲染进新页面。
    expect(screen.queryByTestId('candidate-list')).toBeNull();
  });

  it('#2163：旧上下文的失败写入队列但不污染当前错误区', async () => {
    let rejectSeg: ((e: unknown) => void) | undefined;
    const segPending = new Promise((_resolve, reject) => {
      rejectSeg = reject;
    });
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/geoai/prompt-segment')) return segPending;
      if (url.includes('/geoai/models')) {
        return Promise.resolve(
          jsonOk({ models: [{ model_id: 'm1', task_types: ['promptable_segmentation'] }] }),
        );
      }
      if (url.includes('/geoai/preview')) return Promise.resolve(jsonOk(previewBody));
      return Promise.resolve(jsonOk({}));
    });
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 100, y: 50 } },
      { keys: '[/MouseLeft]', coords: { x: 100, y: 50 } },
    ]);
    await userEvent.click(screen.getByTestId('submit'));
    const source = screen.getByTestId('source-uri');
    await userEvent.clear(source);
    await userEvent.type(source, '/data/other.tif');
    rejectSeg?.(new Error('boom'));
    await waitFor(() =>
      expect(screen.getByTestId('run-queue')).toHaveTextContent('boom'),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('#2163：提交防重入 —— pending 期间重复点击只发一次请求', async () => {
    let resolveSeg: ((v: unknown) => void) | undefined;
    const segPending = new Promise((resolve) => {
      resolveSeg = resolve;
    });
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/geoai/prompt-segment')) return segPending;
      if (url.includes('/geoai/models')) {
        return Promise.resolve(
          jsonOk({ models: [{ model_id: 'm1', task_types: ['promptable_segmentation'] }] }),
        );
      }
      if (url.includes('/geoai/preview')) return Promise.resolve(jsonOk(previewBody));
      return Promise.resolve(jsonOk({}));
    });
    await bootPanel();
    const overlay = screen.getByTestId('prompt-overlay');
    await userEvent.pointer([
      { target: overlay, keys: '[MouseLeft>]', coords: { x: 100, y: 50 } },
      { keys: '[/MouseLeft]', coords: { x: 100, y: 50 } },
    ]);
    await userEvent.click(screen.getByTestId('submit'));
    // disabled 后第二次点击无效（忙挡 + 按钮态双重防护）。
    expect(screen.getByTestId('submit')).toBeDisabled();
    const calls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/geoai/prompt-segment'),
    );
    expect(calls).toHaveLength(1);
    resolveSeg?.(jsonOk({ run_id: 'run-1', outputs: {} }));
  });
});
