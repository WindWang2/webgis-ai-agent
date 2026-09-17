'use client';

/**
 * 会话热路径视图：SkillPolicy 投影 / Evidence-Claim 投影 / runtime trace
 * 回放。全部挂在活跃 chat session 上（require_owned_session 归属证明链，
 * ownerToken → X-Session-Token）；session 切换即换投影（resetKey）。
 *
 * 诚实披露：这三个面是**进程内**数据 —— 后端重启即清空，绝不伪装持久化。
 * trace 只含服务端已截断的结构化决策证据（无思维链/密钥）。
 */
import {
  useCockpitProjection,
} from '@/lib/cockpit/use-cockpit-projection';
import {
  getCockpitSessionSkill,
  getCockpitSessionEvidence,
  getCockpitSessionTrace,
} from '@/lib/api/cockpit';
import { useT } from '@/lib/i18n/useT';
import { StatusBadge, Kv, EmptyState, VirtualList } from './cockpit-shared';

export interface SessionViewProps {
  enabled: boolean;
  sessionId?: string | null;
  ownerToken?: string | null;
}

export function SkillView({ enabled, sessionId, ownerToken }: SessionViewProps) {
  const t = useT('cockpit');
  const skill = useCockpitProjection(
    (signal) =>
      getCockpitSessionSkill(sessionId as string, { signal, ownerToken }),
    { enabled: enabled && sessionId != null, resetKey: sessionId ?? 'none' },
  );

  if (sessionId == null) {
    return <EmptyState testId="cockpit-skill-no-session">{t('empty.noSession')}</EmptyState>;
  }
  const g = skill.data?.guidance ?? null;
  if (!skill.data?.present || !g) {
    return <EmptyState testId="cockpit-skill-absent">{t('skill.absent')}</EmptyState>;
  }
  const decision = (g.decision ?? {}) as Record<string, unknown>;
  const selected = typeof decision.selected_skill === 'string' ? decision.selected_skill : null;
  const confidence = typeof decision.confidence === 'number' ? decision.confidence : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5" data-testid="cockpit-skill-view">
      {selected ? (
        <p className="text-meta font-medium text-ink" data-testid="cockpit-skill-selected">
          {t('skill.selected')}: <span className="text-status-accent">{selected}</span>
        </p>
      ) : (
        <EmptyState testId="cockpit-skill-absent">{t('skill.absent')}</EmptyState>
      )}
      {confidence !== null ? (
        <Kv label={t('skill.confidence')}>{confidence.toFixed(2)}</Kv>
      ) : null}
      <Kv label={t('skill.guidesPlanning')}>
        {g.guides_planning ? t('skill.yes') : t('skill.no')}
      </Kv>
      {g.shadow ? (
        <Kv label={t('skill.shadow')}>
          <span className="break-all">{JSON.stringify(g.shadow)}</span>
        </Kv>
      ) : null}
      <p className="text-meta text-ink-muted">{t('mission.processLocalNote')}</p>
    </div>
  );
}

export function EvidenceView({ enabled, sessionId, ownerToken }: SessionViewProps) {
  const t = useT('cockpit');
  const evidence = useCockpitProjection(
    (signal) =>
      getCockpitSessionEvidence(sessionId as string, { signal, ownerToken, limit: 200 }),
    { enabled: enabled && sessionId != null, resetKey: sessionId ?? 'none' },
  );

  if (sessionId == null) {
    return <EmptyState testId="cockpit-evidence-no-session">{t('empty.noSession')}</EmptyState>;
  }
  if (!evidence.data?.present) {
    return <EmptyState testId="cockpit-evidence-absent">{t('evidence.absent')}</EmptyState>;
  }
  const claims = evidence.data.claims;
  const edges = evidence.data.edges;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5" data-testid="cockpit-evidence-view">
      <p className="text-meta text-ink-muted">
        {t('evidence.claims')} × {claims.length} · {t('evidence.edges')} × {edges.length}
        {evidence.data.truncated ? ` · ${t('evidence.truncated')}` : ''}
      </p>
      <VirtualList
        items={claims}
        rowHeight={64}
        rowTestId="cockpit-claim-row"
        ariaLabel={t('evidence.claims')}
        testId="cockpit-claims-virtual"
        renderRow={(claim) => (
          <div className="flex h-full flex-col justify-center gap-0.5 border-b border-edge-subtle px-1 text-meta">
            <span className="flex items-center gap-2">
              <StatusBadge state={claim.status} label={t(`claimStatus.${claim.status}`)} />
              <span className="min-w-0 truncate font-medium text-ink">{claim.subject || claim.claim_id}</span>
              {claim.contradicting_evidence_refs.length > 0 ? (
                <span className="text-status-critical">⚠ {t('evidence.contradicts')}</span>
              ) : null}
            </span>
            <span className="text-ink-muted">
              {claim.claim_type}
              {claim.value !== null ? ` · ${claim.value} ${claim.unit}` : ''}
              {claim.confidence !== null ? ` · ${t('evidence.confidence')} ${claim.confidence.toFixed(2)}` : ''}
              {' · '}{t('evidence.supports')} ×{claim.supporting_evidence_refs.length}
            </span>
          </div>
        )}
      />
    </div>
  );
}

export function TraceView({ enabled, sessionId, ownerToken }: SessionViewProps) {
  const t = useT('cockpit');
  const trace = useCockpitProjection(
    (signal) =>
      getCockpitSessionTrace(sessionId as string, { signal, ownerToken, limit: 64 }),
    { enabled: enabled && sessionId != null, resetKey: sessionId ?? 'none' },
  );

  if (sessionId == null) {
    return <EmptyState testId="cockpit-trace-no-session">{t('empty.noSession')}</EmptyState>;
  }
  const events = trace.data?.events ?? [];
  const counters = Object.entries(trace.data?.counters ?? {});

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5" data-testid="cockpit-trace-view">
      <p className="text-meta text-ink-muted">
        {t('trace.events')} × <span data-testid="cockpit-trace-total">{events.length}</span>
        {' · '}{t('trace.noSecrets')}
      </p>
      {events.length === 0 ? (
        <EmptyState testId="cockpit-trace-empty">{t('empty.noData')}</EmptyState>
      ) : (
        <VirtualList
          items={events}
          rowHeight={32}
          rowTestId="cockpit-trace-row"
          ariaLabel={t('trace.events')}
          testId="cockpit-trace-virtual"
          renderRow={(ev) => (
            <div className="flex h-full items-center gap-2 border-b border-edge-subtle px-1 text-meta">
              <span className="w-16 shrink-0 text-ink-muted">{new Date(ev.ts * 1000).toLocaleTimeString()}</span>
              <StatusBadge state={ev.stage} label={ev.stage} />
              <span className="min-w-0 truncate text-ink-secondary">
                {Object.entries(ev.detail).map(([k, v]) => `${k}=${String(v)}`).join(' · ')}
              </span>
            </div>
          )}
        />
      )}
      {counters.length > 0 ? (
        <p className="text-meta text-ink-muted">
          {t('trace.counters')}: {counters.map(([k, v]) => `${k}=${v}`).join(' · ')}
        </p>
      ) : null}
    </div>
  );
}
