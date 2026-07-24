"use client"

import { useEffect, useRef } from "react"
import { useHudStore } from "@/lib/store/useHudStore"
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
  const drainSystemMessage = useHudStore((s) => s.drainSystemMessage)
  const addToast = useToastStore((s) => s.addToast)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (pendingMsg) {
      // 判断消息类型：含"失败"/"错误" = error，含"下载" = info，其余 = success
      const isError = /失败|错误|error/i.test(pendingMsg)
      addToast(pendingMsg, isError ? "error" : "info")

      // 500ms 后推进队列（给 toast 渲染时间）
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => {
        drainSystemMessage()
      }, 500)
    }

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [pendingMsg, drainSystemMessage, addToast])

  return null
}
