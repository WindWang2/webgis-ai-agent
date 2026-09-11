/**
 * 大屏值守模式测试（P7）—— 键盘可达 / 轮播开关与 reduced-motion 默认 /
 * 视图切换 / 退出。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Wallboard } from './wallboard';

function renderWallboard(onExit = vi.fn()) {
  return { onExit, ...render(<Wallboard ownerToken="tok" onExit={onExit} />) };
}

describe('Wallboard（P7 值守大屏）', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  it('三视图 tab + 默认自动轮播开启（无 reduced-motion 偏好时）', () => {
    renderWallboard();
    expect(screen.getByRole('region', { name: '集群值守大屏' })).toBeInTheDocument();
    expect(screen.getByTestId('wallboard-carousel-toggle')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('tab', { name: '集群总览' })).toHaveAttribute('aria-selected', 'true');
  });

  it('reduced-motion 偏好下自动轮播默认关闭', () => {
    (window as { matchMedia?: unknown }).matchMedia = vi.fn().mockReturnValue({ matches: true });
    renderWallboard();
    expect(screen.getByTestId('wallboard-carousel-toggle')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/reduced-motion/)).toBeInTheDocument();
  });

  it('键盘：→/← 切页、Space 开关轮播、Esc 退出', () => {
    const onExit = vi.fn();
    const { container } = render(<Wallboard ownerToken="tok" onExit={onExit} />);
    const region = screen.getByRole('region', { name: '集群值守大屏' });

    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '断路器 / 缓存' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '系统健康' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(region, { key: 'ArrowLeft' });
    expect(screen.getByRole('tab', { name: '断路器 / 缓存' })).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(region, { key: ' ' });
    expect(screen.getByTestId('wallboard-carousel-toggle')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.keyDown(region, { key: 'Escape' });
    expect(onExit).toHaveBeenCalledTimes(1);
    expect(container).toBeDefined();
  });

  it('Esc 在全屏中不退出（交给原生 fullscreen 退出）', () => {
    const onExit = vi.fn();
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => ({}) });
    render(<Wallboard ownerToken="tok" onExit={onExit} />);
    fireEvent.keyDown(screen.getByRole('region', { name: '集群值守大屏' }), { key: 'Escape' });
    expect(onExit).not.toHaveBeenCalled();
    delete (document as { fullscreenElement?: unknown }).fullscreenElement;
  });

  it('轮播 toggle 按钮可切换', () => {
    renderWallboard();
    const toggle = screen.getByTestId('wallboard-carousel-toggle');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('退出按钮调用 onExit', () => {
    const onExit = vi.fn();
    renderWallboard(onExit);
    fireEvent.click(screen.getByTestId('wallboard-exit'));
    expect(onExit).toHaveBeenCalledTimes(1);
  });
});
