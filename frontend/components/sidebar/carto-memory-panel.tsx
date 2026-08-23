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
import {
  activateCartoFact,
  getCartoMemory,
  retireCartoFact,
  type CartoFact,
  type CartoFactStatus,
} from '@/lib/api/carto-memory';

const KIND_LABEL: Record<string, string> = {
  shared_classification: '共享分类',
  preference: '偏好',
  recipe_outcome: 'recipe 成效',
  data_profile: '数据画像',
};

const STATUS_LABEL: Record<CartoFactStatus, string> = {
  active: '生效中',
  stale: '已过期',
  conflicted: '待裁决',
  retired: '已撤销',
};

function factDetail(fact: CartoFact): string {
  const payload = fact.payload ?? {};
  if (fact.kind === 'shared_classification') {
    const breaks = Array.isArray(payload.breaks) ? payload.breaks : [];
    return breaks.length
      ? `${String(payload.type ?? '?')} · 断点 [${breaks.join(', ')}]`
      : `${String(payload.type ?? '?')} · ${String(payload.class_count ?? '?')} 类`;
  }
  if (fact.kind === 'preference') {
    return String(payload.value ?? '');
  }
  if (fact.kind === 'recipe_outcome') {
    return `上次达到 ${fact.validity_tier ?? '?'}`;
  }
  return '分布基线（漂移判定锚点）';
}

export function CartoMemoryPanel({ projectId }: { projectId: string | null }) {
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
        setError(e instanceof Error ? e.message : '加载制图记忆失败');
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
      addToast(`已撤销「${KIND_LABEL[fact.kind] ?? fact.kind} · ${fact.subject}」`, 'success');
      await refresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : '撤销失败', 'error');
    } finally {
      setMutating(null);
    }
  };

  const handleActivate = async (fact: CartoFact) => {
    if (!projectId) return;
    setMutating(fact.id);
    try {
      await activateCartoFact(projectId, fact.id);
      addToast(`已激活「${KIND_LABEL[fact.kind] ?? fact.kind} · ${fact.subject}」`, 'success');
      await refresh();
    } catch (e) {
      addToast(e instanceof Error ? e.message : '激活失败', 'error');
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
    <section aria-label="项目制图记忆" className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 text-meta font-medium text-ink-secondary hover:text-ink"
      >
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <Brain size={14} aria-hidden />
        制图记忆
        {open && counts.active > 0 && (
          <span className="text-micro text-ink-muted">（{counts.active} 条生效）</span>
        )}
      </button>

      {open && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          {loading ? (
            <LoadingState label="加载制图记忆…" />
          ) : error ? (
            <InlineNotice variant="error">{error}</InlineNotice>
          ) : visible.length === 0 ? (
            <EmptyState
              icon={MapIcon}
              title="暂无制图记忆"
              description="同一项目出图并通过质量评审后，分类方案与偏好会在此沉淀"
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
                      {KIND_LABEL[fact.kind] ?? fact.kind} · {fact.subject}
                      <span
                        className={
                          fact.status === 'active'
                            ? 'ml-1.5 text-micro text-status-ok'
                            : fact.status === 'conflicted'
                              ? 'ml-1.5 text-micro text-status-warning'
                              : 'ml-1.5 text-micro text-ink-muted'
                        }
                      >
                        {STATUS_LABEL[fact.status]}
                      </span>
                    </div>
                    <div className="truncate text-micro text-ink-muted">{factDetail(fact)}</div>
                  </div>
                  {authUser && (
                    <div className="flex shrink-0 gap-1">
                      {fact.status !== 'active' && (
                        <IconButton
                          label="激活"
                          icon={RotateCcw}
                          iconSize={13}
                          disabled={mutating === fact.id}
                          onClick={() => void handleActivate(fact)}
                        />
                      )}
                      {fact.status !== 'retired' && (
                        <IconButton
                          label="撤销"
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
            <p className="text-micro text-ink-muted">登录后可撤销或激活记忆条目</p>
          )}
        </div>
      )}
    </section>
  );
}
