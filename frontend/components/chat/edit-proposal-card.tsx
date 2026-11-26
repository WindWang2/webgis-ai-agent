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
  FeatureEditImpactSummary,
  FeatureEditOpSummary,
  FeatureEditPreviewItem,
  FeatureEditProposalState,
  FeatureEditTopologyContext,
} from '@/lib/types/feature-edit';

interface EditProposalCardProps {
  proposal: FeatureEditProposalState;
  disabled?: boolean;
  onDecide?: (planId: string, action: 'approve' | 'reject') => void;
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

/** 带符号的有界量值（服务端保留 3 位小数；展示收敛到 2 位 + 正号显式）。 */
function formatSignedDelta(v: number): string {
  const rounded = Math.round(v * 100) / 100;
  return `${rounded > 0 ? '+' : ''}${rounded}`;
}

/**
 * ADR-0300 共享拓扑影响段（additive：仅共享 op 的 plan 带这些字段）。
 * 有界渲染：固定行数的计数/量值投影，不展开邻接清单 —— 权威传播范围以
 * 服务端 plan summary 为准。
 */
function ImpactSection({
  summary,
  context,
}: {
  summary?: FeatureEditImpactSummary;
  context?: FeatureEditTopologyContext;
}) {
  const t = useT('topologyEditing');
  const newViolations = context?.new_violations ?? 0;
  const existingViolations = context?.existing_violations ?? 0;
  const crossDatasetBlocked = summary?.atomicity?.cross_dataset === 'blocked';
  return (
    <div
      data-testid="edit-proposal-impact"
      className="mb-2 space-y-0.5 rounded border border-neutral-700/60 bg-neutral-900/40 px-2 py-1.5 text-xs"
    >
      <div className="text-neutral-300">
        {t('impactSection.touched', { count: summary?.touched_features ?? 0 })}
        {' · '}
        {t('impactSection.neighbors', { count: summary?.affected_neighbors ?? 0 })}
        {summary?.neighbors_truncated ? (
          <span className="ml-1 text-amber-400">{t('impactSection.truncated')}</span>
        ) : null}
      </div>
      {summary?.area_delta_m2 != null ? (
        <div className="text-sky-400">{t('impactSection.areaDelta', { delta: formatSignedDelta(summary.area_delta_m2) })}</div>
      ) : null}
      {summary?.length_delta_m != null ? (
        <div className="text-sky-400">{t('impactSection.lengthDelta', { delta: formatSignedDelta(summary.length_delta_m) })}</div>
      ) : null}
      {context ? (
        <div className="flex items-center gap-1.5 text-neutral-300">
          <span aria-hidden className={newViolations > 0 ? 'text-red-400' : 'text-emerald-400'}>●</span>
          <span className={newViolations > 0 ? 'text-red-400' : 'text-emerald-400'}>
            {t('impactSection.newViolations', { count: newViolations })}
          </span>
          <span aria-hidden>/</span>
          <span className="text-neutral-400">
            {t('impactSection.existingViolations', { count: existingViolations })}
          </span>
        </div>
      ) : null}
      {crossDatasetBlocked ? (
        <div className="text-amber-400">{t('impactSection.blockedCrossDataset')}</div>
      ) : null}
    </div>
  );
}

function DiffRows({ preview }: { preview: FeatureEditPreviewItem[] }) {
  const t = useT();
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
  // 字面量键在组件作用域内（t 来自本地 useT —— i18n 盲区棘轮可静态判定）
  const labels: Record<FeatureEditOpSummary['op'], string> = {
    set_fields: t('chat.editProposal.opSetFields'),
    rename_field: t('chat.editProposal.opRenameField'),
    repair_geometry: t('chat.editProposal.opRepairGeometry'),
    move_by: t('chat.editProposal.opMoveBy'),
    snap_to_ref: t('chat.editProposal.opSnapToRef'),
    create_features: t('chat.editProposal.opCreateFeatures'),
    delete_features: t('chat.editProposal.opDeleteFeatures'),
    replace_geometry: t('chat.editProposal.opReplaceGeometry'),
    split_feature: t('chat.editProposal.opSplitFeature'),
    merge_features: t('chat.editProposal.opMergeFeatures'),
    // ADR-0300 共享拓扑 op（词表 additive；标签来自新 namespace，chat.json 不动）
    move_shared_vertex: t('topologyEditing.ops.moveSharedVertex'),
    insert_shared_vertex: t('topologyEditing.ops.insertSharedVertex'),
    delete_shared_vertex: t('topologyEditing.ops.deleteSharedVertex'),
    reshape_shared_edge: t('topologyEditing.ops.reshapeSharedEdge'),
    connect_endpoint: t('topologyEditing.ops.connectEndpoint'),
  };
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
            · {labels[op.op]}{t('chat.editProposal.colon')}<span className="text-neutral-400">{opSummaryText(op)}</span>
          </li>
        ))}
        {proposal.schema_ops.map((s, i) => (
          <li key={`schema-${i}`}>
            · {t('chat.editProposal.rename')}{t('chat.editProposal.colon')}{s.from ?? '?'} → {s.to ?? '?'}
          </li>
        ))}
      </ul>

      {proposal.preview.length > 0 ? <DiffRows preview={proposal.preview} /> : null}

      {proposal.impact_summary || proposal.topology_context ? (
        <ImpactSection summary={proposal.impact_summary} context={proposal.topology_context} />
      ) : null}

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
