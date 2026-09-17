'use client';
// ADR-0198：路由壳（Next 限制 page 的导出面）；面板实现在 components/geoai。
import { GeoAiPanel } from '@/components/geoai/geoai-panel';

export default function GeoAiPage() {
  return <GeoAiPanel />;
}
