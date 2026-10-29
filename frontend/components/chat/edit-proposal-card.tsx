'use client';
/**
 * EditProposalCard —— 要素编辑提案卡（/goal 03）。
 *
 * 状态机 open(pending) → approving → applied / rejected / stale / conflicted /
 * expired / error（ClarificationCard 同款纪律：终态只读、按钮按状态显隐、
 * 错误可重试）。approve = owner 鉴权 REST 决策（服务端同事务执行 CAS/入账/
 * 盖章/广播）；applied 后地图数据经 content_revision 管线增量刷新。
 */
import { useT } from '@/lib/i18n/useT';

import type {
  FeatureEditOpSummary,
  FeatureEditPreviewItem,
  FeatureEditProposalState,
} from '@/lib/types/feature-edit';

interface EditProposalCardProps {
  proposal: FeatureEditProposalState;
  disabled?: boolean;
  onDecide?: (planId: string, action: 'approve' | 'reject') => void;
}

type EditT = ReturnType<typeof useT>;

function opLabel(t: EditT, op: FeatureEditOpSummary['op']): string {
  // 字面量键分支（i18n 静态扫描盲区棘轮不认计算键）
  switch (op) {
    case 'set_fields': return t('chat.editProposal.opSetFields');
    case 'rename_field': return t('chat.editProposal.opRenameField');
    case 'repair_geometry': return t('chat.editProposal.opRepairGeometry');
    case 'move_by': return t('chat.editProposal.opMoveBy');
    case 'snap_to_ref': return t('chat.editProposal.opSnapToRef');
    default: return op;
  }
}

function opSummaryText(op: FeatureEditOpSummary): string {
  switch (op.op) {
    case 'set_fields':
      return (op.fields ?? []).join('、');
    case 'rename_field':
      return `${op.from ?? '?'} → ${op.to ?? '?'}`;
    case 'repair_geometry':
      return (op.fixes ?? []).join('、');
    case 'move_by':
      return `dx=${op.dx_m ?? '?'}m dy=${op.dy_m ?? '?'}m`;
    case 'snap_to_ref':
      return `容差 ${op.tolerance_m ?? '?'}m${op.snapped !== undefined ? `（命中 ${op.snapped}）` : ''}`;
    default:
      return '';
  }
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'string') return v.length > 48 ? `${v.slice(0, 48)}…` : v;
  return String(v);
}

function DiffRows({ preview, t }: { preview: FeatureEditPreviewItem[]; t: EditT }) {
  const rows = preview.slice(0, 12);
  return (
    <div className="space-y-1.5">
      {rows.map((item) => (
        <div
          key={item.wfid}
          data-testid="edit-proposal-diff"
          className="rounded border border-neutral-700/60 bg-neutral-900/40 px-2 py-1.5"
        >
          {item.fields
            ? Object.entries(item.fields).map(([field, diff]) => (
                <div key={field} className="flex items-center gap-1.5 text-xs">
                  <span className="font-medium text-neutral-300">{field}:</span>
                  <span className="text-neutral-400 line-through">{formatValue(diff.before)}</span>
                  <span aria-hidden>→</span>
                  <span className="text-emerald-400">{formatValue(diff.after)}</span>
                </div>
              ))
            : null}
          {item.geometry ? (
            <div className="text-xs text-sky-400">
              {t('chat.editProposal.geometrySummary', { before: item.geometry.positions_before ?? '?', after: item.geometry.positions_after ?? '?' })}
            </div>
          ) : null}
        </div>
      ))}
      {preview.length > rows.length ? (
        <div className="text-xs text-neutral-500">{t('chat.editProposal.moreFeatures', { count: preview.length - rows.length })}</div>
      ) : null}
    </div>
  );
}

export function EditProposalCard({ proposal, disabled, onDecide }: EditProposalCardProps) {
  const t = useT();
  const terminal = ['applied', 'rejected', 'stale', 'conflicted', 'expired'].includes(proposal.status);
  const busy = proposal.status === 'approving';

  return (
    <div
      data-testid={`edit-proposal-card-${proposal.status}`}
      className="my-2 rounded-lg border border-neutral-700 bg-neutral-900/70 p-3 text-sm"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">
          {t('chat.editProposal.title')}
          <span className="ml-2 text-xs font-normal text-neutral-400">
            {t('chat.editProposal.header', { count: proposal.n_targets, ref: proposal.ref_id.slice(0, 24) })}
          </span>
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-xs ${
            proposal.safe ? 'bg-emerald-900/60 text-emerald-300' : 'bg-amber-900/60 text-amber-300'
          }`}
        >
          {proposal.safe ? t('chat.editProposal.safe') : t('chat.editProposal.needsApproval')}
        </span>
      </div>

      <ul className="mb-2 space-y-0.5 text-xs text-neutral-300">
        {proposal.ops.map((op, i) => (
          <li key={`${op.op}-${i}`}>
            · {opLabel(t, op.op)}{t('chat.editProposal.colon')}<span className="text-neutral-400">{opSummaryText(op)}</span>
          </li>
        ))}
        {proposal.schema_ops.map((s, i) => (
          <li key={`schema-${i}`}>
            · {t('chat.editProposal.rename')}{t('chat.editProposal.colon')}{s.from ?? '?'} → {s.to ?? '?'}
          </li>
        ))}
      </ul>

      {proposal.preview.length > 0 ? <DiffRows preview={proposal.preview} t={t} /> : null}

      {proposal.status === 'applied' && proposal.receipt ? (
        <div className="mt-2 text-xs text-emerald-400" data-testid="edit-proposal-applied-receipt">
          {t('chat.editProposal.applied')} · rev {proposal.receipt.content_revision_after ?? '?'}
          {' · '}
          {t('chat.editProposal.undoHint')}
        </div>
      ) : null}
      {proposal.status === 'rejected' ? (
        <div className="mt-2 text-xs text-neutral-400">{t('chat.editProposal.rejected')}</div>
      ) : null}
      {proposal.status === 'stale' || proposal.status === 'conflicted' ? (
        <div className="mt-2 text-xs text-amber-400">
          {t('chat.editProposal.conflict')}
        </div>
      ) : null}
      {proposal.status === 'expired' ? (
        <div className="mt-2 text-xs text-amber-400">{t('chat.editProposal.expired')}</div>
      ) : null}
      {proposal.status === 'error' ? (
        <div className="mt-2 text-xs text-red-400" data-testid="edit-proposal-error">
          {proposal.error ?? t('chat.editProposal.error')}
        </div>
      ) : null}

      {!terminal ? (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
            disabled={disabled || busy}
            data-testid="edit-proposal-approve"
            onClick={() => onDecide?.(proposal.plan_id, 'approve')}
          >
            {busy ? t('chat.editProposal.approving') : t('chat.editProposal.approve')}
          </button>
          <button
            type="button"
            className="rounded border border-neutral-600 px-3 py-1 text-xs text-neutral-300 disabled:opacity-50"
            disabled={disabled || busy}
            data-testid="edit-proposal-reject"
            onClick={() => onDecide?.(proposal.plan_id, 'reject')}
          >
            {t('chat.editProposal.reject')}
          </button>
        </div>
      ) : null}
    </div>
  );
}
