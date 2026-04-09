"use client"
import { useState, useRef, useEffect } from "react"
import { Send, Paperclip, Bot, User, Loader2, Upload, X, Check, AlertCircle } from "lucide-react"
import { streamChat, SSEEventType } from "@/lib/api/chat"
import { useTask } from "@/lib/contexts/task-context"
import { TaskProgress } from "@/components/chat/task-progress"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ChartRenderer, ChartData } from "@/components/chat/chart-renderer"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
  charts?: ChartData[]
}

interface Attachment {
  id: string
  name: string
  size: number
  type: string
}

// Callback types for parent component communication
interface ChatPanelProps {
  onAnalysisRequest?: (message: string, attachments?: Attachment[]) => void
  incomingMessage?: string
  incomingResponse?: string
  onToolResult?: (toolName: string, result: any) => void
}

export function ChatPanel({ onAnalysisRequest, incomingMessage, incomingResponse, onToolResult }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我是 WebGIS AI 助手，可以帮你进行空间数据分析和制图。请描述你想进行的分析，例如「分析北京市人口分布」或「找出周边5公里内的公园」",
      timestamp: new Date(),
    },
  ])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string>()
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [showAttachments, setShowAttachments] = useState(false)
  const [currentStep, setCurrentStep] = useState<SSEEventType | 'error' | null>(null)
  const messageEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Task context
  const {
    currentTask,
    handleTaskStart,
    handleStepStart,
    handleStepResult,
    handleStepError,
    handleTaskComplete,
    handleTaskError,
    handleTaskCancelled,
    clearTask,
  } = useTask()

  // Reasoning step order and labels
  const stepOrder: SSEEventType[] = ['thinking', 'planning', 'acting', 'observing', 'done']
  const stepLabels: Record<SSEEventType | 'error', { label: string; icon: React.ReactNode }> = {
    thinking: { label: '思考中', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    planning: { label: '规划方案', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    acting: { label: '执行操作', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    observing: { label: '分析结果', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    done: { label: '完成', icon: <Check className="h-3 w-3 text-green-500" /> },
    tool_error: { label: '执行出错', icon: <AlertCircle className="h-3 w-3 text-red-500" /> },
    error: { label: '执行出错', icon: <AlertCircle className="h-3 w-3 text-red-500" /> },
    message: { label: '回复消息', icon: <Check className="h-3 w-3 text-green-500" /> }
  }

  // Handle incoming messages from parent
  useEffect(() => {
    if (incomingMessage) {
      handleSend(incomingMessage, incomingResponse)
    }
  }, [incomingMessage, incomingResponse])

  const scrollToBottom = () => {
    setTimeout(() => {
      messageEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }, 100)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    const newAttachments: Attachment[] = files.map(file => ({
      id: Date.now().toString() + Math.random(),
      name: file.name,
      size: file.size,
      type: file.type,
    }))
    setAttachments(prev => [...prev, ...newAttachments])
    setShowAttachments(true)
  }

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  const handleSend = async (msg?: string, customResponse?: string) => {
    const messageText = msg || input.trim()
    if (!messageText || isLoading) return

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: messageText,
      timestamp: new Date(),
      ...(msg && { isThinking: !!customResponse }),
    }

    setMessages(prev => [...prev, userMessage])
    if (!msg) {
      setInput("")
      setAttachments([])
    }
    
    if (customResponse) {
      // Pre-set response (for demo/testing)
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: customResponse,
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, assistantMessage])
      onAnalysisRequest?.(messageText, attachments)
      scrollToBottom()
      return
    }

    setIsLoading(true)

    // Add thinking indicator
    const thinkingMessage: Message = {
      id: (Date.now() + 1).toString(),
      role: "assistant",
      content: "",
      timestamp: new Date(),
      isThinking: true,
    }
    setMessages(prev => [...prev, thinkingMessage])
    scrollToBottom()

    try {
      let assistantContent = ""
      setCurrentStep('thinking')

      for await (const event of streamChat(messageText, sessionId)) {
        const { event: eventType, data } = event

        // Update current step for reasoning events
        if (stepOrder.includes(eventType as SSEEventType) || eventType === 'tool_error') {
          setCurrentStep(eventType)
        }

        if (eventType === "session" && data?.session_id) {
          setSessionId(data.session_id)
        } else if (eventType === "task_start" && data?.task_id) {
          handleTaskStart(data.task_id)
        } else if (eventType === "step_start" && data?.task_id) {
          handleStepStart(data.task_id, data.step_id, data.step_index, data.tool)
        } else if (eventType === "step_result" && data?.task_id) {
          handleStepResult(data.task_id, data.step_id, data.tool, data.result, data.has_geojson)
          if (data.has_geojson && onToolResult) {
            onToolResult(data.tool, data.result)
          }
          // 检测图表数据
          if (data.result?.chart) {
            setMessages(prev => prev.map(msg =>
              msg.id === thinkingMessage.id
                ? { ...msg, charts: [...(msg.charts || []), data.result.chart as ChartData], isThinking: false }
                : msg
            ))
          }
        } else if (eventType === "step_error" && data?.task_id) {
          handleStepError(data.task_id, data.step_id, data.error)
        } else if (eventType === "task_complete" && data?.task_id) {
          handleTaskComplete(data.task_id, data.step_count, data.summary)
        } else if (eventType === "task_error" && data?.task_id) {
          handleTaskError(data.task_id, data.error)
        } else if (eventType === "task_cancelled" && data?.task_id) {
          handleTaskCancelled(data.task_id)
        } else if (eventType === "tool_call") {
          const toolName = typeof data === "object" ? data.name || data.tool : String(data)
          assistantContent += `\n🔧 *正在调用 ${toolName}...*\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "tool_result") {
          const toolName = typeof data === "object" ? data.name || "unknown" : "unknown"
          const toolResult = typeof data === "object" ? (data.result || data) : data
          console.log("[ChatPanel] tool_result:", toolName, "hasGeojson:", !!toolResult?.geojson, "hasChart:", !!toolResult?.chart, "features:", toolResult?.geojson?.features?.length)

          // 通知父组件渲染 GeoJSON
          if (onToolResult) {
            onToolResult(toolName, toolResult)
          }

          // 检测图表数据
          if (toolResult?.chart) {
            setMessages(prev => prev.map(msg =>
              msg.id === thinkingMessage.id
                ? { ...msg, charts: [...(msg.charts || []), toolResult.chart as ChartData], isThinking: false }
                : msg
            ))
          }

          // 简化显示
          let summary = ""
          if (toolResult?.chart) {
            summary = `图表已生成: ${toolResult.chart.title}`
          } else if (toolResult?.count !== undefined) {
            summary = `找到 ${toolResult.count} 个结果`
          } else if (toolResult?.stats) {
            summary = `统计完成`
          } else if (toolResult?.error) {
            summary = `错误: ${toolResult.error}`
          } else if (toolResult?.status === "ok") {
            summary = `数据获取成功`
          } else {
            summary = "完成"
          }

          assistantContent += `\n✅ **${toolName}**: ${summary}\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "message" || eventType === "content" || eventType === "token") {
          const chunk = typeof data === "object" ? (data.content || data.text || data.message || "") : String(data)
          assistantContent += chunk
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "done" || eventType === "end") {
          setCurrentStep('done')
          break
        } else if (eventType === "tool_error") {
          const errorMsg = typeof data === "object" ? (data.message || data.error || "未知错误") : String(data)
          assistantContent += `\n❌ **错误**: ${errorMsg}\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        }
      }

      // Ensure we un-set thinking state
      setMessages(prev => prev.map(msg =>
        msg.id === thinkingMessage.id
          ? { ...msg, isThinking: false }
          : msg
      ))

      // Notify parent component
      onAnalysisRequest?.(messageText, attachments)

    } catch (error) {
      setCurrentStep('error')
      setMessages(prev => prev.map(msg =>
        msg.id === thinkingMessage.id
          ? { ...msg, content: "抱歉，请求失败了，请重试", isThinking: false }
          : msg
      ))
    } finally {
      // Keep done/error state visible for 1 second before clearing
      setTimeout(() => {
        setCurrentStep(null)
        clearTask()
      }, 1000)
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex h-full flex-col glass border-r border-cyan-500/20">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-cyan-500/20 p-4 bg-cyan-950/50">
        <Bot className="h-5 w-5 text-cyan-400 animate-pulse" />
        <h1 className="font-semibold text-white">AI 对话</h1>
      </div>

      {/* Task Progress Card (inline, replaces old reasoning bar) */}
      {currentTask && (
        <div className="border-b border-cyan-500/20 px-4 py-2 bg-cyan-950/30">
          <TaskProgress task={currentTask} />
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex gap-3 ${
              message.role === "user" ? "flex-row-reverse" : ""
            }`}
          >
            <div
              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                message.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted"
              }`}
            >
              {message.role === "user" ? (
                <User className="h-4 w-4" />
              ) : (
                <Bot className="h-4 w-4" />
              )}
            </div>
            <div
              className={`max-w-[85%] rounded-lg p-3 text-sm ${
                message.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted"
              }`}
            >
              {message.isThinking ? (
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-xs">思考中...</span>
                </div>
              ) : (
                <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:my-2 prose-pre:my-1 prose-code:text-xs break-words">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {message.content}
                  </ReactMarkdown>
                  {message.charts?.map((chart, idx) => (
                    <ChartRenderer key={`chart-${message.id}-${idx}`} chart={chart} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messageEndRef} />
      </div>

      {/* Attachments Preview */}
      {attachments.length > 0 && (
        <div className="px-4 py-2 border-t border-border flex flex-wrap gap-2">
          {attachments.map((att) => (
            <div key={att.id} className="flex items-center gap-1 bg-muted rounded px-2 py-1 text-xs">
              <Upload className="h-3 w-3" />
              <span className="max-w-24 truncate">{att.name}</span>
              <button onClick={() => removeAttachment(att.id)} className="hover:text-destructive">
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="border-t border-border p-4">
        <div className="flex gap-2">
          <label className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors">
            <input type="file" multiple className="hidden" onChange={handleFileSelect} />
            <Paperclip className="h-4 w-4" />
          </label>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入分析指令..."
            disabled={isLoading}
            className="flex-1 rounded-lg border border-border bg-muted px-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isLoading}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
