/**
 * Canvas copilot 组件测试（ADR-0194 / 任务书阶段二）。
 *
 * 覆盖：
 * - 框选工具：激活后捕获手势 → dispatch 全局状态（copilotTool/highlight）
 *   并在画布上渲染虚线高亮框（stroke-dasharray）；
 * - 信封上报：手势完成 → SpatialAffordanceEnvelope 构建 + 即时 POST +
 *   stage 随轮捎带；同步上报路径 < 100ms（spec §5 预算）；
 * - 纯函数：box/freehand/lasso 捕获 + 像素→WGS84 线性投影；
 * - 生成式微 UI：4 类卡片渲染 + 双向绑定（widget_reply 回流 + onReply）
 *   + 不安全 widget 拒挂（isWidgetSpecSafe / extractMountedWidget）；
 * - streamChat 携带 canvas_actions。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, act, fireEvent, render, screen } from '@testing-library/react';
import { openStream } from '@/lib/api/transport';
import {
  buildEnvelope,
  extractMountedWidget,
  isWidgetSpecSafe,
  reportAffordance,
  type SpatialAffordanceEnvelope,
  type WidgetSpec,
} from '@/lib/copilot/affordance';
import {
  boxRing,
  freehandRing,
  polygonLassoRing,
  pxToLngLat,
} from '@/lib/copilot/sketch-capture';
import { configureAffordanceChannel } from '@/lib/copilot/affordance';
import { sendWidgetReply } from '@/lib/copilot/widget-binding';
import { streamChat } from '@/lib/api/chat';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  SpatialSketchTool,
} from './spatial-sketch-tool';
import { WidgetHost } from './generative-widgets/widget-host';
import { HistogramSliderWidget } from './generative-widgets/histogram-slider';
import { SwipeCompareWidget } from './generative-widgets/swipe-compare';
import { CandidatePickerWidget } from './generative-widgets/candidate-picker';
import { SketchBoxWidget } from './generative-widgets/sketch-box';

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
  openStream: vi.fn(),
}));

const RECT = { left: 0, top: 0, width: 800, height: 600 };

beforeEach(() => {
  useHudStore.getState().clearCopilotState();
  configureAffordanceChannel({ getSessionId: () => 'sess-copilot-test' });
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  // jsdom 26 无 PointerEvent —— RTL 回退构造的裸 Event 不携带 clientX。
  // 以 MouseEvent 子类 polyfill（clientX/Y 由 MouseEvent init 原生支持）。
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: Record<string, unknown> = {}) {
      super(type, init);
      this.pointerId = (init.pointerId as number) ?? 0;
    }
  }
  vi.stubGlobal('PointerEvent', PointerEventPolyfill);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    RECT as DOMRect,
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(openStream).mockReset();
});

/* ─── 纯函数 ─── */

describe('sketch-capture 纯函数', () => {
  it('boxRing 归一化为闭合 4 角环', () => {
    const ring = boxRing([300, 260], [100, 100]);
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual([100, 100]);
    expect(ring[4]).toEqual(ring[0]);
  });

  it('freehandRing 降采样并闭合；点数不足返回 null', () => {
    const pts: [number, number][] = [[0, 0], [1, 0], [2, 0], [50, 50], [100, 40], [0, 2]];
    const ring = freehandRing(pts);
    expect(ring).not.toBeNull();
    expect(ring![0]).toEqual(ring![ring!.length - 1]);
    expect(freehandRing([[0, 0], [1, 1]])).toBeNull();
  });

  it('polygonLassoRing 闭合且最少 3 点', () => {
    expect(polygonLassoRing([[0, 0], [10, 0]])).toBeNull();
    const ring = polygonLassoRing([[0, 0], [10, 0], [10, 10]]);
    expect(ring![0]).toEqual(ring![ring!.length - 1]);
  });

  it('pxToLngLat 线性投影：右移增经度、下移减纬度', () => {
    const view = { center: [116.33, 39.83] as [number, number], zoom: 14 };
    const center = pxToLngLat([400, 300], RECT, view);
    const right = pxToLngLat([500, 300], RECT, view);
    const down = pxToLngLat([400, 400], RECT, view);
    expect(center[0]).toBeCloseTo(116.33, 2);
    expect(right[0]).toBeGreaterThan(center[0]);
    expect(down[1]).toBeLessThan(center[1]);
  });
});

