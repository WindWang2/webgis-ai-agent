'use client';

/**
 * 运维控制台根容器（ADR-0142 D1）—— 挂在 nav rail「运维」tab 的 context panel。
 *
 * 视图分区：集群（P3）/ 计划（P4）/ 运行时（P1 runtime-inspector 复活）/
 * 断路器（P6）/ 系统（P5 复用设置面板的同一组件）。大屏值守（P7）经头部
 * 按钮进入全屏覆盖层。内部 tab 记忆不进全局 store（运维面不参与模式协调）。
 */
import { useState } from 'react';
import { Activity, GitPullRequestArrow, Cpu, Zap, HeartPulse, MonitorPlay } from 'lucide-react';
import { ClusterDashboard } from './cluster-dashboard';
import { PlanConsole } from './plan-console';
import { RuntimeSection } from './runtime-section';
import { BreakerPanel } from './breaker-panel';
import { SystemHealthPanel } from './system-health-panel';
import { Wallboard } from './wallboard';

const VIEWS = [
  { key: 'cluster', label: '集群', icon: Activity },
  { key: 'plan', label: '计划', icon: GitPullRequestArrow },
  { key: 'runtime', label: '运行时', icon: Cpu },
  { key: 'breaker', label: '断路器', icon: Zap },
  { key: 'health', label: '系统', icon: HeartPulse },
] as const;

type ViewKey = (typeof VIEWS)[number]['key'];

export function OpsConsole({
  sessionId,
  ownerToken,
}: {
  sessionId?: string | null;
  ownerToken?: string | null;
}) {
  const [view, setView] = useState<ViewKey>('cluster');
  const [wallboard, setWallboard] = useState(false);

  if (wallboard) {
    return <Wallboard ownerToken={ownerToken} onExit={() => setWallboard(false)} />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="ops-console">
      {/* 分区切换 + 大屏入口 */}
      <div className="flex items-center justify-between gap-2 border-b border-edge-subtle px-1 pb-2">
        <div role="tablist" aria-label="运维视图" className="flex items-center gap-0.5 overflow-x-auto">
          {VIEWS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              data-testid={`ops-view-${key}`}
              onClick={() => setView(key)}
              className={`flex shrink-0 items-center gap-1 rounded-sm px-2 py-1 text-meta font-medium transition-colors ${
                view === key
                  ? 'bg-status-accent-soft text-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover'
              }`}
            >
              <Icon size={12} aria-hidden />
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          data-testid="ops-wallboard-enter"
          onClick={() => setWallboard(true)}
          aria-label="进入大屏值守模式"
          title="大屏值守（W）"
          className="flex shrink-0 items-center gap-1 rounded-sm border border-edge-subtle px-2 py-1 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
        >
          <MonitorPlay size={12} aria-hidden />
          大屏
        </button>
      </div>

      {/* 当前分区 */}
      <div className="min-h-0 flex-1 overflow-y-auto pt-3">
        {view === 'cluster' && <ClusterDashboard ownerToken={ownerToken} />}
        {view === 'plan' && <PlanConsole ownerToken={ownerToken} sessionId={sessionId} />}
        {view === 'runtime' && <RuntimeSection ownerToken={ownerToken} sessionId={sessionId} />}
        {view === 'breaker' && <BreakerPanel />}
        {view === 'health' && <SystemHealthPanel ownerToken={ownerToken} />}
      </div>
    </div>
  );
}

export default OpsConsole;
