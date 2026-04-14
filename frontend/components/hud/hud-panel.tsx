'use client';

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';

interface HudPanelProps {
  children: React.ReactNode;
  title?: string;
  icon?: React.ReactNode;
  position: 'left' | 'right';
  isOpen: boolean;
  onClose?: () => void;
  className?: string;
  width?: string;
}

const slideVariants = {
  left: {
    initial: { x: -40, opacity: 0 },
    animate: { x: 0, opacity: 1 },
    exit: { x: -40, opacity: 0 },
  },
  right: {
    initial: { x: 40, opacity: 0 },
    animate: { x: 0, opacity: 1 },
    exit: { x: 40, opacity: 0 },
  },
};

export function HudPanel({
  children,
  title,
  icon,
  position,
  isOpen,
  onClose,
  className = '',
  width = 'w-[380px]',
}: HudPanelProps) {
  const positionClasses =
    position === 'left'
      ? 'left-4 top-4 bottom-20'
      : 'right-4 top-4 bottom-20';

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          className={`absolute ${positionClasses} ${width} z-20 flex flex-col glass-panel rounded-2xl overflow-hidden ${className}`}
          variants={slideVariants[position]}
          initial="initial"
          animate={{
            ...slideVariants[position].animate,
            y: [0, -4, 0],
          }}
          exit="exit"
          transition={{ 
            type: 'spring', 
            stiffness: 300, 
            damping: 30,
            y: { repeat: Infinity, duration: 5, ease: "easeInOut" }
          }}
        >
          {/* Header */}
          {title && (
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] bg-white/[0.01]">
              <div className="flex items-center gap-2.5">
                {icon && (
                  <div className="relative">
                    <span className="text-hud-cyan">{icon}</span>
                    <div className="absolute -inset-1 bg-hud-cyan/15 rounded-full blur-sm" />
                  </div>
                )}
                <h2 className="hud-holographic text-[11px] font-bold">
                  {title}
                </h2>
              </div>
              {onClose && (
                <button
                  onClick={onClose}
                  className="hud-btn h-7 w-7 rounded-md"
                >
                  <X className="h-3.5 w-3.5 text-white/40" />
                </button>
              )}
            </div>
          )}

          {/* Content */}
          <div className="flex-1 overflow-y-auto overflow-x-hidden">
            {children}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
