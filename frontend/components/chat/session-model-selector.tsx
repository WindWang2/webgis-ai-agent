'use client';

/**
 * G37（会话模型运行配置）：会话内模型选择与用量工作台（WP11）。
 *
 * 事实纪律：
 * - 展示的「实际生效」读服务端回执（GET /chat/sessions/{id}/model 的
 *   last_turn = 真实 get_state 回读），SSE model_status 只作实时旁证；
 * - 不可用原因、coverage、费用来源（provider/estimated/unknown）如实
 *   分层，unknown 绝不显示成“免费”；
 * - 选择只经 PUT（CAS revision）—— 409 冲突自动刷新重试提示；localStorage
 *   的模型名永远不是生效事实。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { apiFetch, isApiError, describeApiError } from '@/lib/api/transport';
import { useModelRuntimeStore } from '@/lib/store/useModelRuntimeStore';

interface CatalogCapabilities {
  context_window: number | null;
  max_output: number | null;
  tool_calling: boolean | null;
  reasoning: boolean | null;
  vision: boolean | null;
  cost_class: string;
  source: string;
}

interface CatalogItem {
  id: string;
  provider: string;
  is_default: boolean;
  available: boolean;
  unavailable_reason: string | null;
  capabilities: CatalogCapabilities;
}

interface ModelView {
  catalog_generation: string;
  catalog: CatalogItem[];
  preference: {
    mode: string;
    model_id: string | null;
    thinking_level: string | null;
    session_token_cap: number | null;
    revision: number;
  };
  target: {
    ok: boolean;
    reason_code: string | null;
    reason_detail: string | null;
    model_id: string | null;
  };
  last_turn: {
    turn_id: string;
    status: string;
    mode: string;
    requested_model: string;
    effective_model: string | null;
    thinking_requested: string | null;
    thinking_effective: string | null;
    response_model: string | null;
    created_at: string | null;
  } | null;
  pending_next_turn: boolean;
  thinking_controllable: boolean;
}

interface UsageView {
  totals: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  turns_recorded: number;
  turns_incomplete: number;
  cost_total_micro_usd: number | null;
  cost_source: string;
  coverage: { included: string[]; not_included: string[] };
  budget?: { token_cap: number | null; remaining: number | null; used?: number | null };
  /** WP4（GOAL-20261127-01）：attempt 链投影（有界；缺省 = 旧后端）。 */
  recent_turns?: Array<{
    provider_retry_attempts?: number;
    route_reason_codes?: string[];
    last_provider_failure_kind?: string | null;
  }>;
}

const UNAVAILABLE_REASON_KEYS: Record<string, string> = {
  credential_missing: 'chat.model.reasonCredential',
  invalid_id: 'chat.model.reasonInvalid',
};

