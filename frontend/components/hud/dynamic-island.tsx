'use client';

import React, { useState, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Send, Loader2, Upload, Sparkles, Settings } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

interface DynamicIslandProps {
  onSend: (message: string) => void;
  isLoading: boolean;
  onUploadClick?: () => void;
  statusText?: string;
}

export function DynamicIsland({ onSend, isLoading, onUploadClick, statusText }: DynamicIslandProps) {
  const [input, setInput] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || isLoading) return;
    onSend(text);
    setInput('');
  }, [input, isLoading, onSend]);

  const { setSettingsOpen } = useHudStore();

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isError = statusText?.includes('⚠') || statusText?.includes('出');
  const isDone = statusText?.includes('✓') || statusText?.includes('完成');
  const isThinking = isLoading || (statusText && !isError && !isDone);

  const getStatusColor = () => {
    if (isError) return 'border-hud-red shadow-[0_0_20px_rgba(255,45,85,0.15)]';
    if (isDone) return 'border-hud-green shadow-[0_0_20px_rgba(0,255,65,0.15)]';
    if (isThinking) return 'border-hud-cyan/40 shadow-[0_0_25px_rgba(0,242,255,0.2)]';
    return 'border-white/10';
  };

  return (
    <motion.div
      className="absolute bottom-6 left-1/2 -translate-x-1/2 z-30"
      initial={{ y: 40, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.3, type: 'spring', stiffness: 260, damping: 24 }}
    >
      <div
        className={`dynamic-island flex items-center gap-3 px-4 py-2.5 rounded-2xl transition-all duration-500 ${
          isFocused ? 'w-[640px]' : 'w-[540px]'
        } ${getStatusColor()}`}
      >
        {/* Core Glow (Agent Pulse) */}
        {isThinking && (
          <div className="absolute inset-0 rounded-2xl animate-pulse bg-hud-cyan/5 pointer-events-none" />
        )}

        {/* Upload button */}
        <button
          onClick={onUploadClick}
          className="hud-btn h-9 w-9 shrink-0 rounded-lg text-white/30 hover:text-white/60"
          title="上传 GIS 数据"
        >
          <Upload className="h-4 w-4" />
        </button>

        {/* Settings button */}
        <button
          onClick={() => setSettingsOpen(true)}
          className="hud-btn h-9 w-9 shrink-0 rounded-lg text-white/30 hover:text-white/60"
          title="系统设置"
        >
          <Settings className="h-4 w-4" />
        </button>

        {/* Input field */}
        <div className="flex-1 relative flex items-center">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            disabled={isLoading}
            placeholder={isLoading ? "" : "输入空间分析指令..."}
            className="w-full bg-transparent text-[13px] text-white/90 placeholder:text-white/20 focus:outline-none font-light tracking-wide"
          />
          
          {/* Status indicator - Integrated into the bar */}
          <AnimatePresence>
            {statusText && (
              <motion.div
                className="absolute right-2 flex items-center gap-2"
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -10 }}
              >
                <div className={`h-1.5 w-1.5 rounded-full animate-pulse ${
                  isError ? 'bg-hud-red' : isDone ? 'bg-hud-green' : 'bg-hud-cyan'
                }`} />
                <span className={`text-[10px] font-mono uppercase tracking-[0.2em] ${
                  isError ? 'text-hud-red/80' : isDone ? 'text-hud-green/80' : 'text-hud-cyan/80'
                }`}>
                  {statusText}
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* AI sparkle indicator */}
        <div className="shrink-0">
          <Sparkles className={`h-3.5 w-3.5 transition-all duration-500 ${
            isThinking ? 'text-hud-cyan scale-110 drop-shadow-[0_0_8px_rgba(0,242,255,0.5)]' : 'text-white/10'
          }`} />
        </div>

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={!input.trim() || isLoading}
          className={`hud-btn h-9 w-9 shrink-0 rounded-lg transition-all duration-300 ${
            input.trim() && !isLoading
              ? 'bg-hud-cyan/15 border-hud-cyan/30 text-hud-cyan hover:bg-hud-cyan/25'
              : 'text-white/20'
          }`}
        >
          {isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </button>
      </div>
    </motion.div>
  );
}
