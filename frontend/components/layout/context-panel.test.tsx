import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

/**
 * ContextPanel (UI V3) — 统一上下文面板。
 *
 * Pin 的契约：
 *   - role=tabpanel + PanelHeader（title/description/badge/close）；
 *   - tab 切换即卸载（只渲染 active tab 的内容）；
 *   - 折叠时 aria-hidden + visibility 隐藏；
 *   - 右缘 separator 键盘调宽（280–420 clamp）+ 双击复位；
 *   - 拖拽调宽：pointermove 只走命令式草稿（CSS 变量 + aria），全程零
 *     全局 store 写；终止（up/cancel/lostcapture/blur/折叠）恰好提交一次。
 */

const counters = vi.hoisted(() => ({ chatRenders: 0 }));

/**
 * jsdom 没有 PointerEvent 构造器，fireEvent.pointer* 会退化成裸 Event ——
 * pointerId/clientX/button 等 init 属性全部丢失（探测验证：handler 触发但
 * 属性全 undefined）。补一个基于 MouseEvent 的最小 polyfill，让拖拽契约
 * 可以在 jsdom 里确定性验证。
 */
if (typeof window.PointerEvent !== 'function') {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    pointerType: string;
    isPrimary: boolean;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? '';
      this.isPrimary = init.isPrimary ?? false;
    }
  }
  // @ts-expect-error -- 补齐 jsdom 缺失的 DOM API 形状
  window.PointerEvent = PointerEventPolyfill;
}

const toggleLeftPanel = vi.fn();
const setSidebarWidth = vi.fn();

const store: Record<string, unknown> = {
  activeLeftTab: 'chat',
  leftPanelOpen: true,
  toggleLeftPanel,
  sidebarWidth: 320,
  setSidebarWidth,
  layers: [],
  exports: [],
  results: [],
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(store),
}));

// 重依赖 tab 全部打桩，只验证 ContextPanel 自身的编排逻辑。
// ChatTab 带渲染计数 —— 拖拽性能断言用（活动 tab 不得逐 pointermove 重渲染）。
vi.mock('@/components/sidebar/chat-tab', () => ({
  ChatTab: () => {
    counters.chatRenders += 1;
    return <div data-testid="tab-chat" />;
  },
}));
vi.mock('@/components/sidebar/project-tab', () => ({ ProjectTab: () => <div data-testid="tab-project" /> }));
vi.mock('@/components/sidebar/layers-tab', () => ({ LayersTab: () => <div data-testid="tab-layers" /> }));
vi.mock('@/components/sidebar/analysis-tab', () => ({ AnalysisTab: () => <div data-testid="tab-analysis" /> }));
vi.mock('@/components/sidebar/data-sources-tab', () => ({ DataSourcesTab: () => <div data-testid="tab-data-sources" /> }));
vi.mock('@/components/sidebar/map-studio-tab', () => ({ MapStudioTab: () => <div data-testid="tab-map-studio" /> }));
vi.mock('@/components/sidebar/tasks-tab', () => ({ TasksTab: () => <div data-testid="tab-tasks" /> }));
vi.mock('@/components/sidebar/results-tab', () => ({ ResultsTab: () => <div data-testid="tab-results" /> }));

// Import AFTER the mocks are registered.
import { ContextPanel } from './context-panel';

const baseProps = {
  messages: [],
  aiStatus: 'idle' as const,
  onSend: vi.fn(),
};

/** 同步执行的 rAF 桩 —— 草稿宽度断言不依赖真实帧时序。 */
function stubRafSync() {
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0);
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', () => {});
}

/** 在 separator 上执行一次完整拖拽的 pointer 序列（不释放）。 */
function dragTo(clientX: number, { pointerId = 1, startX = 100 } = {}) {
  const separator = screen.getByRole('separator', { name: '调整面板宽度' });
  fireEvent.pointerDown(separator, { pointerId, button: 0, clientX: startX, clientY: 50 });
  fireEvent.pointerMove(separator, { pointerId, clientX, clientY: 50 });
  return separator;
}

