'use client';

/**
 * G15 多画幅产品编辑子面板（WP07）—— 挂 Map Studio「画幅」子页签。
 *
 * 单一真相 = committed MapSpec frame_product（经 subscribeFrameProduct 投影）；
 * 全部写经 commitFrameProductPatch（set_frame_product + expected_revision
 * CAS；409 superseded 静默收敛）。能力：激活画幅、改标题、同步组 A/B/无、
 * 图例策略、排序（上移/下移 —— 显示序变化，frame_id 身份不动）、隐藏/恢复
 * （layout.frames[].enabled —— 经产品整表携带，身份键不受影响）、解绑。
 * 「已保存」= 服务端 revision 落定（回灌的 spec 即真相），无本地平行态。
 */
import { useCallback, useEffect, useMemo, useId, useState } from 'react';
import { useSyncExternalStore } from 'react';
import { Layers, ArrowUp, ArrowDown, Eye, EyeOff, Target, Link2, Unlink } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import {
  commitFrameProductPatch,
  getFrameProductSnapshot,
  subscribeFrameProduct,
  type FrameProductView,
} from '@/lib/cartography/frame-product';

/** frames 整表 patch 基底：committed 原始槽逐字携带（data/where/
 * time_window/sync_group 等不丢），编辑只覆写目标键。 */
function framesPatchBase(view: FrameProductView): Array<Record<string, unknown>> {
  return view.slots.map((s) => ({ ...s.raw }));
}

function useFrameProductView(): FrameProductView {
  const subscribe = useCallback((cb: () => void) => subscribeFrameProduct(cb), []);
  return useSyncExternalStore(subscribe, getFrameProductSnapshot, getFrameProductSnapshot);
}

