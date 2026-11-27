import { Suspense } from 'react';

import { PublicationReaderView } from './reader-view';

/**
 * 只读地图发布读者页路由壳（04 线）——/p/[code]。
 * 公开页：无登录态（story 页先例）；页面仅路由，逻辑在 reader-view。
 */
export default async function PublicationReaderPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;
  return (
    <Suspense
      fallback={
        <div role="status" className="flex items-center justify-center h-screen">
          Loading…
        </div>
      }
    >
      <PublicationReaderView code={code} />
    </Suspense>
  );
}
