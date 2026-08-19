"use client"

import React, { Component, useEffect } from "react"
import { MotionConfig } from "framer-motion"
import { MapProvider } from "react-map-gl/maplibre"
import { MapActionProvider } from "@/lib/contexts/map-action-context"
import { ToastContainer } from "@/components/ui/toast"
import { SystemMessageBridge } from "@/components/providers/system-message-bridge"
import { enableHudPersistWrites, useHudStore } from "@/lib/store/useHudStore"

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

class ErrorBoundary extends Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      // 全屏错误页走语义 token（bg-surface-canvas / text-ink-* / status-*）：
      // 硬编码浅色在暗色主题下会渲染成一块白屏。
      return (
        <div className="h-screen w-screen bg-surface-canvas flex items-center justify-center text-ink">
          <div className="text-center space-y-4">
            <div className="text-status-accent text-sm font-mono uppercase tracking-widest">System Error</div>
            <p className="text-ink-muted text-sm max-w-md">
              {this.state.error?.message || "An unexpected error occurred"}
            </p>
            <button
              onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload() }}
              className="px-4 py-2 rounded-md border border-status-accent-border text-status-accent text-sm hover:bg-status-accent-soft transition-colors"
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export function ClientProviders({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    void Promise.resolve(useHudStore.persist.rehydrate()).finally(() => {
      enableHudPersistWrites();
    });
  }, []);
  return (
    // MotionConfig reducedMotion="user"：globals.css 的 reduced-motion 全局样式
    // 只能关掉 CSS 动画，触不到 framer-motion 用 JS 内联样式驱动的弹簧动画；
    // 这里让 framer-motion 自行读取 prefers-reduced-motion 并降级。
    <MotionConfig reducedMotion="user">
      <ErrorBoundary>
        <MapProvider>
          <MapActionProvider>
            {children}
            <SystemMessageBridge />  {/* FE-01: 消费 pendingSystemMessage 队列 */}
            <ToastContainer />
          </MapActionProvider>
        </MapProvider>
      </ErrorBoundary>
    </MotionConfig>
  )
}
