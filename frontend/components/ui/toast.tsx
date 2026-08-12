"use client"

import { create } from "zustand"
import { AnimatePresence, motion } from "framer-motion"
import { X, CheckCircle2, AlertCircle, Info, AlertTriangle } from "lucide-react"
import React from "react"

/* ── Types ── */

type ToastType = "success" | "error" | "info" | "warning"

interface Toast {
  id: string
  message: string
  type: ToastType
  createdAt: number
}

interface ToastStore {
  toasts: Toast[]
  addToast: (message: string, type?: ToastType, duration?: number) => void
  removeToast: (id: string) => void
}

/* ── Store ── */

const DEDUP_WINDOW_MS = 2000
const DEFAULT_DURATION_MS = 3000

let toastCounter = 0

export const useToastStore = create<ToastStore>((set, get) => ({
  toasts: [],

  addToast: (message, type = "info", duration = DEFAULT_DURATION_MS) => {
    const now = Date.now()
    const isDuplicate = get().toasts.some(
      (t) => t.message === message && now - t.createdAt < DEDUP_WINDOW_MS
    )
    if (isDuplicate) return

    const id = `toast-${++toastCounter}`
    const toast: Toast = { id, message, type, createdAt: now }
    set((state) => ({ toasts: [...state.toasts, toast] }))

    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
      }, duration)
    }
  },

  removeToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }))
  },
}))

/* ── Style maps ── */

// V4: the same four status slots as StatusBadge / InlineNotice, so an operation's
// toast and its badge are never two different greens. `text-hud-cyan` (info) was
// an undefined utility and rendered as inherited colour.
const typeStyles: Record<ToastType, { color: string; border: string; icon: React.ReactNode }> = {
  success: {
    color: "text-status-success",
    border: "border-status-success-border",
    icon: <CheckCircle2 className="h-icon-md w-icon-md shrink-0 text-status-success" aria-hidden />,
  },
  error: {
    color: "text-status-critical",
    border: "border-status-critical-border",
    icon: <AlertCircle className="h-icon-md w-icon-md shrink-0 text-status-critical" aria-hidden />,
  },
  info: {
    color: "text-status-info",
    border: "border-status-info-border",
    icon: <Info className="h-icon-md w-icon-md shrink-0 text-status-info" aria-hidden />,
  },
  warning: {
    color: "text-status-warning",
    border: "border-status-warning-border",
    icon: <AlertTriangle className="h-icon-md w-icon-md shrink-0 text-status-warning" aria-hidden />,
  },
}

/* ── Component ── */

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts)
  const removeToast = useToastStore((s) => s.removeToast)

  return (
    /*
      a11y（P0）：ToastContainer 之前没有任何 live region —— 「模板已应用」、
      「数据源已删除」、连通测试结果等每一次操作反馈对读屏用户都是不存在的。
      role="status" + aria-live="polite" 让它们在用户空闲时被读出，而不是打断。
    */
    <div
      role="status"
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 right-4 z-[9999] flex flex-col-reverse gap-2"
    >
      <AnimatePresence mode="popLayout">
        {toasts.map((toast) => {
          const style = typeStyles[toast.type]
          return (
            <motion.div
              key={toast.id}
              layout
              initial={{ opacity: 0, x: 80, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 80, scale: 0.95 }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
              /* `bg-ds-surface` / `backdrop-blur-hud` were never defined in the
                 Tailwind config, so the toast rendered with no background at all
                 — its text sat straight on the map. Now on the overlay surface. */
              className={`pointer-events-auto flex max-w-sm items-center gap-2 rounded-md border bg-surface-overlay px-3 py-2 shadow-overlay ${style.border}`}
            >
              {style.icon}
              <span className={`font-mono text-meta ${style.color}`}>{toast.message}</span>
              <button
                onClick={() => removeToast(toast.id)}
                className="ml-2 shrink-0 text-ink-muted transition-colors hover:text-ink"
                aria-label="关闭提示"
              >
                <X className="h-icon-sm w-icon-sm" aria-hidden />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