export function FrameProductPanel() {
  const view = useFrameProductView();
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [busy, setBusy] = useState(false);
  const baseId = useId();

  const runPatch = useCallback(
    async (patch: Parameters<typeof commitFrameProductPatch>[0], successKey: string) => {
      setBusy(true);
      try {
        const out = await commitFrameProductPatch(patch);
        if (out && out.status === 'applied') {
          useToastStore.getState().addToast(t(successKey), 'success');
        }
      } catch {
        useToastStore.getState().addToast(t('workbench.frameProduct.saveFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [t],
  );

  if (!view.bound) {
    return (
      <div className="p-4 text-body text-ink-muted" data-testid="frame-product-empty">
        <p className="font-medium text-ink">{t('workbench.frameProduct.emptyTitle')}</p>
        <p className="mt-1">{t('workbench.frameProduct.emptyHint')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3" data-testid="frame-product-panel">
      {/* 产品摘要（refs-only；含共享分级状态） */}
      <div className="rounded-md border border-edge-subtle bg-surface-sunken p-2.5 text-meta">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-ink">
            {view.gridTitle || t('workbench.frameProduct.defaultTitle')}
          </span>
          <span className="text-ink-muted">
            {view.slots.length} {t('workbench.frameProduct.framesCountUnit')}
            {' · '}
            {view.columns}×{Math.max(1, Math.ceil(view.slots.length / view.columns))}
          </span>
        </div>
        {view.sharedExpression ? (
          <p className="mt-1 text-ink-muted" data-testid="frame-product-shared">
            {t('workbench.frameProduct.sharedExpression', {
              field: view.sharedExpression.field,
              status: t(`workbench.frameProduct.domain_${view.sharedExpression.domainStatus}`),
            })}
            {view.sharedExpression.breaksCount > 0
              ? ` · ${view.sharedExpression.breaksCount - 1} ${t('workbench.frameProduct.classes')}`
              : ''}
          </p>
        ) : (
          <p className="mt-1 text-ink-muted">{t('workbench.frameProduct.noSharedExpression')}</p>
        )}
        {view.disclosures.slice(0, 3).map((d) => (
          <p key={d} className="mt-0.5 text-caption text-ink-muted">{d}</p>
        ))}
      </div>

      {/* 画幅列表：显示序 = 数组序；身份 = frame_id（徽标显示） */}
      <ol className="flex flex-col gap-1.5" data-testid="frame-product-frames">
        {view.slots.map((slot, index) => (
          <li
            key={slot.frameId}
            className="rounded-md border border-edge-subtle bg-surface-overlay p-2"
            style={{
              borderColor: slot.active ? 'var(--agent-accent)' : undefined,
            }}
            data-frame-id={slot.frameId}
          >
            <div className="flex items-center gap-1.5">
              <span className="w-5 text-center text-caption text-ink-muted">{index + 1}</span>
              <input
                id={`${baseId}-title-${slot.frameId}`}
                aria-label={t('workbench.frameProduct.frameTitle')}
                className="min-w-0 flex-1 rounded border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                defaultValue={slot.title}
                disabled={busy}
                onBlur={(e) => {
                  const next = e.target.value.trim();
                  if (!next || next === slot.title) return;
                  const frames = framesPatchBase(view);
                  frames[index] = { ...frames[index], title: next };
                  void runPatch({ frames }, 'workbench.frameProduct.saved');
                }}
              />
              {slot.active ? (
                <span
                  className="flex items-center gap-0.5 rounded bg-[color:var(--agent-accent)]/15 px-1.5 py-0.5 text-caption text-[color:var(--agent-accent)]"
                  title={t('workbench.frameProduct.activeFrame')}
                >
                  <Target size={11} aria-hidden />
                </span>
              ) : (
                <button
                  type="button"
                  aria-label={t('workbench.frameProduct.activate', { id: slot.frameId })}
                  disabled={busy}
                  onClick={() =>
                    void runPatch(
                      { active_frame: slot.frameId },
                      'workbench.frameProduct.saved',
                    )
                  }
                  className="rounded p-1 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
                >
                  <Target size={13} aria-hidden />
                </button>
              )}
              <button
                type="button"
                aria-label={t('workbench.frameProduct.moveUp')}
                disabled={busy || index === 0}
                onClick={() => {
                  const ids = view.slots.map((s) => s.frameId);
                  [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
                  const byId = new Map(view.slots.map((s) => [s.frameId, s.raw]));
                  const frames = ids.map((fid) => ({ ...byId.get(fid)! }));
                  void runPatch({ frames }, 'workbench.frameProduct.saved');
                }}
                className="rounded p-1 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-30"
              >
                <ArrowUp size={13} aria-hidden />
              </button>
              <button
                type="button"
                aria-label={t('workbench.frameProduct.moveDown')}
                disabled={busy || index === view.slots.length - 1}
                onClick={() => {
                  const ids = view.slots.map((s) => s.frameId);
                  [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]];
                  const byId = new Map(view.slots.map((s) => [s.frameId, s.raw]));
                  const frames = ids.map((fid) => ({ ...byId.get(fid)! }));
                  void runPatch({ frames }, 'workbench.frameProduct.saved');
                }}
                className="rounded p-1 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-30"
              >
                <ArrowDown size={13} aria-hidden />
              </button>
            </div>
            <div className="mt-1 flex items-center gap-2 pl-6 text-caption text-ink-muted">
              <code className="truncate" title={slot.frameId}>{slot.frameId}</code>
              {slot.binding?.hasTimeWindow ? (
                <span className="rounded bg-surface-sunken px-1">{t('workbench.frameProduct.timeWindow')}</span>
              ) : null}
              {slot.binding?.hasWhere ? (
                <span className="rounded bg-surface-sunken px-1">{t('workbench.frameProduct.whereFilter')}</span>
              ) : null}
              {/* 同步组 A/B/无（联动相机开关） */}
              <span className="ml-auto flex items-center gap-1">
                {(['A', 'B'] as const).map((g) => {
                  const inGroup = slot.syncGroup === g;
                  return (
                    <button
                      key={g}
                      type="button"
                      aria-pressed={inGroup}
                      aria-label={t('workbench.frameProduct.syncGroup', { group: g })}
                      disabled={busy}
                      onClick={() => {
                        const frames = framesPatchBase(view);
                        const target = { ...frames[index] } as {
                          sync_group?: string;
                        };
                        if (inGroup) delete target.sync_group;
                        else target.sync_group = g;
                        frames[index] = target;
                        void runPatch({ frames }, 'workbench.frameProduct.saved');
                      }}
                      className={`flex items-center gap-0.5 rounded px-1.5 py-0.5 transition-colors ${
                        inGroup
                          ? 'bg-[color:var(--agent-accent)]/15 text-[color:var(--agent-accent)]'
                          : 'text-ink-muted hover:bg-surface-hover'
                      }`}
                    >
                      {inGroup ? <Link2 size={10} aria-hidden /> : <Unlink size={10} aria-hidden />}
                      {g}
                    </button>
                  );
                })}
              </span>
              {/* 图例策略 */}
              <select
                aria-label={t('workbench.frameProduct.legendPolicy')}
                disabled={busy}
                value={slot.legend}
                onChange={(e) => {
                  const frames = framesPatchBase(view);
                  frames[index] = { ...frames[index], legend: e.target.value };
                  void runPatch({ frames }, 'workbench.frameProduct.saved');
                }}
                className="rounded border border-edge-subtle bg-surface-sunken px-1 py-0.5 text-caption text-ink"
              >
                <option value="shared">{t('workbench.frameProduct.legendShared')}</option>
                <option value="independent">{t('workbench.frameProduct.legendIndependent')}</option>
                <option value="shared_plus_frame">{t('workbench.frameProduct.legendPlus')}</option>
              </select>
            </div>
          </li>
        ))}
      </ol>

      <p className="text-caption text-ink-muted">{t('workbench.frameProduct.identityNote')}</p>
    </div>
  );
}