describe('ContextPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    counters.chatRenders = 0;
    store.activeLeftTab = 'chat';
    store.leftPanelOpen = true;
    store.sidebarWidth = 320;
    store.layers = [];
    store.exports = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders tabpanel with PanelHeader meta for the active tab', () => {
    render(<ContextPanel {...baseProps} />);

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveAttribute('id', 'workspace-panel');
    expect(panel).toHaveAttribute('aria-labelledby', 'rail-tab-chat');

    expect(screen.getByText('对话')).toBeInTheDocument();
    expect(screen.getByText('AI 地理智能体')).toBeInTheDocument();
    expect(screen.getByTestId('tab-chat')).toBeInTheDocument();
    // 切换即卸载：其它 tab 内容不渲染
    expect(screen.queryByTestId('tab-layers')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tab-tasks')).not.toBeInTheDocument();
  });

  it('switches content + header meta when the active tab changes', () => {
    store.activeLeftTab = 'layers';
    store.layers = [{ id: 'L1' }, { id: 'L2' }];
    render(<ContextPanel {...baseProps} />);

    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'rail-tab-layers');
    expect(screen.getByText('图层')).toBeInTheDocument();
    // 徽标 = 图层数
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByTestId('tab-layers')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-chat')).not.toBeInTheDocument();
  });

  it('maps legacy exports tab onto the map studio panel', () => {
    store.activeLeftTab = 'exports';
    render(<ContextPanel {...baseProps} />);
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'rail-tab-export_layout');
    expect(screen.getByText('制图工坊')).toBeInTheDocument();
    expect(screen.getByTestId('tab-map-studio')).toBeInTheDocument();
  });

  it('close button collapses the panel', () => {
    render(<ContextPanel {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: '收起面板' }));
    expect(toggleLeftPanel).toHaveBeenCalledTimes(1);
  });

  it('is aria-hidden when collapsed', () => {
    store.leftPanelOpen = false;
    render(<ContextPanel {...baseProps} />);
    expect(screen.getByRole('tabpanel', { hidden: true })).toHaveAttribute('aria-hidden', 'true');
  });

  it('separator keyboard arrows resize within 280–420 clamp', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    expect(separator).toHaveAttribute('aria-valuenow', '320');

    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(setSidebarWidth).toHaveBeenCalledWith(336);

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(setSidebarWidth).toHaveBeenCalledWith(304);
  });

  it('separator clamps at the max width', () => {
    store.sidebarWidth = 420;
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(setSidebarWidth).toHaveBeenCalledWith(420);
  });

  it('double-click resets the width to the 320 default', () => {
    store.sidebarWidth = 400;
    render(<ContextPanel {...baseProps} />);
    fireEvent.doubleClick(screen.getByRole('separator', { name: '调整面板宽度' }));
    expect(setSidebarWidth).toHaveBeenCalledWith(320);
  });
});

