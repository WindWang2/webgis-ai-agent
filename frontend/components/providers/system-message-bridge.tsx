"use client"

import { useEffect, useRef } from "react"
import { useHudStore } from "@/lib/store/useHudStore"
import type { SystemMessage } from "@/lib/store/hud-types"
import { useToastStore } from "@/components/ui/toast"

/**
 * 审计 FE-01：pendingSystemMessage 队列之前被写入 6+ 处（map-action-handler
 * 的导出成功/失败、AI 命令失败等）但**无消费者**，所有通知静默丢失。
 *
 * 此组件挂在 ClientProviders 内，监听 pendingSystemMessage 变化，
 * 通过 toast 显示，然后调 drainSystemMessage() 推进队列。
 */
export function SystemMessageBridge() {
  const pendingMsg = useHudStore((s) => s.pendingSystemMessage)
  // drainSystemMessage() 重新激活队首「同值字符串」时 pendingSystemMessage
  // 引用不变（uiSlice 只返回 { pendingSystemMessage: next }），zustand v5 的
  // useSyncExternalStore 以 Object.is 比较选中的值 → 跳过渲染 → effect 不重跑
  // → 该条重复消息永不 toast，且 pending 永久停在旧值上，后续消息只入队无人
  // drain。队列长度同步入选：drain 激活队首时队列长度必然下降，据此把「重新
  // 激活」与「纯入队」区分开。
  const queueLen = useHudStore((s) => s._systemMessageQueue.length)
  const drainSystemMessage = useHudStore((s) => s.drainSystemMessage)
  const addToast = useToastStore((s) => s.addToast)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 上一次 effect 运行时的 (pending, 队列长度)，用于区分「新激活」与「纯入队」
  const lastRef = useRef<{ msg: SystemMessage | null; qlen: number } | null>(null)

  // 只在卸载时清定时器：effect 因「纯入队」重跑时不能清掉待执行的 drain，
  // 否则队列推进会静默停止。
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  useEffect(() => {
    const prev = lastRef.current
    lastRef.current = { msg: pendingMsg, qlen: queueLen }
    if (!pendingMsg) return
    // 纯入队（pending 未变且队列变长）不是新激活，不重复 toast
    if (prev && prev.msg === pendingMsg && queueLen >= prev.qlen) return

    const text = typeof pendingMsg === "string" ? pendingMsg : pendingMsg.text
    const level = typeof pendingMsg === "string" ? "info" : pendingMsg.level
    addToast(text, level)

    // 500ms 后推进队列（给 toast 渲染时间）
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      drainSystemMessage()
    }, 500)
  }, [pendingMsg, queueLen, drainSystemMessage, addToast])

  return null
}
