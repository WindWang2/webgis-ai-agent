'use client';

/**
 * Agent 运行控制台（cockpit.v1）—— Harness 运行态可视化根容器。
 *
 * 挂在 nav rail「cockpit」tab 的 context panel（append-only 接缝，同 ops
 * 先例）。内部 tab 记忆不进全局 store（与 ops-console 同纪律）。
 *
 * 回答四个操作员问题：**正在做什么**（mission/执行）、**为什么**（技能投
 * 影 + Pi 上下文）、**卡在哪/证据**（blocked reason / frontier / claim）、
 * **资源**（配额/预留/背压）。全部读自后端 projection；operator 动作只
 * 调既有 mission-runtime 路由（两步确认，无乐观写）。
 */
import { useEffect, useState } from 'react';
import { ListChecks, Cpu, Lightbulb, GitBranch, Gauge, History } from 'lucide-react';
import { getCockpitHealth } from '@/lib/api/cockpit';
import { useT } from '@/lib/i18n/useT';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import { LiveDot } from './cockpit-shared';
import { MissionView } from './mission-view';
import { SwarmView, ResourcesView } from './execution-views';
import { SkillView, EvidenceView, TraceView } from './session-views';

const VIEWS = [
  { key: 'mission', icon: ListChecks },
  { key: 'execution', icon: Cpu },
  { key: 'skill', icon: Lightbulb },
  { key: 'evidence', icon: GitBranch },
  { key: 'resources', icon: Gauge },
  { key: 'trace', icon: History },
] as const;

type ViewKey = (typeof VIEWS)[number]['key'];

export function AgentOpsCockpit({
  sessionId,
  ownerToken,
}: {
  sessionId?: string | null;
  ownerToken?: string | null;
}) {
  const t = useT('cockpit');
  const reducedMotion = usePrefersReducedMotion();
  const [view, setView] = useState<ViewKey>('mission');
  const [health, setHealth] = useState<'checking' | 'enabled' | 'disabled' | 'error'>('checking');
  // 选中的 mission 由 root 持有：mission 视图选中，execution/resources 视图
  // 共享同一目标（受控下传，无事件桥接、无全局 store）。
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(null);

  // 健康探测：一次挂载级请求（不轮询）—— 决定整个投影面的可用性。
  // 卸载时 abort；组件重挂载（tab 切换）会重新探测，拿到的开关永远新鲜。
  useEffect(() => {
    const controller = new AbortController();
    setHealth('checking');
    getCockpitHealth(controller.signal)
      .then((res) => setHealth(res.enabled ? 'enabled' : 'disabled'))
      .catch(() => setHealth('error'));
    return () => controller.abort();
  }, []);

  if (health !== 'enabled') {
    return (
      <div className="flex min-h-0 flex-1 flex-col" data-testid="cockpit-root">
        <header className="pb-2">
          <p className="text-meta font-medium text-ink">{t('title')}</p>
          <p className="text-meta text-ink-muted">{t('subtitle')}</p>
        </header>
        <div
          data-testid={health === 'disabled' ? 'cockpit-disabled' : health === 'error' ? 'cockpit-health-error' : 'cockpit-health-pending'}
          role="status"
          aria-live="polite"
          className="flex flex-1 items-center justify-center rounded-sm border border-dashed border-edge-subtle px-3 py-6 text-center text-meta text-ink-secondary"
        >
          {health === 'disabled' ? t('disabled') : health === 'error' ? t('healthCheckFailed') : '…'}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="cockpit-root">
      <header className="flex items-center justify-between gap-2 border-b border-edge-subtle px-1 pb-2">
        <p className="text-meta font-medium text-ink">{t('title')}</p>
        <LiveDot active reducedMotion={reducedMotion} />
      </header>

      {/* 视图切换（role=tablist，与 ops-console 同款） */}
      <div role="tablist" aria-label={t('title')} className="flex items-center gap-0.5 overflow-x-auto py-1">
        {VIEWS.map(({ key, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={view === key}
            data-testid={`cockpit-view-${key}`}
            onClick={() => setView(key)}
            className={`flex shrink-0 items-center gap-1 rounded-sm px-2 py-1 text-meta font-medium transition-colors ${
              view === key
                ? 'bg-status-accent-soft text-status-accent'
                : 'text-ink-secondary hover:bg-surface-hover'
            }`}
          >
            <Icon aria-hidden className="h-3.5 w-3.5" />
            {t(`view.${key}`)}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-1">
        {view === 'mission' && (
          <MissionView
            enabled
            ownerToken={ownerToken}
            selectedId={selectedMissionId}
            onSelect={setSelectedMissionId}
          />
        )}
        {view === 'execution' && (
          <SwarmView enabled missionId={selectedMissionId} ownerToken={ownerToken} />
        )}
        {view === 'resources' && (
          <ResourcesView enabled missionId={selectedMissionId} ownerToken={ownerToken} />
        )}
        {view === 'skill' && (
          <SkillView enabled sessionId={sessionId} ownerToken={ownerToken} />
        )}
        {view === 'evidence' && (
          <EvidenceView enabled sessionId={sessionId} ownerToken={ownerToken} />
        )}
        {view === 'trace' && (
          <TraceView enabled sessionId={sessionId} ownerToken={ownerToken} />
        )}
      </div>
    </div>
  );
}
