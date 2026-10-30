'use client';

/**
 * MapHarnessCards —— 地图操作回执卡 + 最终显示确认卡的复合宿主（ADR-0278）。
 *
 * 数据面纪律：
 * - 回执来自 receipt-bus（后端命令 map_operation_receipt 投递），组件零
 *   fetch、零推导 —— 渲染服务端裁决的事实（per-effect before/after、
 *   user_wins/superseded/partial 如实呈现，不用 200 冒充成功）；
 * - 显示确认卡自水合：挂载/会话切换时 GET display-confirmation；auto
 *   模式永不显示（历史行为零变化）；MapSpec live generation 变化后
 *   debounce 复查（终验裁决随观察/turn 演进，非轮询）；
 * - 「地图已改变」≠「用户已看到/认可」：只有 owner 当前代次 ack 才把
 *   pending 翻成 confirmed（后端新鲜度门权威）。
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ChevronDown, ChevronUp, Eye, EyeOff, X } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import {
  commitPendingMapOperation,
  fetchDisplayConfirmation,
  fetchMapOperations,
  postDisplayAck,
  type DisplayConfirmationStatus,
} from '@/lib/api/map-operations';
import {
  dismissOperationReceipt,
  getOperationReceipts,
  pushOperationReceipt,
  setReceiptBusSession,
  subscribeReceipts,
  updateOperationReceipt,
  type MapOperationReceiptView,
} from '@/lib/map-operations/receipt-bus';
import { subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';

const STATUS_TONE: Record<string, string> = {
  committed: 'text-emerald-600',
  preview: 'text-sky-600',
  partial: 'text-amber-600',
  superseded: 'text-amber-600',
  rejected: 'text-rose-600',
  noop: 'text-ink-muted',
  confirmation_required: 'text-amber-600',
  clarification_needed: 'text-amber-600',
};

const EFFECT_LABEL_KEYS: Record<string, string> = {
  set_layer_visibility: 'effectVisibility',
  set_layer_opacity: 'effectOpacity',
  set_layer_paint: 'effectPaint',
  set_layer_symbol: 'effectSymbol',
  move_layer: 'effectMove',
  set_viewport: 'effectViewport',
  set_component: 'effectComponent',
  remove_layer: 'effectRemove',
};

function _fmtValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'on' : 'off';
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).slice(0, 3);
    return entries.map(([k, v]) => `${k}=${_fmtValue(v)}`).join(', ') || '{}';
  }
  return String(value);
}

function ReceiptCard(props: {
  receipt: MapOperationReceiptView;
  sessionId: string | null;
  ownerToken?: string | null;
}) {
  const { receipt, sessionId, ownerToken } = props;
  const t = useT();
  const [expanded, setExpanded] = useState(true);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = String(receipt.status || '');
  const effects = Array.isArray(receipt.effects) ? receipt.effects : [];

  const approve = useCallback(async () => {
    if (!sessionId || committing) return;
    setCommitting(true);
    setError(null);
    try {
      const finalReceipt = await commitPendingMapOperation(
        sessionId, receipt.operation_id, ownerToken,
      );
      updateOperationReceipt(finalReceipt);
    } catch (err) {
      setError(String((err as Error)?.message || err));
    } finally {
      setCommitting(false);
    }
  }, [committing, ownerToken, receipt.operation_id, sessionId]);

  return (
    <div
      data-testid={`map-operation-card-${receipt.operation_id}`}
      className="pointer-events-auto w-80 rounded-lg border border-edge bg-surface-raised/95 p-3 shadow-lg"
    >
      <header className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span
            data-testid={`map-op-status-${receipt.operation_id}`}
            className={`text-xs font-semibold ${STATUS_TONE[status] || 'text-ink'}`}
          >
            {t(`map.mapOp.status_${status || 'unknown'}`)}
          </span>
          <span className="ml-2 text-micro text-ink-muted">
            {t('map.mapOp.rev')} {receipt.final_revision ?? receipt.mutation_revision ?? '—'}
            {receipt.replayed ? ` · ${t('map.mapOp.replayed')}` : ''}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label={expanded ? t('map.mapOp.collapse') : t('map.mapOp.expand')}
            onClick={() => setExpanded((v) => !v)}
            className="text-ink-muted hover:text-ink"
          >
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            data-testid={`map-op-dismiss-${receipt.operation_id}`}
            aria-label={t('map.mapOp.dismiss')}
            onClick={() => dismissOperationReceipt(receipt.operation_id)}
            className="text-ink-muted hover:text-ink"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {receipt.message ? (
        <p className="mb-1.5 text-micro leading-relaxed text-ink">
          {String(receipt.message).slice(0, 200)}
        </p>
      ) : null}

      {receipt.clarification?.candidates?.length ? (
        <ul className="mb-1.5 space-y-0.5" data-testid={`map-op-candidates-${receipt.operation_id}`}>
          {receipt.clarification.candidates.map((c) => (
            <li key={c.layer_id} className="text-micro text-ink-muted">
              · {c.name || c.layer_id}
            </li>
          ))}
        </ul>
      ) : null}

      {expanded && effects.length > 0 ? (
        <ul className="space-y-1">
          {effects.slice(0, 8).map((eff) => (
            <li key={eff.index} className="flex items-baseline justify-between gap-2 text-micro">
              <span className="truncate text-ink">
                {t(`map.mapOp.${EFFECT_LABEL_KEYS[eff.kind] || 'effectOther'}`)}
                {(eff.targets?.length ?? 0) > 0 ? ` · ${eff.targets!.join(', ')}` : ''}
              </span>
              <span
                className={`shrink-0 font-mono text-[10px] ${
                  eff.state === 'applied' ? 'text-emerald-600'
                    : eff.state === 'failed' ? 'text-rose-600'
                    : 'text-ink-muted'
                }`}
                title={eff.reason_code ? t(`map.mapOp.reason_${eff.reason_code}`) : undefined}
              >
                {eff.reason_code === 'user_wins'
                  ? t('map.mapOp.state_user_wins')
                  : t(`map.mapOp.state_${eff.state || 'planned'}`)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {status === 'confirmation_required' && sessionId ? (
        <button
          type="button"
          data-testid={`map-op-approve-${receipt.operation_id}`}
          disabled={committing}
          onClick={() => void approve()}
          className="mt-2 w-full rounded-md border border-edge-subtle bg-surface px-2 py-1 text-micro font-medium text-ink hover:bg-surface-overlay disabled:opacity-50"
        >
          {committing ? t('map.mapOp.committing') : t('map.mapOp.approveRemoval')}
        </button>
      ) : null}

      {error ? (
        <p data-testid={`map-op-error-${receipt.operation_id}`} className="mt-1 text-micro text-rose-600">
          {t('map.mapOp.commitFailed')}: {error.slice(0, 120)}
        </p>
      ) : null}
    </div>
  );
}

function DisplayConfirmationCard(props: {
  sessionId: string;
  ownerToken?: string | null;
  status: DisplayConfirmationStatus;
  onAcked: (next: DisplayConfirmationStatus) => void;
}) {
  const { sessionId, ownerToken, status, onAcked } = props;
  const t = useT();
  const [acking, setAcking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const acknowledge = useCallback(async () => {
    if (acking) return;
    setAcking(true);
    setError(null);
    try {
      const resp = await postDisplayAck(
        sessionId, status.latest_observation_seq, ownerToken,
      );
      if (resp.accepted && resp.confirmed) {
        setDone(true);
        onAcked({ ...status, confirmed: true, pending: false });
      } else if (resp.reason === 'mode_auto') {
        setDone(true);
        onAcked({ ...status, confirmed: true, pending: false });
      } else {
        setError(t('map.displayConfirm.notConfirmed'));
      }
    } catch (err) {
      setError(String((err as Error)?.message || err).slice(0, 140));
    } finally {
      setAcking(false);
    }
  }, [acking, onAcked, ownerToken, sessionId, status, t]);

  if (done || !status.pending) return null;

  return (
    <div
      data-testid="display-confirmation-card"
      className="pointer-events-auto w-80 rounded-lg border border-edge bg-surface-raised/95 p-3 shadow-lg"
    >
      <header className="mb-1.5 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          <Eye className="h-3.5 w-3.5" aria-hidden />
          {t('map.displayConfirm.title')}
        </span>
        <EyeOff className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
      </header>
      <p className="mb-2 text-micro leading-relaxed text-ink">
        {t('map.displayConfirm.body')}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          data-testid="display-confirm-ack"
          disabled={acking}
          onClick={() => void acknowledge()}
          className="flex-1 rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-micro font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
        >
          {acking ? t('map.displayConfirm.acking') : t('map.displayConfirm.ack')}
        </button>
        <button
          type="button"
          data-testid="display-confirm-dismiss"
          onClick={() => setDone(true)}
          className="rounded-md border border-edge-subtle px-2 py-1 text-micro text-ink-muted hover:bg-surface-overlay"
        >
          {t('map.displayConfirm.later')}
        </button>
      </div>
      {error ? (
        <p data-testid="display-confirm-error" className="mt-1 text-micro text-rose-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function MapHarnessCards(props: {
  sessionId?: string | null;
  ownerToken?: string | null;
}) {
  const { sessionId, ownerToken } = props;
  const receipts = useSyncExternalStore(subscribeReceipts, getOperationReceipts);
  const [confirmStatus, setConfirmStatus] = useState<DisplayConfirmationStatus | null>(null);
  const confirmStatusRef = useRef<DisplayConfirmationStatus | null>(null);
  confirmStatusRef.current = confirmStatus;
  const mountedSessionRef = useRef<string | null>(null);

  // 会话绑定/切换：bus 清空 + 确认状态重置。
  useEffect(() => {
    setReceiptBusSession(sessionId ?? null);
    mountedSessionRef.current = sessionId ?? null;
    setConfirmStatus(null);
  }, [sessionId]);

  // 显示确认水合：不抢首批渲染/观察窗口（map-panel.cartography-race 对
  // fetch 调用序有精确断言）—— 挂载后 5s 兜底一次 + MapSpec live
  // generation 变化 debounce 2s 复查；auto 模式确认面恒空，已知 auto
  // 即不再复查（env 在部署内恒定）。终验裁决随观察/turn 演进时
  // live generation 必然前进，卡面随之刷新 —— 非轮询。
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let mountTimer: ReturnType<typeof setTimeout> | null = null;
    let debounce: ReturnType<typeof setTimeout> | null = null;
    const hydrate = async () => {
      if (cancelled || mountedSessionRef.current !== sessionId) return;
      try {
        const status = await fetchDisplayConfirmation(sessionId, ownerToken);
        if (!cancelled && mountedSessionRef.current === sessionId) {
          setConfirmStatus(status);
        }
      } catch {
        if (!cancelled) setConfirmStatus(null);
      }
    };
    mountTimer = setTimeout(() => {
      if (confirmStatusRef.current?.mode === 'auto') return;
      void hydrate();
      // 独立评审 D-4：跨刷新恢复 —— 拉服务端回执环回填 bus（刷新后
      // agent 操作历史重现；fetch 失败静默 —— 环是披露面不是正确性面）。
      void (async () => {
        try {
          const remote = await fetchMapOperations(sessionId, ownerToken);
          for (const receipt of remote.slice(0, 3)) {
            pushOperationReceipt(receipt, sessionId);
          }
        } catch {
          /* 恢复失败不打扰用户 */
        }
      })();
    }, 5000);
    const unsubscribe = subscribeMapSpecLive(() => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => {
        if (confirmStatusRef.current?.mode === 'auto') return;
        void hydrate();
      }, 2000);
    });
    return () => {
      cancelled = true;
      unsubscribe();
      if (mountTimer) clearTimeout(mountTimer);
      if (debounce) clearTimeout(debounce);
    };
  }, [sessionId, ownerToken]);

  if (!sessionId) return null;

  const visibleReceipts = receipts.slice(0, 3);

  if (visibleReceipts.length === 0 && !confirmStatus?.pending) return null;

  return (
    <div
      data-testid="map-harness-cards"
      className="pointer-events-none absolute bottom-24 left-3 z-40 flex flex-col gap-2"
    >
      {confirmStatus?.pending ? (
        <DisplayConfirmationCard
          sessionId={sessionId}
          ownerToken={ownerToken}
          status={confirmStatus}
          onAcked={setConfirmStatus}
        />
      ) : null}
      {visibleReceipts.map((receipt) => (
        <ReceiptCard
          key={receipt.operation_id}
          receipt={receipt}
          sessionId={sessionId}
          ownerToken={ownerToken}
        />
      ))}
    </div>
  );
}
