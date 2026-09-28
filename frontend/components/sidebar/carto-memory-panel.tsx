'use client';

/**
 * 项目制图记忆面板（ADR-0069 / spec 开放问题 2）。
 *
 * 展示项目的制图事实账本——共享分类方案/偏好/recipe 成效及其状态
 * （active 注入中、stale 数据漂移过期、conflicted 待裁决、retired 已撤销），
 * 并提供两个人工治理动作：撤销（retire）与显式激活（裁决入口）。
 *
 * 定位说明（与后端一致）：记忆是作图先验，不是评审证据。面板只治理
 * "下一张图从什么起点出发"，不提供任何影响 gate 判定的入口。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Brain, ChevronDown, ChevronRight, Map as MapIcon, RotateCcw, Trash2 } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { IconButton } from '@/components/shared/icon-button';
import { useToastStore } from '@/components/ui/toast';
import { useAuthUser } from '@/lib/auth/use-auth-user';
import { useT } from '@/lib/i18n/useT';
import { t as tNow } from '@/lib/i18n/t';
import {
  activateCartoFact,
  getCartoMemory,
  retireCartoFact,
  type CartoFact,
  type CartoFactStatus,
} from '@/lib/api/carto-memory';

/** kind 是后端语义枚举 —— 标签走消息键，未知 kind 回退显示原值。 */
const KIND_LABEL_KEYS: Record<string, string> = {
  shared_classification: 'kind.sharedClassification',
  preference: 'kind.preference',
  recipe_outcome: 'kind.recipeOutcome',
  data_profile: 'kind.dataProfile',
};

const STATUS_LABEL_KEYS: Record<CartoFactStatus, string> = {
  active: 'status.active',
  stale: 'status.stale',
  conflicted: 'status.conflicted',
  retired: 'status.retired',
};

/** render 期被调用：命令式 t 读 store 当前语言（随语言切换的重渲染自动刷新）。 */
function kindLabel(kind: string): string {
  const key = KIND_LABEL_KEYS[kind];
  return key ? tNow(`sidebar.memory.${key}`) : kind;
}

function statusLabel(status: CartoFactStatus): string {
  return tNow(`sidebar.memory.${STATUS_LABEL_KEYS[status]}`);
}

function factDetail(fact: CartoFact): string {
  const payload = fact.payload ?? {};
  if (fact.kind === 'shared_classification') {
    const breaks = Array.isArray(payload.breaks) ? payload.breaks : [];
    return breaks.length
      ? tNow('sidebar.memory.detail.breaks', {
          type: String(payload.type ?? '?'),
          breaks: breaks.join(', '),
        })
      : tNow('sidebar.memory.detail.classCount', {
          type: String(payload.type ?? '?'),
          count: String(payload.class_count ?? '?'),
        });
  }
  if (fact.kind === 'preference') {
    return String(payload.value ?? '');
  }
  if (fact.kind === 'recipe_outcome') {
    return tNow('sidebar.memory.detail.recipeTier', { tier: fact.validity_tier ?? '?' });
  }
  return tNow('sidebar.memory.detail.baseline');
}

export function CartoMemoryPanel({ projectId }: { projectId: string | null }) {
const t = useT();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [facts, setFacts] = useState<CartoFact[]>([]);
  const [mutating, setMutating] = useState<string | null>(null);
  const addToast = useToastStore((s) => s.addToast);
  // 撤销/激活是项目写路径，后端要求认证（与 #528 同款门控）。
  const authUser = useAuthUser();

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!projectId) return;
      setLoading(true);
      setError(null);
      try {
        const overview = await getCartoMemory(projectId, { signal });
        setFacts(overview.facts);
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setError(e instanceof Error ? e.message : tNow('sidebar.memory.loadFailed'));
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (!open || !projectId) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [open, projectId, refresh]);

  const handleRetire = async (fact: CartoFact) => {
    if (!projectId) return;
    setMutating(fact.id);
    try {
      await retireCartoFact(projectId, fact.id);
      addToast(
        tNow('sidebar.memory.retiredToast', { label: `${kindLabel(fact.kind)} · ${fact.subject}` }),
        'success',
      );
      await refresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : tNow('sidebar.memory.retireFailed'), 'error');
    } finally {
      setMutating(null);
    }
  };

  const handleActivate = async (fact: CartoFact) => {
    if (!projectId) return;
    setMutating(fact.id);
    try {
      await activateCartoFact(projectId, fact.id);
      addToast(
        tNow('sidebar.memory.activatedToast', { label: `${kindLabel(fact.kind)} · ${fact.subject}` }),
        'success',
      );
      await refresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : tNow('sidebar.memory.activateFailed'), 'error');
    } finally {
      setMutating(null);
    }
  };

  if (!projectId) return null;

  const visible = facts.filter((f) => f.kind !== 'data_profile');
  const counts = facts.reduce<Record<string, number>>((acc, f) => {
    acc[f.status] = (acc[f.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <section aria-label={t('sidebar.memory.aria')} className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 text-meta font-medium text-ink-secondary hover:text-ink"
      >
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <Brain size={14} aria-hidden />
        {t('sidebar.memory.title')}
        {open && counts.active > 0 && (
          <span className="text-micro text-ink-muted">{t('sidebar.memory.countSuffix', { count: counts.active })}</span>
        )}
      </button>

      {open && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          {loading ? (
            <LoadingState label={t('sidebar.memory.loading')} />
          ) : error ? (
            <InlineNotice variant="error">{error}</InlineNotice>
          ) : visible.length === 0 ? (
            <EmptyState
              icon={MapIcon}
              title={t('sidebar.memory.empty')}
              description={t('sidebar.memory.emptyDesc')}
            />
          ) : (
            <ul className="space-y-1.5">
              {visible.map((fact) => (
                <li
                  key={fact.id}
                  className="flex items-start justify-between gap-2 rounded border border-edge-subtle px-2 py-1.5"
                >
                  <div className="min-w-0">
                    <div className="text-meta font-medium text-ink">
                      {kindLabel(fact.kind)} · {fact.subject}
                      <span
                        className={
                          fact.status === 'active'
                            ? 'ml-1.5 text-micro text-status-ok'
                            : fact.status === 'conflicted'
                              ? 'ml-1.5 text-micro text-status-warning'
                              : 'ml-1.5 text-micro text-ink-muted'
                        }
                      >
                        {statusLabel(fact.status)}
                      </span>
                    </div>
                    <div className="truncate text-micro text-ink-muted">{factDetail(fact)}</div>
                  </div>
                  {authUser && (
                    <div className="flex shrink-0 gap-1">
                      {fact.status !== 'active' && (
                        <IconButton
                          label={t('sidebar.memory.activate')}
                          icon={RotateCcw}
                          iconSize={13}
                          disabled={mutating === fact.id}
                          onClick={() => void handleActivate(fact)}
                        />
                      )}
                      {fact.status !== 'retired' && (
                        <IconButton
                          label={t('sidebar.memory.undo')}
                          icon={Trash2}
                          iconSize={13}
                          disabled={mutating === fact.id}
                          onClick={() => void handleRetire(fact)}
                        />
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {!authUser && visible.length > 0 && (
            <p className="text-micro text-ink-muted">{t('sidebar.memory.loginHint')}</p>
          )}
        </div>
      )}
    </section>
  );
}