export function SessionModelSelector({
  sessionId,
  ownerToken,
  turnActive,
}: {
  sessionId: string | null;
  ownerToken?: string | null;
  turnActive: boolean;
}) {
  const t = useT();
  const [view, setView] = useState<ModelView | null>(null);
  const [usage, setUsage] = useState<UsageView | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  /** R2 #2191：load() 代次 —— 会话切换后旧 /model /usage 响应不得回写。 */
  const loadGenRef = useRef(0);
  const liveStatus = useModelRuntimeStore(
    (s) => (sessionId ? s.bySession[sessionId] ?? null : null),
  );

  const load = useCallback(async () => {
    if (!sessionId) return;
    // R2 #2191：代次守卫 —— 慢的 /model、/usage 响应不得覆盖新会话的徽标。
    const gen = ++loadGenRef.current;
    try {
      const data = await apiFetch<ModelView>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/model`,
        { label: 'session model view load', ownerToken },
      );
      if (loadGenRef.current !== gen) return;
      setView(data);
    } catch {
      if (loadGenRef.current !== gen) return;
      setView(null); // 会话不可读（新建/权限）→ 整条隐藏，不伪造状态
    }
    try {
      const u = await apiFetch<UsageView>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/usage`,
        { label: 'session usage load', ownerToken },
      );
      if (loadGenRef.current !== gen) return;
      setUsage(u);
    } catch {
      if (loadGenRef.current !== gen) return;
      setUsage(null);
    }
  }, [sessionId, ownerToken]);

  // 会话切换：立刻清旧视图并令在飞响应失效（旧 /model /usage 不得回写）。
  useEffect(() => {
    loadGenRef.current += 1;
    setView(null);
    setUsage(null);
    setNotice(null);
    setBusy(false);
  }, [sessionId]);

  useEffect(() => {
    load();
  }, [load]);

  // turn 结束（turnActive 翻转 false）→ 刷新回执/用量
  const prevActive = React.useRef(turnActive);
  useEffect(() => {
    if (prevActive.current && !turnActive) load();
    prevActive.current = turnActive;
  }, [turnActive, load]);

  const select = useCallback(
    async (mode: 'inherit' | 'explicit', modelId?: string) => {
      if (!sessionId || !view) return;
      setBusy(true);
      setNotice(null);
      try {
        await apiFetch(
          `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/model`,
          {
            method: 'PUT',
            ownerToken,
            label: 'session model preference update',
            body: {
              mode,
              model_id: mode === 'explicit' ? (modelId ?? null) : null,
              expected_revision: view.preference.revision,
            },
          },
        );
        await load();
      } catch (err) {
        if (isApiError(err) && err.status === 409) {
          setNotice(t('chat.model.conflict'));
          await load(); // CAS 冲突：刷新后用户可重选（不静默覆盖他人）
        } else {
          setNotice(describeApiError(err, t('chat.model.updateFailed')));
        }
      } finally {
        setBusy(false);
      }
    },
    [sessionId, view, ownerToken, t, load],
  );

  if (!sessionId || !view) return null;

  const pref = view.preference;
  // review-P3：live 失败读回（ok=false，如回读不匹配）不显示 effective 徽标
  // —— 那是 vendor 报告的「错误模型」，不是生效事实；服务端回执仍展示
  // requested → effective 原文与状态。
  const liveEffective =
    liveStatus && liveStatus.ok !== false ? liveStatus.effective?.model : null;
  const effective = liveEffective ?? view.last_turn?.effective_model ?? null;
  const requestedNow =
    liveStatus?.requested?.model ?? view.last_turn?.requested_model ?? null;
  const inherit = pref.mode !== 'explicit';
  const unavailableKey = (reason: string | null) =>
    (reason && UNAVAILABLE_REASON_KEYS[reason]) || null;

  return (
    <div className="border-b border-edge-subtle px-panel py-1.5" data-testid="session-model-selector">
      <div className="flex items-center gap-2 text-micro text-ink-secondary">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="inline-flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-left"
        >
          {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          <span className="shrink-0 font-medium">{t('chat.model.label')}</span>
          <span className="truncate font-mono">
            {inherit ? t('chat.model.inherit') : (pref.model_id ?? '—')}
          </span>
          {effective && (
            <span
              className="shrink-0 rounded-sm border border-edge-subtle bg-surface-sunken px-1"
              title={t('chat.model.effectiveTitle')}
            >
              {t('chat.model.effective')}: <span className="font-mono">{effective}</span>
            </span>
          )}
          {turnActive && (
            <span className="shrink-0 text-status-accent" title={t('chat.model.pendingTitle')}>
              {requestedNow && requestedNow !== effective
                ? t('chat.model.pendingNext')
                : t('chat.model.busy')}
            </span>
          )}
          {view.pending_next_turn && !turnActive && (
            <span className="shrink-0 text-status-accent">{t('chat.model.pendingNext')}</span>
          )}
          {usage && (
            <span className="ml-auto shrink-0" title={t('chat.model.usageTitle')}>
              {t('chat.model.tokensTotal', {
                total: usage.totals.total.toLocaleString(),
              })}
              {usage.budget?.token_cap
                ? ` / ${usage.budget.token_cap.toLocaleString()}`
                : ''}
              {usage.turns_incomplete > 0 ? ` · ${t('chat.model.incomplete')}` : ''}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={load}
          aria-label={t('chat.model.refresh')}
          className="shrink-0 cursor-pointer rounded-sm p-0.5 hover:bg-surface-hover"
        >
          <RefreshCw size={11} />
        </button>
      </div>

      {notice && (
        <div className="pt-1 text-micro text-status-critical" role="status">{notice}</div>
      )}

      {open && (
        <div className="mt-1.5 space-y-2 text-micro">
          {/* 目录：可选项 + 不可选原因（M08/M09） */}
          <div className="space-y-1">
            <button
              type="button"
              disabled={busy || inherit}
              onClick={() => select('inherit')}
              className={`flex w-full cursor-pointer items-center gap-2 rounded-sm border px-2 py-1 text-left transition-colors ${
                inherit
                  ? 'border-status-accent bg-status-accent-soft'
                  : 'border-edge-subtle hover:bg-surface-hover'
              } disabled:cursor-not-allowed disabled:opacity-60`}
            >
              <span className="font-medium">{t('chat.model.inherit')}</span>
              <span className="text-ink-muted">
                {view.catalog.find((c) => c.is_default)?.id ?? '—'}
              </span>
            </button>
            {view.catalog
              .filter((c) => !c.is_default)
              .map((item) => {
                const selected = !inherit && pref.model_id === item.id;
                const reasonKey = item.available ? null : unavailableKey(item.unavailable_reason);
                return (
                  <button
                    key={item.id}
                    type="button"
                    disabled={busy || !item.available}
                    onClick={() => select('explicit', item.id)}
                    className={`flex w-full cursor-pointer items-center gap-2 rounded-sm border px-2 py-1 text-left transition-colors ${
                      selected
                        ? 'border-status-accent bg-status-accent-soft'
                        : 'border-edge-subtle hover:bg-surface-hover'
                    } disabled:cursor-not-allowed`}
                  >
                    <span className="font-mono">{item.id}</span>
                    {!item.available && (
                      <span className="text-status-critical">
                        {reasonKey ? t(reasonKey) : t('chat.model.unavailable')}
                      </span>
                    )}
                    {item.capabilities.context_window != null && (
                      <span className="ml-auto text-ink-muted">
                        ctx {(item.capabilities.context_window / 1000).toFixed(0)}k
                      </span>
                    )}
                    {item.capabilities.cost_class !== 'unknown' && (
                      <span className="text-ink-muted">{item.capabilities.cost_class}</span>
                    )}
                  </button>
                );
              })}
          </div>

          {/* 思考档（M14/M15：不可控要明说；requested/effective 分开） */}
          <div className="flex items-center gap-2 text-ink-muted">
            <span className="font-medium text-ink-secondary">{t('chat.model.thinking')}</span>
            {view.thinking_controllable ? (
              <span>
                {pref.thinking_level ?? '—'} →{' '}
                {view.last_turn?.thinking_effective ?? liveStatus?.thinking.effective ?? '—'}
              </span>
            ) : (
              <span title={t('chat.model.thinkingNotControllableTitle')}>
                {t('chat.model.thinkingNotControllable')}
              </span>
            )}
          </div>

          {/* 回执（M18/M19：responseModel 可缺席；不伪造） */}
          {view.last_turn && (
            <div className="rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
              <div className="font-medium text-ink-secondary">{t('chat.model.lastTurn')}</div>
              <div className="font-mono">
                {view.last_turn.requested_model || '—'}
                {' → '}
                {view.last_turn.effective_model || t('chat.model.unknown')}
              </div>
              <div>
                responseModel: {view.last_turn.response_model || t('chat.model.notReported')}
                {' · '}
                {t(`chat.model.status.${view.last_turn.status}`)}
              </div>
            </div>
          )}

          {/* 用量与 coverage（M28/M29：来源分层；unknown 不当免费） */}
          {usage && (
            <div className="rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
              <div className="font-medium text-ink-secondary">{t('chat.model.usage')}</div>
              <div className="font-mono">
                in {usage.totals.input.toLocaleString()} · out{' '}
                {usage.totals.output.toLocaleString()} · cache{' '}
                {(usage.totals.cacheRead + usage.totals.cacheWrite).toLocaleString()}
              </div>
              <div>
                {t('chat.model.cost')}:{' '}
                {usage.cost_total_micro_usd != null
                  ? `$${(usage.cost_total_micro_usd / 1_000_000).toFixed(4)} (${usage.cost_source})`
                  : t('chat.model.costUnknown')}
              </div>
              {/* WP4：预算块（与 admission 同权威；cap=None = 无预算控制） */}
              <div>
                {t('chat.model.budget')}:{' '}
                {usage.budget?.token_cap != null
                  ? `${(usage.budget.used ?? 0).toLocaleString()} / ${usage.budget.token_cap.toLocaleString()}${
                      usage.budget.remaining != null
                        ? ` · ${t('chat.model.budgetRemaining').replace('{n}', usage.budget.remaining.toLocaleString())}`
                        : ''
                    }`
                  : t('chat.model.budgetUnlimited')}
              </div>
              {/* WP4：最近 turn 运行事实（attempt 链；空 = 未观察到，不渲染） */}
              {(() => {
                const last = usage.recent_turns?.[usage.recent_turns.length - 1];
                if (!last) return null;
                const bits: string[] = [];
                if ((last.provider_retry_attempts ?? 0) > 0) {
                  bits.push(
                    t('chat.model.runtimeAttempts').replace(
                      '{n}', String(last.provider_retry_attempts),
                    ),
                  );
                }
                if (last.last_provider_failure_kind) {
                  bits.push(
                    t('chat.model.runtimeFailure').replace(
                      '{kind}', last.last_provider_failure_kind,
                    ),
                  );
                }
                if (last.route_reason_codes?.length) {
                  bits.push(
                    t('chat.model.runtimeRoute').replace(
                      '{codes}', last.route_reason_codes.slice(0, 3).join(','),
                    ),
                  );
                }
                return bits.length ? (
                  <div className="text-ink-muted font-mono">{bits.join(' · ')}</div>
                ) : null;
              })()}
              {usage.turns_incomplete > 0 && (
                <div className="text-status-warning">{t('chat.model.incomplete')}</div>
              )}
              <div className="text-ink-muted">{t('chat.model.coverageNote')}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
