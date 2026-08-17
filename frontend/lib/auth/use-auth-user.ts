'use client';

import { useSyncExternalStore } from 'react';
import { getAuthUser, subscribeAuth } from './tokenStore';

/**
 * 响应式登录态 —— 登录/登出即时刷新依赖它的按钮门控（#469 导出、
 * #528 项目 tab / 恢复操作）。useSyncExternalStore 订阅 tokenStore：
 * getAuthUser 读当前用户，subscribeAuth 挂监听；SSR/isBrowser 缺失时
 * 快照为 null（匿名）。
 */
export function useAuthUser() {
  return useSyncExternalStore(
    subscribeAuth,
    getAuthUser,
    () => null,
  );
}