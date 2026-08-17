import type { CommandEntry } from './types';
import { devOnly } from '@/lib/utils/logger';

/**
 * query_features — 点要素探查（#535：幽灵命令落地）。
 *
 * 后端 `query_map_features`（app/tools/spatial.py）返回
 * `{command: 'query_features', location: [lng, lat], buffer_m, summary}`，但
 * #205-#208 命令迁移时前端目录里从未登记该命令 —— 每次『这是什么』都得到
 * 后端成功 + 前端 unknown_command 失败（prompt.py 还主动推荐该工具，所以是
 * 稳定的一等失败路径）。这里给出真正的实现：把 lng/lat 投影到屏幕像素，围绕
 * 它（buffer_m 折算像素半径）查询已渲染要素，汇总 name/title/label 属性以
 * 系统消息的形式如实汇报。
 */
export const queryCommands: Record<string, CommandEntry> = {
  query_features: {
    requiredParams: (p) => Array.isArray(p.location) && p.location.length === 2,
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const location = params?.location as [number, number] | undefined;
      if (!Array.isArray(location) || location.length !== 2) {
        return { status: 'failed', error: 'invalid_params' };
      }
      const [lng, lat] = location;
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
        return { status: 'failed', error: 'invalid_params' };
      }
      const bufferM =
        typeof params?.buffer_m === 'number' && Number.isFinite(params.buffer_m) && params.buffer_m > 0
          ? params.buffer_m
          : 10;

      try {
        const pixel = map.project(location);
        // 米 → 像素半径的近似换算（当前缩放级别下赤道周长 / 瓦片像素，
        // 按纬度 cos 修正）。只想兜住"这个点附近有什么"，近似足够诚实。
        const zoom = map.getZoom();
        const metersPerPixel =
          (40075016.686 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom + 8);
        const pxRadius = Math.max(1, Math.round(bufferM / Math.max(metersPerPixel, 1e-9)));
        const geometry: [number, number, number, number] = [
          pixel.x - pxRadius,
          pixel.y - pxRadius,
          pixel.x + pxRadius,
          pixel.y + pxRadius,
        ];
        const features: any[] = map.queryRenderedFeatures(geometry);
        const names = features
          .map((f) => f?.properties?.name ?? f?.properties?.title ?? f?.properties?.label)
          .filter((n) => typeof n === 'string' && n.length > 0);
        const unique = Array.from(new Set(names));
        const summary =
          features.length > 0
            ? `在 [${lng.toFixed(6)}, ${lat.toFixed(6)}] 半径 ${bufferM}m 内查询到 ` +
              `${features.length} 个已渲染要素` +
              (unique.length > 0
                ? `：${unique.slice(0, 8).join('、')}${unique.length > 8 ? '…' : ''}`
                : '。')
            : `在 [${lng.toFixed(6)}, ${lat.toFixed(6)}] 半径 ${bufferM}m 内未查询到已渲染要素。`;
        try {
          getHudState().setPendingSystemMessage(`[系统通知] ${summary}`);
        } catch {
          /* defensive: store unavailable during unit render */
        }
        return {
          status: 'succeeded',
          result: { featureCount: features.length, names: unique, summary },
        };
      } catch (e) {
        devOnly.error('[query_features] feature query failed:', e);
        return { status: 'failed', error: 'query_failed' };
      }
    },
  },
};