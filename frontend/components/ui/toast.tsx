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

const typeStyles: Record<ToastType, { color: string; border: string; icon: React.ReactNode }> = {
  success: {
    color: "text-emerald-400",
    border: "border-emerald-500/30",
    icon: <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />,
  },
  error: {
    color: "text-red-400",
    border: "border-red-500/30",
    icon: <AlertCircle className="h-4 w-4 text-red-400 shrink-0" />,
  },
  info: {
    color: "text-hud-cyan",
    border: "border-hud-cyan/30",
    icon: <Info className="h-4 w-4 text-hud-cyan shrink-0" />,
  },
  warning: {
    color: "text-amber-400",
    border: "border-amber-500/30",
    icon: <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />,
  },
}

/* ── Component ── */

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts)
  const removeToast = useToastStore((s) => s.removeToast)

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col-reverse gap-2 pointer-events-none">
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
              className={`
                pointer-events-auto flex items-center gap-2 px-4 py-3
                rounded-xl shadow-lg backdrop-blur-hud
                bg-ds-surface border ${style.border}
                max-w-sm
              `}
            >
              {style.icon}
              <span className={`text-sm font-mono ${style.color}`}>{toast.message}</span>
              <button
                onClick={() => removeToast(toast.id)}
                className="ml-2 shrink-0 text-white/40 hover:text-white/80 transition-colors"
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