/* ─── 框选工具：全局状态 dispatch + 虚线高亮 ─── */

describe('SpatialSketchTool 框选', () => {
  it('工具未激活时不渲染覆层', () => {
    const { container } = render(<SpatialSketchTool />);
    expect(container.querySelector('[data-testid="copilot-sketch-overlay"]')).toBeNull();
  });

  it('框选手势 dispatch 全局状态并渲染虚线高亮框', () => {
    const envelopeSpy = vi.fn();
    render(
      <SpatialSketchTool
        mapView={{ center: [116.33, 39.83], zoom: 14 }}
        onAffordance={envelopeSpy}
      />,
    );
    act(() => {
      useHudStore.getState().setCopilotTool('box_select');
    });
    const overlay = screen.getByTestId('copilot-sketch-overlay');
    expect(overlay.getAttribute('data-copilot-tool')).toBe('box_select');

    fireEvent.pointerDown(overlay, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 300, clientY: 260, pointerId: 1 });
    fireEvent.pointerUp(overlay, { clientX: 300, clientY: 260, pointerId: 1 });

    // 全局状态 dispatch
    const highlight = useHudStore.getState().copilotHighlight;
    expect(highlight).not.toBeNull();
    expect(highlight!.kind).toBe('box_select');
    expect(highlight!.ring).toHaveLength(5);
    // 画布上的虚线高亮框
    const polygon = document.querySelector('[data-testid="copilot-highlight"]');
    expect(polygon).not.toBeNull();
    expect(polygon!.getAttribute('stroke-dasharray')).toBe('6 4');
    // 信封出口：框选几何 + screen_px + bbox
    expect(envelopeSpy).toHaveBeenCalledTimes(1);
    const envelope = envelopeSpy.mock.calls[0][0] as SpatialAffordanceEnvelope;
    expect(envelope.actions).toHaveLength(1);
    const action = envelope.actions[0];
    expect(action.kind).toBe('box_select');
    expect(action.geometry?.type).toBe('Polygon');
    expect(action.screen_px).toEqual({ x: 100, y: 100, width: 200, height: 160 });
    expect(action.bbox![0]).toBeLessThan(action.bbox![2]);
  });

  it('手绘圈选默认通道：即时 POST + stage + 上报延迟 < 100ms', async () => {
    render(<SpatialSketchTool mapView={{ center: [116.33, 39.83], zoom: 14 }} />);
    act(() => {
      useHudStore.getState().setCopilotTool('freehand_lasso');
    });
    const overlay = screen.getByTestId('copilot-sketch-overlay');
    fireEvent.pointerDown(overlay, { clientX: 50, clientY: 50, pointerId: 1 });
    for (let i = 1; i <= 5; i++) {
      fireEvent.pointerMove(overlay, {
        clientX: 50 + i * 40,
        clientY: 50 + (i % 2) * 30,
        pointerId: 1,
      });
    }
    fireEvent.pointerUp(overlay, { clientX: 250, clientY: 80, pointerId: 1 });

    const fetchMock = vi.mocked(globalThis.fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/chat/sessions/sess-copilot-test/canvas-actions');
    const body = JSON.parse(String(init?.body)) as SpatialAffordanceEnvelope;
    expect(body.envelope_id).toBeTruthy();
    expect(body.actions[0].kind).toBe('freehand_lasso');
    // stage：下一轮 turn 捎带同一动作
    const staged = useHudStore.getState().stagedCopilotEnvelope;
    expect(staged?.actions[0].kind).toBe('freehand_lasso');
    // 同步上报路径预算（spec §5）
    const latency = useHudStore.getState().copilotReportLatencyMs;
    expect(latency).not.toBeNull();
    expect(latency!).toBeLessThan(100);
  });

  it('多边形套索：点击加点，完成按钮收口', () => {
    const envelopeSpy = vi.fn();
    render(<SpatialSketchTool mapView={{ center: [116.33, 39.83], zoom: 14 }} onAffordance={envelopeSpy} />);
    act(() => {
      useHudStore.getState().setCopilotTool('polygon_lasso');
    });
    const overlay = screen.getByTestId('copilot-sketch-overlay');
    fireEvent.pointerDown(overlay, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerDown(overlay, { clientX: 400, clientY: 120, pointerId: 2 });
    fireEvent.pointerDown(overlay, { clientX: 250, clientY: 420, pointerId: 3 });
    fireEvent.click(screen.getByTestId('copilot-finish-lasso'));
    const envelope = envelopeSpy.mock.calls[0][0] as SpatialAffordanceEnvelope;
    expect(envelope.actions[0].kind).toBe('polygon_lasso');
    const ring = (envelope.actions[0].geometry!.coordinates as number[][][])[0];
    expect(ring).toHaveLength(4); // 3 点闭合
  });

  it('Escape 清除高亮并退出工具', () => {
    render(<SpatialSketchTool />);
    act(() => {
      useHudStore.getState().setCopilotTool('box_select');
      useHudStore.getState().setCopilotHighlight({ ring: [[0, 0], [1, 1], [2, 0]], kind: 'box_select' });
    });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useHudStore.getState().copilotTool).toBeNull();
    expect(useHudStore.getState().copilotHighlight).toBeNull();
  });
});

