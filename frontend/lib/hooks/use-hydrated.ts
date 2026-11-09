'use client';

import { useSyncExternalStore } from 'react';

const noopSubscribe = () => () => {};

/**
 * 是否已完成 hydration（SSR 首帧与客户端 hydration 渲染均为 false，之后为 true）。
 *
 * 用于「客户端初值依赖 window」的 store 字段：`leftPanelOpen` 由
 * `defaultLeftPanelOpen()` 按视口宽度初始化 —— 服务端恒 true，窄屏客户端为
 * false。直接按它切换图标/文案会让 SSR 与 hydration 首帧分叉（390px 下每次
 * 加载都报 React #418，并整树回退到客户端重渲染）。消费方在未 hydrate 时
 * 先按服务端默认值渲染，hydrate 后再切到真实值。
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}
