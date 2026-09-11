/**
 * 断路器面板三态测试（P6，§5 门禁：closed/open/half_open 可视 + 视觉快照）。
 * DOM 快照作为明暗主题无关的结构取证；明暗视觉由 Playwright capture 兜底。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BreakerPanel } from './breaker-panel';
import {
  recordFabricDisclosure,
  resetFabricDisclosureForTests,
} from '@/lib/api/data-fabric-disclosure';
import {
  breakerClosedFixture,
  breakerOpenFixture,
  breakerHalfOpenFixture,
  cacheLocalHitFixture,
} from '@/test/fixtures/health-fixtures';

/** 序列化当前 DOM 并把时刻（HH:MM:SS）打码 —— 快照跨时区/运行时刻确定。 */
function snapshotDom(container: HTMLElement): string {
  return container.innerHTML.replace(/\d{1,2}:\d{2}:\d{2}/g, 'HH:MM:SS');
}

describe('BreakerPanel（披露留存型只读面板）', () => {
  beforeEach(() => resetFabricDisclosureForTests());

  it('从未收到披露 → 诚实空态（无假数据）', () => {
    const { container } = render(<BreakerPanel />);
    expect(screen.getByText('尚未收到任何断路器披露')).toBeInTheDocument();
    expect(screen.getByText('尚未收到缓存披露')).toBeInTheDocument();
    expect(screen.getByText(/无独立端点（协调点）/)).toBeInTheDocument();
    expect(snapshotDom(container)).toMatchSnapshot('breaker-empty');
  });

  it('closed 态可视：闭合（正常）', () => {
    recordFabricDisclosure({ engine_breaker: breakerClosedFixture });
    const { container } = render(<BreakerPanel />);
    expect(screen.getByText('闭合（正常）')).toBeInTheDocument();
    expect(screen.getByText('0/3')).toBeInTheDocument();
    expect(snapshotDom(container)).toMatchSnapshot('breaker-closed');
  });

  it('open 态可视：断开（熔断）+ 冷却剩余', () => {
    recordFabricDisclosure({ engine_breaker: breakerOpenFixture });
    const { container } = render(<BreakerPanel />);
    expect(screen.getByText('断开（熔断）')).toBeInTheDocument();
    expect(screen.getByText('3/3')).toBeInTheDocument();
    expect(screen.getByText('累计回退')).toBeInTheDocument();
    expect(screen.getByText('半开冷却剩余')).toBeInTheDocument();
    expect(snapshotDom(container)).toMatchSnapshot('breaker-open');
  });

  it('half_open 态可视：半开（试验中）', () => {
    recordFabricDisclosure({ engine_breaker: breakerHalfOpenFixture });
    const { container } = render(<BreakerPanel />);
    expect(screen.getByText('半开（试验中）')).toBeInTheDocument();
    expect(screen.getByText('试验中')).toBeInTheDocument();
    expect(snapshotDom(container)).toMatchSnapshot('breaker-half-open');
  });

  it('缓存披露展示 basis 口径（ttl+fingerprint / +distributed）', () => {
    recordFabricDisclosure({ result_cache: cacheLocalHitFixture });
    render(<BreakerPanel />);
    expect(screen.getByText('ttl+fingerprint')).toBeInTheDocument();
    expect(screen.getByText('hit')).toBeInTheDocument();
  });

  it('stats() 聚合无端点 —— 如实标注不能虚构命中率曲线', () => {
    render(<BreakerPanel />);
    expect(screen.getByText(/不能虚构命中率曲线/)).toBeInTheDocument();
  });
});
