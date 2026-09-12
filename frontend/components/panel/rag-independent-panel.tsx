'use client';

import React, { useCallback, useRef, useState, type KeyboardEvent } from 'react';
import { Brain, FileText, Search, X } from 'lucide-react';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useKnowledgeDocs } from '@/lib/hooks/use-knowledge-docs';
import { KnowledgeDocsTab } from './knowledge/knowledge-docs-tab';
import { KnowledgeSearchTab } from './knowledge/knowledge-search-tab';
import { KnowledgeUpload } from './knowledge/knowledge-upload';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

type PanelTab = 'docs' | 'search';

const TABS: Array<{ key: PanelTab; label: string; icon: React.ElementType }> = [
  { key: 'docs', label: '文档', icon: FileText },
  { key: 'search', label: '检索', icon: Search },
];

/**
 * 知识库面板 —— #607 `return null` 存根的真实替代（V9）。
 *
 * #607 决策语境：旧面板消费零生产者的 ragResults，永远空态，按诚实性原则
 * 移除为空壳。本面板按「有真实端点才有面板」原则重建：全部数据来自
 * /api/v1/knowledge/* 真实调用（契约见 frontend/docs/knowledge-market-recon.md）；
 * 后端缺口（分块详情、multipart 上传、嵌入模型配置）一律诚实提示，不造假数据。
 *
 * 挂载契约保持 `{ open, onClose }` 不变（page.tsx 动态 import 零改动）；
 * 抽屉样式与 settings-panel 同配方（useDialogFocus / z-[100..101] / --drawer-w）。
 */
export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState<PanelTab>('docs');

  const docsResult = useKnowledgeDocs(open);

  useDialogFocus({
    open,
    containerRef: drawerRef,
    onEscape: onClose,
    initialFocusSelector: '[role="tab"]',
  });

  // WAI-APG：水平 tablist 方向键漫游（roving tabindex）。
  const onTabKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const idx = TABS.findIndex((t) => t.key === tab);
      let next: number | null = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = ((idx < 0 ? 0 : idx) + 1) % TABS.length;
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp')
        next = ((idx < 0 ? 0 : idx) - 1 + TABS.length) % TABS.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = TABS.length - 1;
      if (next === null) return;
      e.preventDefault();
      setTab(TABS[next].key);
      drawerRef.current
        ?.querySelector<HTMLButtonElement>(`#knowledge-tab-${TABS[next].key}`)
        ?.focus();
    },
    [tab],
  );

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-[100] bg-surface-scrim" onClick={onClose} aria-hidden />

      {/* Drawer —— 与 settings-panel 同宽度变量（--drawer-w），两个右侧抽屉共享
          「地图仍然可见」的宽度约束。 */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="knowledge-panel-title"
        tabIndex={-1}
        className="fixed inset-y-0 right-0 z-[101] flex animate-slide-from-right flex-col bg-surface-panel shadow-drawer"
        style={{ width: 'var(--drawer-w)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-edge-subtle px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-surface-sunken">
              <Brain size={16} aria-hidden className="text-agent-accent" />
            </div>
            <div>
              <div id="knowledge-panel-title" className="text-title font-bold leading-tight text-ink">
                知识库
              </div>
              <div className="text-meta leading-tight text-ink-muted">Knowledge Base · RAG</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭知识库面板"
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-hover"
          >
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div
          role="tablist"
          aria-label="知识库视图"
          onKeyDown={onTabKeyDown}
          className="flex gap-1 border-b border-edge-subtle px-5 pt-2"
        >
          {TABS.map(({ key, label, icon: Icon }) => {
            const active = tab === key;
            return (
              <button
                key={key}
                id={`knowledge-tab-${key}`}
                role="tab"
                aria-selected={active}
                aria-controls={`knowledge-tabpanel-${key}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setTab(key)}
                className={`inline-flex items-center gap-1.5 rounded-t-sm border-b-2 px-3 py-2 text-body font-medium transition-colors ${
                  active
                    ? 'border-status-accent text-ink'
                    : 'border-transparent text-ink-muted hover:text-ink-secondary'
                }`}
              >
                <Icon size={14} aria-hidden />
                {label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div
          role="tabpanel"
          id={`knowledge-tabpanel-${tab}`}
          aria-labelledby={`knowledge-tab-${tab}`}
          className="flex-1 overflow-y-auto px-5 py-4"
        >
          {tab === 'docs' ? (
            <div className="flex flex-col gap-4">
              <KnowledgeUpload onUploaded={docsResult.refresh} />
              <KnowledgeDocsTab docsResult={docsResult} />
            </div>
          ) : (
            <KnowledgeSearchTab onRequestClose={onClose} />
          )}
        </div>
      </div>
    </>
  );
}

export default RagIndependentPanel;
