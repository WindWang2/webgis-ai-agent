'use client';

import React, { useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';
import { getSkills } from '@/lib/api/skills';
import { isApiError } from '@/lib/api/transport';

export function SkillsHub() {
  const skills = useHudStore((s) => s.skills);
  const toggleSkill = useHudStore((s) => s.toggleSkill);
  const setSkills = useHudStore((s) => s.setSkills);

  useEffect(() => {
    const controller = new AbortController();
    getSkills({ signal: controller.signal })
      .then((skillsList) => {
        if (!skillsList.length) return;
        const existing = useHudStore.getState().skills;
        const existingMap = Object.fromEntries(existing.map((s) => [s.id, s]));
        setSkills(
          skillsList.map((sk) => ({
            id: sk.name,
            name: sk.name,
            desc: sk.description,
            enabled: existingMap[sk.name]?.enabled ?? true,
            calls: existingMap[sk.name]?.calls ?? 0,
            category: '工作流',
          }))
        );
      })
      .catch((err: unknown) => {
        // AbortError on unmount is expected; log real failures only.
        if (isApiError(err) || (err instanceof Error && err.name !== 'AbortError')) {
          console.warn('SkillsHub: failed to load skills', err);
        }
      });
    return () => controller.abort();
  }, [setSkills]);

  /* Group skills by category */
  const grouped = skills.reduce<Record<string, typeof skills>>((acc, sk) => {
    const cat = sk.category || 'Other';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(sk);
    return acc;
  }, {});

  const categoryOrder = [
    '数据获取',
    '遥感分析',
    '空间分析',
    '网络分析',
    '地形分析',
    '制图',
    '输出',
    'Other',
  ];

  const sortedCategories = Object.keys(grouped).sort((a, b) => {
    const ia = categoryOrder.indexOf(a);
    const ib = categoryOrder.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  return (
    <div className="flex flex-col gap-5">
      <STitle title="Skills Hub" sub="Agent 技能管理" />

      {sortedCategories.map((category) => (
        <div key={category}>
          <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-2">
            {category}
          </div>
          <div className="flex flex-col gap-1.5">
            {grouped[category].map((sk) => (
              <div
                key={sk.id}
                className="flex items-center gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2.5 transition-all"
                style={{
                  opacity: sk.enabled ? 1 : 0.55,
                }}
              >
                {/* Name + badge */}
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-body font-medium text-ink truncate">
                    {sk.name}
                  </span>
                  {sk.calls > 0 && (
                    <span
                      className="rounded-pill px-1.5 py-0.5 text-body font-bold leading-none"
                      style={{
                        backgroundColor: 'color-mix(in srgb, var(--agent-accent, #16a34a) 8%, transparent)',
                        /* 计数是 accent 作文字（状态数字）—— 用 text-safe 变体。 */
                        color: 'var(--agent-accent)',
                      }}
                    >
                      {sk.calls}
                    </span>
                  )}
                </div>

                {/* Description */}
                <div className="text-body text-ink-muted truncate flex-1">
                  {sk.desc}
                </div>

                {/* Toggle */}
                <ToggleSwitch
                  label={`启用技能：${sk.name}`}
                  checked={sk.enabled}
                  onChange={() => toggleSkill(sk.id)}
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Upload custom skill */}
      <button className="flex items-center justify-center gap-2 rounded-md border-2 border-dashed border-edge-subtle bg-surface-raised py-3 text-body font-medium text-ink-muted transition-all hover:border-edge-strong hover:text-ink-secondary">
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <line x1="8" y1="3" x2="8" y2="13" />
          <line x1="3" y1="8" x2="13" y2="8" />
        </svg>
        Upload Custom Skill
      </button>
    </div>
  );
}
