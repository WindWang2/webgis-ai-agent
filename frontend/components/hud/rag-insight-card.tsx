'use client';

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { BookOpen, X, Database, FileText } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

export function RagInsightCard() {
  const ragInsight = useHudStore((s) => s.ragInsight);
  const setRagInsight = useHudStore((s) => s.setRagInsight);

  return (
    <AnimatePresence>
      {ragInsight && (
        <motion.div
          className="absolute top-4 left-1/2 -translate-x-1/2 z-30 w-[400px]"
          initial={{ opacity: 0, y: -20, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        >
          <div className="glass-panel rounded-xl overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06]">
              <div className="flex items-center gap-2">
                <div className="relative">
                  <BookOpen className="h-3.5 w-3.5 text-hud-cyan" />
                  <div className="absolute -inset-1 bg-hud-cyan/15 rounded-full blur-sm" />
                </div>
                <span className="text-[10px] font-display font-semibold uppercase tracking-[0.15em] text-hud-cyan/80">
                  RAG Insight
                </span>
              </div>
              <button
                onClick={() => setRagInsight(null)}
                className="hud-btn h-6 w-6 rounded-md"
              >
                <X className="h-3 w-3 text-white/30" />
              </button>
            </div>

            {/* Content */}
            <div className="px-4 py-3 space-y-2">
              <h3 className="text-xs font-medium text-white/80 flex items-center gap-1.5">
                <Database className="h-3 w-3 text-hud-cyan/50" />
                {ragInsight.title}
              </h3>
              <p className="text-[11px] text-white/50 leading-relaxed">
                {ragInsight.content}
              </p>
              {ragInsight.source && (
                <div className="flex items-center gap-1.5 pt-1">
                  <FileText className="h-2.5 w-2.5 text-white/20" />
                  <span className="text-[9px] font-mono text-white/20">
                    来源: {ragInsight.source}
                  </span>
                </div>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
