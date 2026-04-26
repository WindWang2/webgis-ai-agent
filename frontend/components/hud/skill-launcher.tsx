'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Zap, X } from 'lucide-react';
import { getSkills, Skill } from '@/lib/api/skills';

interface SkillLauncherProps {
  onActivate: (skillName: string) => void;
  isLoading: boolean;
}

export function SkillLauncher({ onActivate, isLoading }: SkillLauncherProps) {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      getSkills().then(setSkills).catch(() => setSkills([]));
    }
  }, [open]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const handleActivate = useCallback((name: string) => {
    setOpen(false);
    onActivate(name);
  }, [onActivate]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        disabled={isLoading}
        className={`hud-btn h-9 w-9 shrink-0 rounded-lg transition-all duration-300 ${
          open ? 'bg-hud-cyan/15 border-hud-cyan/30 text-hud-cyan' : 'text-white/30 hover:text-white/60'
        }`}
        title="技能"
      >
        <Zap className="h-4 w-4" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="absolute bottom-12 left-1/2 -translate-x-1/2 w-[360px] glass-panel rounded-xl p-3 border border-white/[0.06]"
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 5, scale: 0.95 }}
            transition={{ duration: 0.15 }}
          >
            <div className="flex items-center justify-between mb-2 px-1">
              <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-hud-cyan/70">
                Skills
              </span>
              <button onClick={() => setOpen(false)} className="text-white/20 hover:text-white/40">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {skills.length === 0 ? (
              <div className="text-center py-4 text-white/20 text-xs">暂无可用技能</div>
            ) : (
              <div className="space-y-1.5 max-h-[280px] overflow-y-auto">
                {skills.map((skill) => (
                  <button
                    key={skill.name}
                    onClick={() => handleActivate(skill.name)}
                    className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-white/[0.04] transition-colors group"
                  >
                    <div className="flex items-center gap-2">
                      <Zap className="h-3 w-3 text-hud-cyan/50 group-hover:text-hud-cyan shrink-0" />
                      <span className="text-[12px] text-white/80 group-hover:text-white font-medium">
                        {skill.name}
                      </span>
                    </div>
                    <p className="text-[11px] text-white/30 mt-0.5 ml-5 leading-relaxed">
                      {skill.description}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