describe('ContextPanel resize drag (perf + lifecycle contract)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    counters.chatRenders = 0;
    store.activeLeftTab = 'chat';
    store.leftPanelOpen = true;
    store.sidebarWidth = 320;
    store.layers = [];
    store.exports = [];
    stubRafSync();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('PERF: 100 pointer moves → 0 store writes while dragging, exactly 1 commit on release', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });

    fireEvent.pointerDown(separator, { pointerId: 1, button: 0, clientX: 100, clientY: 50 });
    const rendersAtDragStart = counters.chatRenders; // 含 dragging=true 的一次重渲染
    for (let i = 1; i <= 100; i += 1) {
      fireEvent.pointerMove(separator, { pointerId: 1, clientX: 100 + i * 2, clientY: 50 });
    }

    // 拖拽全程：零全局宽度提交，活动 tab 零额外渲染
    expect(setSidebarWidth).not.toHaveBeenCalled();
    expect(counters.chatRenders).toBe(rendersAtDragStart);

    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 300, clientY: 50 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    // 320 + 200 → clamp 到 420
    expect(setSidebarWidth).toHaveBeenCalledWith(420);
  });

  it('PERF: draft width is applied imperatively and clamped to the min bound', () => {
    render(<ContextPanel {...baseProps} />);
    const panel = screen.getByRole('tabpanel');
    const separator = dragTo(100 - 90); // 320 - 90 → clamp 280

    expect(panel.style.getPropertyValue('--panel-draft-w')).toBe('280px');
    expect(separator).toHaveAttribute('aria-valuenow', '280');
    expect(counters.chatRenders).toBe(2); // 初次渲染 + dragStart，无逐 move 渲染

    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 10, clientY: 50 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(280);
  });

  it('a plain click on the handle (no movement) commits nothing', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    fireEvent.pointerDown(separator, { pointerId: 1, button: 0, clientX: 100, clientY: 50 });
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 100, clientY: 50 });
    expect(setSidebarWidth).not.toHaveBeenCalled();
  });

  it('non-primary buttons never start a drag', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    fireEvent.pointerDown(separator, { pointerId: 1, button: 2, clientX: 100, clientY: 50 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 260, clientY: 50 });
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 260, clientY: 50 });
    expect(setSidebarWidth).not.toHaveBeenCalled();
  });

  it('STAB: pointercancel ends the drag and commits the visible draft once', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = dragTo(140); // 360
    fireEvent.pointerCancel(separator, { pointerId: 1 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(360);

    // 终止后残余事件不再有任何写入
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 600, clientY: 50 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
  });

  it('STAB: lostpointercapture ends the drag and commits once', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = dragTo(120); // 340
    // jsdom 无 PointerEvent 构造器 —— 裸 Event 上补 pointerId
    // （真实浏览器里 lostpointercapture 天然携带）
    const ev = new Event('lostpointercapture', { bubbles: true });
    Object.defineProperty(ev, 'pointerId', { value: 1 });
    fireEvent(separator, ev);
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(340);
  });

  it('STAB: window blur ends the drag (capture 语义不可靠) and commits once', () => {
    render(<ContextPanel {...baseProps} />);
    dragTo(160); // 380
    act(() => {
      fireEvent(window, new Event('blur'));
    });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(380);
  });

  it('STAB: collapsing the panel mid-drag terminates and commits the draft', () => {
    const { rerender } = render(<ContextPanel {...baseProps} />);
    dragTo(150); // 370

    store.leftPanelOpen = false;
    rerender(<ContextPanel {...baseProps} />);

    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(370);
  });

  it('STAB: unmounting mid-drag leaves no listener and commits nothing', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = render(<ContextPanel {...baseProps} />);
    dragTo(130); // 350

    unmount();

    expect(setSidebarWidth).not.toHaveBeenCalled(); // 卸载后不写 store
    // jsdom 无 pointer capture → 兜底 window 监听也必须被摘除
    expect(removeSpy).toHaveBeenCalledWith('blur', expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith('pointermove', expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith('pointerup', expect.any(Function));
    removeSpy.mockRestore();

    // 卸载后派发的事件不再触发任何提交
    act(() => {
      fireEvent(window, new Event('blur'));
      fireEvent(window, new Event('pointermove'));
    });
    expect(setSidebarWidth).not.toHaveBeenCalled();
  });

  it('STAB: rapid drag start/stop cycles stay idempotent', () => {
    render(<ContextPanel {...baseProps} />);
    // 每轮都从 320 起（mock store 不回写）：330 / 350 提交，第三轮回到 320
    // —— 与起点相同 ⇒ 不提交，验证“变化才提交”语义
    const targets = [110, 130, 100];
    targets.forEach((clientX, i) => {
      const separator = dragTo(clientX, { pointerId: i + 1 });
      fireEvent.pointerUp(separator, { pointerId: i + 1 });
    });
    expect(setSidebarWidth).toHaveBeenCalledTimes(2);
    expect(setSidebarWidth).toHaveBeenNthCalledWith(1, 330);
    expect(setSidebarWidth).toHaveBeenNthCalledWith(2, 350);
  });

  it('STAB: a second pointerdown during an active drag is ignored (no draft hijack)', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = dragTo(120, { pointerId: 1 }); // 340
    // 第二根手指落在手柄上：不得覆盖在途拖拽
    fireEvent.pointerDown(separator, { pointerId: 2, button: 0, clientX: 500, clientY: 50 });
    fireEvent.pointerMove(separator, { pointerId: 2, clientX: 560, clientY: 50 });
    // 原指针释放：按第一指针的草稿恰好提交一次
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 120, clientY: 50 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(340);
  });

  it('STAB: keyboard arrows during an active drag fold into the draft (single commit)', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = dragTo(120, { pointerId: 1 }); // 340
    fireEvent.keyDown(separator, { key: 'ArrowRight' }); // 356
    fireEvent.keyDown(separator, { key: 'ArrowRight' }); // 372
    expect(setSidebarWidth).not.toHaveBeenCalled(); // 拖拽中零直写
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 120, clientY: 50 });
    expect(setSidebarWidth).toHaveBeenCalledTimes(1);
    expect(setSidebarWidth).toHaveBeenCalledWith(372);
  });

  it('A11Y: closing the panel returns focus to the corresponding rail tab', () => {
    const railTab = document.createElement('button');
    railTab.id = 'rail-tab-chat';
    document.body.appendChild(railTab);

    const { rerender } = render(<ContextPanel {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: '收起面板' }));
    expect(toggleLeftPanel).toHaveBeenCalledTimes(1);

    // store mock 不会翻转 leftPanelOpen —— 手动模拟 store 提交后的重渲染；
    // effect 在面板隐藏落地后归还焦点（无 setTimeout 竞态）
    store.leftPanelOpen = false;
    rerender(<ContextPanel {...baseProps} />);
    expect(railTab).toHaveFocus();

    document.body.removeChild(railTab);
  });

  it('A11Y: focus restore does not steal focus the user already moved elsewhere', () => {
    const railTab = document.createElement('button');
    railTab.id = 'rail-tab-chat';
    const elsewhere = document.createElement('button');
    elsewhere.id = 'somewhere-else';
    document.body.append(railTab, elsewhere);
    elsewhere.focus();

    const { rerender } = render(<ContextPanel {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: '收起面板' }));
    store.leftPanelOpen = false;
    rerender(<ContextPanel {...baseProps} />);

    expect(elsewhere).toHaveFocus();
    expect(railTab).not.toHaveFocus();

    document.body.removeChild(railTab);
    document.body.removeChild(elsewhere);
  });
});
