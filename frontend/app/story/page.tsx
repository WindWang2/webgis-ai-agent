'use client';
import { Suspense } from 'react';
import { StoryView } from './story-view';
import { useT } from '@/lib/i18n/useT';

// ADR-0147：页面文件只保留路由壳（Next 限制 page 的导出面）；章节模型、
// 编排持久化、scrubber 与叙事导出实现见同目录 story-view / chapters /
// chapter-artifact / chapter-scrubber / narrative-export。
function StorySuspenseFallback() {
  // #2140 F3：路由级加载提示同样走 i18n（此前硬编码英文 "Loading..."）。
  const t = useT('story');
  return (
    <div role="status" className="flex items-center justify-center h-screen text-ink-muted">
      {t('storyLoading')}
    </div>
  );
}

export default function StoryPage() {
  return (
    <Suspense fallback={<StorySuspenseFallback />}>
      <StoryView />
    </Suspense>
  );
}
