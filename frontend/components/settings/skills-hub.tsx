'use client';

import React from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';

export function SkillsHub() {
  const skills = useHudStore((s) => s.skills);
  const toggleSkill = useHudStore((s) => s.toggleSkill);

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
          <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-2">
            {category}
          </div>
          <div className="flex flex-col gap-1.5">
            {grouped[category].map((sk) => (
              <div
                key={sk.id}
                className="flex items-center gap-3 rounded-lg border border-slate-900/6 bg-white/50 px-3 py-2.5 transition-all"
                style={{
                  opacity: sk.enabled ? 1 : 0.55,
                }}
              >
                {/* Name + badge */}
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-[12px] font-medium text-slate-700 truncate">
                    {sk.name}
                  </span>
                  {sk.calls > 0 && (
                    <span
                      className="text-[10px] font-bold rounded-full px-1.5 py-0.5 leading-none"
                      style={{
                        backgroundColor: 'rgba(22,163,74,0.08)',
                        color: '#16a34a',
                      }}
                    >
                      {sk.calls}
                    </span>
                  )}
                </div>

                {/* Description */}
                <div className="text-[11px] text-slate-400 truncate flex-1">
                  {sk.desc}
                </div>

                {/* Toggle */}
                <ToggleSwitch
                  checked={sk.enabled}
                  onChange={() => toggleSkill(sk.id)}
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Upload custom skill */}
      <button className="flex items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-200/80 bg-white/30 py-3 text-[12px] font-medium text-slate-400 hover:text-slate-500 hover:border-slate-300 transition-all">
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
