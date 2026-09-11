'use client';
import { Suspense } from 'react';
import { StoryView } from './story-view';

// ADR-0147：页面文件只保留路由壳（Next 限制 page 的导出面）；章节模型、
// 编排持久化、scrubber 与叙事导出实现见同目录 story-view / chapters /
// chapter-artifact / chapter-scrubber / narrative-export。
export default function StoryPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen text-ink-muted">Loading...</div>}>
      <StoryView />
    </Suspense>
  );
}
