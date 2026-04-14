'use client';

import React, { useState, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Send, Loader2, Upload, Sparkles } from 'lucide-react';

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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <motion.div
      className="absolute bottom-6 left-1/2 -translate-x-1/2 z-30"
      initial={{ y: 40, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.3, type: 'spring', stiffness: 260, damping: 24 }}
    >
      <div
        className={`dynamic-island flex items-center gap-3 px-4 py-2.5 rounded-2xl transition-all duration-300 ${
          isFocused ? 'w-[620px]' : 'w-[520px]'
        }`}
      >
        {/* Upload button */}
        <button
          onClick={onUploadClick}
          className="hud-btn h-9 w-9 shrink-0 rounded-lg"
          title="上传 GIS 数据"
        >
          <Upload className="h-4 w-4 text-hud-muted" />
        </button>

        {/* Input field */}
        <div className="flex-1 relative">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            disabled={isLoading}
            placeholder="输入空间分析指令..."
            className="w-full bg-transparent text-sm text-white/90 placeholder:text-white/25 focus:outline-none font-light tracking-wide"
          />
          {/* Status indicator */}
          <AnimatePresence>
            {statusText && (
              <motion.div
                className="absolute -top-8 left-0 right-0 text-center"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 8 }}
              >
                <span className="text-[10px] font-mono text-hud-cyan/70 uppercase tracking-widest">
                  {statusText}
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* AI sparkle indicator */}
        <div className="shrink-0">
          <Sparkles className={`h-3.5 w-3.5 transition-colors ${isLoading ? 'text-hud-cyan animate-pulse' : 'text-white/15'}`} />
        </div>

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={!input.trim() || isLoading}
          className={`hud-btn h-9 w-9 shrink-0 rounded-lg transition-all ${
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
