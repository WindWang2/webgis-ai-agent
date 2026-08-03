import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RenderDebouncer, type RenderOperation } from './render-debouncer';

describe('RenderDebouncer', () => {
  let mockMap: any;

  beforeEach(() => {
    mockMap = {
      setPaintProperty: vi.fn(),
      setLayoutProperty: vi.fn(),
      removeLayer: vi.fn(),
      getLayer: vi.fn().mockReturnValue({}),
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('coalesces duplicate operations with the same ID', () => {
    const debouncer = new RenderDebouncer(mockMap);
    const op1: RenderOperation = {
      id: 'paint:layer1:opacity',
      type: 'SET_PAINT',
      execute: vi.fn(),
    };
    const op2: RenderOperation = {
      id: 'paint:layer1:opacity',
      type: 'SET_PAINT',
      execute: vi.fn(),
    };

    debouncer.enqueue(op1);
    debouncer.enqueue(op2);

    expect(debouncer.pendingCount()).toBe(1);
    debouncer.flush();
    expect(op1.execute).not.toHaveBeenCalled();
    expect(op2.execute).toHaveBeenCalledWith(mockMap);
  });

  it('executes high priority operations before normal priority operations', () => {
    const executionOrder: string[] = [];
    const debouncer = new RenderDebouncer(mockMap);

    const normalOp: RenderOperation = {
      id: 'normal:1',
      type: 'SET_PAINT',
      priority: 'normal',
      execute: () => executionOrder.push('normal'),
    };

    const highOp: RenderOperation = {
      id: 'high:1',
      type: 'REMOVE_LAYER',
      priority: 'high',
      execute: () => executionOrder.push('high'),
    };

    debouncer.enqueue(normalOp);
    debouncer.enqueue(highOp);

    debouncer.flush();
    expect(executionOrder).toEqual(['high', 'normal']);
  });

  it('flushes pending queue immediately when flush() is called', () => {
    const executed: string[] = [];
    const debouncer = new RenderDebouncer(mockMap);

    debouncer.enqueue({
      id: 'op1',
      type: 'SET_PAINT',
      execute: () => executed.push('op1'),
    });

    debouncer.flush();
    expect(executed).toContain('op1');
    expect(debouncer.pendingCount()).toBe(0);
  });

  it('handles dispose safely', () => {
    const debouncer = new RenderDebouncer(mockMap);
    const op: RenderOperation = {
      id: 'op1',
      type: 'SET_PAINT',
      execute: vi.fn(),
    };

    debouncer.dispose();
    debouncer.enqueue(op);
    expect(debouncer.pendingCount()).toBe(0);
  });
});