/* ─── 生成式微 UI ─── */

function histogramSpec(): WidgetSpec {
  return {
    widget_id: 'w-hist-1',
    kind: 'histogram_slider',
    title: 'NDVI 断点',
    payload: {
      field: 'ndvi',
      bins: [
        { lo: 0, hi: 10, count: 3 },
        { lo: 10, hi: 20, count: 7 },
        { lo: 20, hi: 30, count: 5 },
      ],
      breaks: [15],
      min: 0,
      max: 30,
      layer_ref: 'ndvi_layer',
    },
  };
}

describe('WidgetHost 挂载与双向绑定', () => {
  it('无卡片时不渲染，挂载后按 kind 渲染并可关闭', () => {
    const { container } = render(<WidgetHost />);
    expect(container.querySelector('[data-testid="copilot-widget-host"]')).toBeNull();
    act(() => {
      useHudStore.getState().pushCopilotWidget(histogramSpec());
    });
    expect(screen.getByTestId('generative-widget-histogram_slider')).toBeTruthy();
    expect(screen.getByTestId('histogram-bars')).toBeTruthy();
    fireEvent.click(screen.getByTestId('widget-dismiss'));
    expect(useHudStore.getState().copilotWidgets).toHaveLength(0);
  });

  it('同 widget_id 重复挂载幂等（resume 重放），FIFO 上限 4', () => {
    useHudStore.getState().pushCopilotWidget(histogramSpec());
    useHudStore.getState().pushCopilotWidget(histogramSpec());
    expect(useHudStore.getState().copilotWidgets).toHaveLength(1);
    for (let i = 0; i < 5; i++) {
      useHudStore.getState().pushCopilotWidget({
        ...histogramSpec(),
        widget_id: `w-extra-${i}`,
      });
    }
    expect(useHudStore.getState().copilotWidgets.length).toBeLessThanOrEqual(4);
  });

  it('直方图断点调节 → widget_reply 回流 + onReply（双向绑定）', () => {
    const onReply = vi.fn();
    render(<HistogramSliderWidget spec={histogramSpec()} onReply={onReply} />);
    fireEvent.change(screen.getByTestId('break-slider-0'), { target: { value: '18' } });
    fireEvent.click(screen.getByTestId('histogram-apply'));
    expect(onReply).toHaveBeenCalledWith({ breaks: [18] });
    const fetchMock = vi.mocked(globalThis.fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(String(init?.body));
    const reply = body.actions[0].meta.widget_reply;
    expect(reply.widget_id).toBe('w-hist-1');
    expect(reply.kind).toBe('histogram_slider');
    expect(reply.value.breaks).toEqual([18]);
  });

  it('卷帘对比：滑块拖动 + 方案选择回传', () => {
    const onReply = vi.fn();
    render(
      <SwipeCompareWidget
        spec={{
          widget_id: 'w-swipe-1',
          kind: 'swipe_compare',
          payload: {
            left: { label: '方案A', layer_ref: 'lyr-a' },
            right: { label: '方案B', layer_ref: 'lyr-b' },
          },
        }}
        onReply={onReply}
      />,
    );
    fireEvent.change(screen.getByTestId('swipe-position'), { target: { value: '70' } });
    const left = screen.getByTestId('swipe-pane-left');
    expect(left.style.width).toBe('70%');
    fireEvent.click(screen.getByTestId('swipe-choose-right'));
    expect(onReply).toHaveBeenCalledWith({ selected: 'right' });
  });

  it('候选确认卡：单选 + 确认回传选中 id', () => {
    const onReply = vi.fn();
    render(
      <CandidatePickerWidget
        spec={{
          widget_id: 'w-cand-1',
          kind: 'candidate_picker',
          payload: {
            candidates: [
              { id: 'c1', label: '候选1', stats: { area_km2: 3.2 } },
              { id: 'c2', label: '候选2' },
            ],
            selection_mode: 'single',
          },
        }}
        onReply={onReply}
      />,
    );
    expect(screen.getByTestId('candidate-picker-body')).toBeTruthy();
    fireEvent.click(screen.getByTestId('candidate-c1'));
    fireEvent.click(screen.getByTestId('candidate-confirm'));
    expect(onReply).toHaveBeenCalledWith({ selected: ['c1'] });
  });

  it('sketch-box：CTA 激活框选工具，高亮环完成自动回传几何', () => {
    const onReply = vi.fn();
    render(
      <SketchBoxWidget
        spec={{
          widget_id: 'w-sketch-1',
          kind: 'sketch_box',
          payload: { prompt: '在图上框选目标水域', constraints: { min_area_m2: 100 } },
        }}
        onReply={onReply}
      />,
    );
    fireEvent.click(screen.getByTestId('sketch-box-start'));
    expect(useHudStore.getState().copilotTool).toBe('box_select');
    act(() => {
      useHudStore.getState().setCopilotHighlight({
        ring: [[100, 100], [300, 100], [300, 260], [100, 260]],
        kind: 'box_select',
        lnglatRing: [[116.3, 39.8], [116.36, 39.8], [116.36, 39.86], [116.3, 39.86]],
      });
    });
    expect(onReply).toHaveBeenCalledTimes(1);
    const value = onReply.mock.calls[0][0] as { geometry: { type: string } };
    expect(value.geometry.type).toBe('Polygon');
    // 裁决后工具自动退出
    expect(useHudStore.getState().copilotTool).toBeNull();
  });
});

/* ─── 安全校验 ─── */

describe('widget 安全校验（mount 前最后一道）', () => {
  it('isWidgetSpecSafe 拒绝事件键 / 脚本值 / 宿主对象', () => {
    expect(isWidgetSpecSafe({ onclick: 'x' })).toBe(false);
    expect(isWidgetSpecSafe({ nested: { onError: 'x' } })).toBe(false);
    expect(isWidgetSpecSafe('javascript:alert(1)')).toBe(false);
    expect(isWidgetSpecSafe('<script>alert(1)</script>')).toBe(false);
    expect(isWidgetSpecSafe('data:text/html;base64,AAAA')).toBe(false);
    expect(isWidgetSpecSafe({ html: '<iframe></iframe>' })).toBe(false);
    expect(isWidgetSpecSafe(() => 'x')).toBe(false);
    expect(isWidgetSpecSafe({ ok: [1, 'two', { fine: true }] })).toBe(true);
  });

  it('extractMountedWidget：合法 spec 放行，畸形/不安全丢弃', () => {
    const ok = extractMountedWidget({
      type: 'ui_action',
      action: 'mount_widget',
      widget: histogramSpec(),
    });
    expect(ok?.widget_id).toBe('w-hist-1');
    expect(
      extractMountedWidget({ type: 'ui_action', action: 'detonate' }),
    ).toBeNull();
    expect(
      extractMountedWidget({
        type: 'ui_action',
        action: 'mount_widget',
        widget: { ...histogramSpec(), payload: { onclick: 'x' } },
      }),
    ).toBeNull();
    expect(
      extractMountedWidget({
        type: 'ui_action',
        action: 'mount_widget',
        widget: { widget_id: 'w-x', kind: 'free_form_html', payload: {} },
      }),
    ).toBeNull();
  });
});

/* ─── 信封构建 / stream 通道 ─── */

describe('信封与 stream 捎带', () => {
  it('buildEnvelope 生成 id/ts 与动作序列', () => {
    const envelope = buildEnvelope([
      {
        action_id: 'a-1',
        kind: 'box_select',
        geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
      },
    ]);
    expect(envelope.envelope_id).toContain('env-');
    expect(envelope.client_ts).toBeGreaterThan(0);
    expect(envelope.actions).toHaveLength(1);
  });

  it('streamChat 请求体携带 canvas_actions', async () => {
    const sse = 'event: session\ndata: {"session_id":"s-1"}\n\n';
    vi.mocked(openStream).mockResolvedValue(
      new Response(sse, { status: 200 }) as unknown as globalThis.Response,
    );
    const canvasActions = {
      envelope_id: 'env-9',
      client_ts: 1760000000000,
      actions: [{ action_id: 'a-1', kind: 'box_select' }],
    };
    const gen = streamChat(
      '画一下',
      's-1',
      undefined,
      undefined,
      undefined,
      null,
      undefined,
      null,
      canvasActions,
    );
    const first = await gen.next();
    expect(first.value.event).toBe('session');
    const [, init] = vi.mocked(openStream).mock.calls[0];
    // openStream 收到的是对象 body（序列化在 transport 内部）。
    const body = (init as { body: Record<string, unknown> }).body;
    expect((body.canvas_actions as Record<string, unknown>).envelope_id).toBe('env-9');
    const actions = (body.canvas_actions as { actions: Array<{ kind: string }> }).actions;
    expect(actions[0].kind).toBe('box_select');
  });

  it('reportAffordance 未注册会话时退化为仅构建（不 dispatch）', () => {
    configureAffordanceChannel({ getSessionId: () => null });
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();
    const result = reportAffordance(
      buildEnvelope([{ action_id: 'a-x', kind: 'highlight' }]),
    );
    expect(result.dispatched).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/* ─── copilotSlice 行为 ─── */

describe('copilotSlice', () => {
  it('stage → consume 幂等取走即清', () => {
    const store = useHudStore.getState();
    const envelope = buildEnvelope([{ action_id: 'a-1', kind: 'measure' }]);
    store.stageCopilotEnvelope(envelope);
    expect(useHudStore.getState().consumeStagedCopilotEnvelope()).not.toBeNull();
    expect(useHudStore.getState().consumeStagedCopilotEnvelope()).toBeNull();
  });

  it('clearCopilotState 复位工具/高亮/卡片/staged', () => {
    useHudStore.getState().setCopilotTool('box_select');
    useHudStore.getState().pushCopilotWidget(histogramSpec());
    useHudStore.getState().clearCopilotState();
    const s = useHudStore.getState();
    expect(s.copilotTool).toBeNull();
    expect(s.copilotWidgets).toHaveLength(0);
    expect(s.stagedCopilotEnvelope).toBeNull();
  });
});

/* ─── sendWidgetReply 直接回流 ─── */

describe('widget-binding', () => {
  it('sendWidgetReply 上报 widget_reply action 并触发本地回调', () => {
    const onReply = vi.fn();
    const result = sendWidgetReply(
      { widget_id: 'w-9', kind: 'candidate_picker' },
      { selected: ['c1'] },
      onReply,
    );
    expect(onReply).toHaveBeenCalledWith({ selected: ['c1'] });
    expect(result.dispatched).toBe(true);
    const fetchMock = vi.mocked(globalThis.fetch);
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(String(init?.body));
    expect(body.actions[0].kind).toBe('widget_reply');
    expect(body.actions[0].meta.widget_reply.value).toEqual({ selected: ['c1'] });
  });
});
