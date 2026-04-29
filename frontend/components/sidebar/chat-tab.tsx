'use client';

import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import { Upload, Send, Sparkles, CheckCircle2 } from 'lucide-react';
import type { AiStatus } from '@/lib/store/hud-types';
import MiniMd from '@/components/chat/mini-md';
import { ToolCallCard, ToolCallChain } from '@/components/chat/tool-call-card';

/* ─── Thinking dots animation ─── */
const DOT_ANIMS = ['animate-dot-1', 'animate-dot-2', 'animate-dot-3'];

function ThinkingDots({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-1">
      <div className="flex gap-[3px]">
        {DOT_ANIMS.map((anim) => (
          <span
            key={anim}
            className={`block w-[5px] h-[5px] rounded-full bg-emerald-500 ${anim}`}
          />
        ))}
      </div>
      <span className="text-[11px] text-slate-400">{text}</span>
    </div>
  );
}

/* ─── Suggested prompts ─── */
const SUGGESTED_PROMPTS = [
  '分析该区域的 POI 分布',
  '生成缓冲区分析',
  '计算人口密度热力图',
  '叠加分析两个图层',
];

function SuggestedPromptButtons({ onSend, accentColor }: { onSend: (text: string) => void; accentColor: string }) {
  return (
    <div className="px-3 pt-3 pb-2">
      <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-2">快捷指令</p>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            onClick={() => onSend(prompt)}
            className="px-2.5 py-1.5 rounded-lg text-[11px] text-slate-600 border border-slate-200/80 bg-white/60 hover:bg-white/90 transition-colors"
            style={{
              borderColor: `${accentColor}22`,
            }}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─── Props ─── */
interface ChatTabProps {
  messages: any[];
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  accentColor: string;
}

export function ChatTab({ messages, aiStatus, onSend, accentColor }: ChatTabProps) {
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isBusy = aiStatus === 'thinking' || aiStatus === 'acting';

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, aiStatus, scrollToBottom]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 80)}px`;
  }, [input]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isBusy) return;
    onSend(trimmed);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [input, isBusy, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const thinkingText =
    aiStatus === 'thinking'
      ? '正在分析指令...'
      : aiStatus === 'acting'
        ? '正在执行空间操作...'
        : '';

  return (
    <div className="flex flex-col h-full">
      {/* Messages scroll area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 && !isBusy && (
          <div className="flex flex-col items-center justify-center h-full px-6 text-center">
            <div
              className="w-12 h-12 rounded-2xl flex items-center justify-center mb-3"
              style={{ backgroundColor: `${accentColor}15` }}
            >
              <Sparkles size={22} style={{ color: accentColor }} />
            </div>
            <h3 className="text-[13px] font-semibold text-slate-800 mb-1">GeoAgent</h3>
            <p className="text-[11.5px] text-slate-400 leading-relaxed">
              输入空间分析指令，开始智能 GIS 分析
            </p>
          </div>
        )}

        {/* Message list */}
        <div className="px-3 py-3 space-y-3">
          {messages.map((msg: any, idx: number) => {
            const isUser = msg.role === 'user';
            const time = msg.timestamp
              ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : '';

            return isUser ? (
              /* ── User message: right-aligned bubble ── */
              <div key={msg.id ?? idx} className="flex justify-end">
                <div className="max-w-[85%]">
                  <div className="flex items-center justify-end gap-1.5 mb-0.5">
                    {time && <span className="text-[9px] text-slate-300">{time}</span>}
                    <span className="text-[10px] font-semibold" style={{ color: accentColor }}>You</span>
                  </div>
                  <div
                    className="rounded-2xl rounded-tr-sm px-3 py-2 text-[12.5px] leading-[1.6] text-white"
                    style={{ backgroundColor: accentColor }}
                  >
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                  </div>
                </div>
              </div>
            ) : (
              /* ── Assistant message: left-aligned with avatar ── */
              <div key={msg.id ?? idx} className="flex gap-2">
                <div className="shrink-0 mt-0.5">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center"
                    style={{ backgroundColor: `${accentColor}15` }}
                  >
                    <Sparkles size={11} style={{ color: accentColor }} />
                  </div>
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[10px] font-semibold" style={{ color: accentColor }}>GeoAgent</span>
                    {time && <span className="text-[9px] text-slate-300">{time}</span>}
                  </div>

                  {msg.layerAdded && (
                    <div
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium text-white mb-1.5"
                      style={{ backgroundColor: accentColor }}
                    >
                      <CheckCircle2 size={10} />
                      感知图层已挂载：{msg.layerAdded}
                    </div>
                  )}

                  {msg.isThinking ? (
                    <ThinkingDots text={thinkingText} />
                  ) : msg.content || msg.toolCalls?.length ? (
                    <div className="rounded-2xl rounded-tl-sm bg-slate-50/80 border border-slate-100 px-3 py-2">
                      {msg.content && <MiniMd text={msg.content} />}
                      {msg.toolCalls && msg.toolCalls.length > 0 && (
                        <ToolCallChain calls={msg.toolCalls} />
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}

          {/* Thinking indicator at end of messages */}
          {isBusy && messages.length > 0 && !messages[messages.length - 1]?.isThinking && (
            <ThinkingDots text={thinkingText} />
          )}
        </div>

        {/* Suggested prompts when not busy and few messages */}
        {!isBusy && messages.length <= 1 && (
          <SuggestedPromptButtons onSend={onSend} accentColor={accentColor} />
        )}
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-slate-200/60 bg-white/50" style={{ backdropFilter: 'blur(12px)' }}>
        <div className="flex items-end gap-2 px-3 pt-2.5 pb-1.5">
          {/* Upload button */}
          <button
            className="shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
            title="上传文件"
          >
            <Upload size={14} />
          </button>

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入空间分析指令..."
            rows={1}
            className="flex-1 resize-none bg-transparent text-[12.5px] text-slate-800 placeholder:text-slate-300 outline-none leading-[1.5] max-h-[80px] py-1"
          />

          {/* Send button */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || isBusy}
            className="shrink-0 flex items-center justify-center w-[26px] h-[26px] rounded-lg transition-colors disabled:opacity-40"
            style={{
              backgroundColor: input.trim() ? accentColor : '#e2e8f0',
              color: input.trim() ? '#fff' : '#94a3b8',
            }}
          >
            <Send size={13} />
          </button>
        </div>

        {/* Hint */}
        <div className="px-3 pb-2">
          <span className="text-[9.5px] text-slate-300">
            Enter 发送 · Shift+Enter 换行
          </span>
        </div>
      </div>
    </div>
  );
}

export default ChatTab;
